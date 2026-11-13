"""Inbound enterprise inquiries (the Transition Package page's form).

Defences: server-side validation, a per-session CSRF token, a honeypot field
and a per-IP rate limit counted in the database (Passenger runs several
processes, so an in-memory counter would undercount). The submitted details
are emailed only to the admin-set notification address. The analytics event
carries no personal data.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import re
import secrets
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

HEADCOUNT_BANDS = ('Under 25', '25–100', '100–500', '500+')
TIMINGS = ('Within 30 days', '1–3 months', 'Planning ahead')
MODES = ('design_partner', 'general')
DEFAULTS = {'transition_package_mode': 'design_partner',
            'enterprise_inquiry_notify_email': 'simi@simisi.io',
            'enterprise_inquiry_rate_limit': 5}           # per IP per hour
HONEYPOT_FIELD = 'website'
CSRF_SESSION_KEY = 'enterprise_inquiry_csrf'
_EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$')


class InquiryRejected(Exception):
    """Validation failure: a user-facing message, nothing stored."""


def setting(key: str):
    default = DEFAULTS[key]
    try:
        from app.models.platform_settings import PlatformSetting
        raw = PlatformSetting.get(key, None)
    except Exception:
        return default
    if raw in (None, ''):
        return default
    if isinstance(default, int):
        try:
            return max(1, int(raw))
        except (TypeError, ValueError):
            return default
    return raw


def page_mode() -> str:
    m = str(setting('transition_package_mode')).strip().lower()
    return m if m in MODES else 'design_partner'


def csrf_token(session) -> str:
    tok = session.get(CSRF_SESSION_KEY)
    if not tok:
        tok = secrets.token_urlsafe(32)
        session[CSRF_SESSION_KEY] = tok
    return tok


def csrf_ok(session, submitted: str) -> bool:
    tok = session.get(CSRF_SESSION_KEY)
    return bool(tok and submitted and hmac.compare_digest(str(tok), str(submitted)))


def ip_hash(ip: str) -> str:
    return hashlib.sha256(('enterprise-inquiry|' + (ip or '')).encode('utf-8')).hexdigest()


def rate_limited(ip: str) -> bool:
    from app.models.enterprise_inquiry import EnterpriseInquiry
    since = datetime.utcnow() - timedelta(hours=1)
    n = EnterpriseInquiry.query.filter(EnterpriseInquiry.ip_hash == ip_hash(ip),
                                       EnterpriseInquiry.created_at >= since).count()
    return n >= setting('enterprise_inquiry_rate_limit')


def _clean(v, n):
    return re.sub(r'\s+', ' ', str(v or '')).strip()[:n]


def validate(form: dict) -> dict:
    data = {
        'name': _clean(form.get('name'), 160),
        'work_email': _clean(form.get('email') or form.get('work_email'), 255).lower(),
        'company': _clean(form.get('company'), 200),
        'role': _clean(form.get('role'), 160) or None,
        'headcount_band': _clean(form.get('headcount'), 20) or None,
        'timing': _clean(form.get('timing'), 30) or None,
        'notes': (str(form.get('notes') or '').strip()[:4000]) or None,
        'source': _clean(form.get('source') or 'transition-package', 40),
    }
    if len(data['name']) < 2:
        raise InquiryRejected('Please enter your name.')
    if not _EMAIL_RE.match(data['work_email']):
        raise InquiryRejected('Please enter a valid work email.')
    if len(data['company']) < 2:
        raise InquiryRejected('Please enter your company.')
    if data['headcount_band'] and data['headcount_band'] not in HEADCOUNT_BANDS:
        raise InquiryRejected('Please choose how many people are affected.')
    if data['timing'] and data['timing'] not in TIMINGS:
        raise InquiryRejected('Please choose a timing.')
    if not re.match(r'^[a-z0-9\-]{1,40}$', data['source']):
        data['source'] = 'transition-package'
    return data


def submit(form: dict, session, ip: str) -> dict:
    """Returns {'ok': True} (also for a tripped honeypot, which stores nothing
    and sends nothing), or raises InquiryRejected."""
    from app.extensions import db
    from app.models.enterprise_inquiry import EnterpriseInquiry
    if not csrf_ok(session, form.get('csrf_token')):
        raise InquiryRejected('Your session expired. Reload the page and try again.')
    if str(form.get(HONEYPOT_FIELD) or '').strip():
        logger.info('enterprise inquiry honeypot tripped; dropped')
        return {'ok': True, 'dropped': True}
    if rate_limited(ip):
        raise InquiryRejected('Too many requests from your network. Please try again later or email '
                              'simi@simisi.io.')
    data = validate(form)
    mode = page_mode()
    inq = EnterpriseInquiry(mode=mode, ip_hash=ip_hash(ip), **data)
    db.session.add(inq)
    db.session.commit()
    _notify(inq)
    try:
        from app.models.user_event import UserEvent
        UserEvent.track(None, 'transition_package_inquiry', {'source': inq.source, 'mode': mode})
    except Exception as exc:
        logger.warning('inquiry analytics event failed: %s', exc)
    return {'ok': True, 'id': inq.id}


def _notify(inq) -> None:
    try:
        from app.services.email_service import send_enterprise_inquiry_email
        send_enterprise_inquiry_email(setting('enterprise_inquiry_notify_email'), inq)
    except Exception as exc:
        logger.error('enterprise inquiry notification failed for %s: %s', inq.id, exc)


def set_status(inquiry_id: str, status: str, admin_id: str):
    from app.extensions import db
    from app.models.audit_log import AuditLog
    from app.models.enterprise_inquiry import EnterpriseInquiry
    inq = EnterpriseInquiry.query.get(inquiry_id)
    if inq is None:
        raise InquiryRejected('Inquiry not found.')
    if status not in EnterpriseInquiry.STATUSES:
        raise InquiryRejected('Unknown status.')
    inq.status, inq.status_changed_at = status, datetime.utcnow()
    AuditLog.log('enterprise_inquiry_status', user_id=admin_id, resource_id=inq.id, metadata={'status': status})
    db.session.commit()
    return inq


def promote_to_prospect(inquiry_id: str, admin_id: str):
    """Put a qualified inquiry into the outbound CRM (admin_prospects)."""
    from app.extensions import db
    from app.models.audit_log import AuditLog
    from app.models.enterprise_inquiry import EnterpriseInquiry
    from app.services import admin_crm_service
    inq = EnterpriseInquiry.query.get(inquiry_id)
    if inq is None:
        raise InquiryRejected('Inquiry not found.')
    if inq.prospect_id:
        return inq
    notes = 'Inbound inquiry from /%s (%s). Role: %s. People affected: %s. Timing: %s.\n%s' % (
        inq.source, inq.created_at.date().isoformat(), inq.role or '—', inq.headcount_band or '—',
        inq.timing or '—', inq.notes or '')
    p = admin_crm_service.create_prospect({'firm_name': inq.company, 'lead_name': '%s <%s>' % (inq.name, inq.work_email),
                                           'contact_path': 'form', 'category': 'transition', 'fit': 'high',
                                           'notes': notes})
    inq.prospect_id = p.id
    if inq.status == EnterpriseInquiry.STATUS_NEW:
        inq.status, inq.status_changed_at = EnterpriseInquiry.STATUS_CONTACTED, datetime.utcnow()
    AuditLog.log('enterprise_inquiry_promoted', user_id=admin_id, resource_id=inq.id, metadata={'prospect_id': p.id})
    db.session.commit()
    return inq
