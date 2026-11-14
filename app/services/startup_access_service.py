"""Investor access to a startup page (FOUNDER_PLAN.md WP8 part A, PRD "Startup
bio page": access levels and investor access payment). Phase 3: everything here
sits behind the founder_investor switch and the counsel sign-off.

    gate_open(page=None)               investor routes 404 unless this is True
    can_manage(venture, user)          the owner or the holder of 'investor_contact'
    issue_link / revoke_link / links   founder-issued private links (random stored token)
    resolve_link(token)                counts opens; refuses revoked or unknown tokens
    send_signin(email, page)           emails a signed sign-in link (salt investor-signin, short life)
    verify_signin(token, page)         -> email, stored in the Flask session key '_investors'
                                       (no login_user, no User row)
    has_access(page, email)            active investor_access row, or an unrevoked link for that email
    request_access / approve_access / deny_access
    create_checkout(page, email)       Stripe Checkout, price fnd_investor_access_cents
    fulfil(session)                    idempotent; status depends on the venture's exemption
    refund_if_unpublished(page)        refunds paid access inside fnd_refund_window_days
    mark_refunded(pi, fully_refunded)  the charge.refunded webhook
    investor_context(page, access)     the investor view (startup_page_service plus full proof)

Rules (PRD, FOUNDER_PLAN 1.5): invited investors never pay. A paying investor
who was not invited, under 506(b) or before an exemption is chosen, sees the
ask only after the founders approve; under 506(c) access is active at once.
Payment never verifies accreditation and is never tied to an investment.
"""
from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

SESSION_KEY = '_investors'            # verified investor emails in this browser
SESSION_LINKS_KEY = '_investor_links'  # private-link ids opened in this browser
SIGNIN_SALT = 'investor-signin'
SIGNIN_MAX_AGE = 30 * 60               # seconds
SIGNIN_LIMIT_DEFAULT = 3               # sign-in emails per address and page per hour
SESSION_MAX_EMAILS = 5
LABEL_MAX, MESSAGE_MAX = 200, 1000
CARD_MINIMUM_CENTS = 50
OPEN_SESSION_REUSE = timedelta(hours=23)

WHAT_UNLOCKS = (
    ('The ask', 'the round, the amount and the use of funds, as the founders state them.'),
    ('Full proof', 'every measured result from the proof of concept and every customer quote shared with '
                   'permission.'),
    ('Traction detail', 'the notes and dates behind each metric the founders show.'),
    ('Data room request', 'ask the founders directly for their data room.'),
    ('The investor one-pager', 'a dated PDF of the page, including the ask.'),
)


class AccessError(ValueError):
    """A refusal with a message safe to show. status is the HTTP code to use."""

    def __init__(self, message, status: int = 400):
        super().__init__(message)
        self.status = status


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now():
    return datetime.utcnow()


def _g(obj, key, default=None):
    """A field from a Stripe object or a plain dict."""
    if obj is None:
        return default
    try:
        v = obj.get(key, default)
    except AttributeError:
        v = getattr(obj, key, default)
    return default if v is None else v


def _venture(page):
    from app.extensions import db
    from app.models.founder import FounderVenture
    return db.session.get(FounderVenture, page.venture_id)


def _page(page_id):
    from app.extensions import db
    from app.models.founder import StartupPage
    return db.session.get(StartupPage, page_id) if page_id else None


def _cfg(key, default):
    try:
        from app.services import founder_config
        v = founder_config.get(key)
        return default if v is None or v == '' else v
    except Exception:
        return default


def price_cents() -> int:
    try:
        return int(_cfg('fnd_investor_access_cents', 4900))
    except (TypeError, ValueError):
        return 4900


def refund_window_days() -> int:
    try:
        return int(_cfg('fnd_refund_window_days', 14))
    except (TypeError, ValueError):
        return 14


def paid_access_available() -> bool:
    return price_cents() >= CARD_MINIMUM_CENTS


def norm_email(raw) -> str:
    """A validated, lower-cased address. Raises AccessError."""
    from utils.validators import ValidationError, validate_email
    try:
        return validate_email(raw)
    except ValidationError as exc:
        raise AccessError(str(exc))


def _email_hash(email: str) -> str:
    return hashlib.sha256((email or '').strip().lower().encode()).hexdigest()[:32]


def _log(page, kind, note='', user_id=None):
    from app.services import founder_ventures
    founder_ventures.log_event(page.venture_id, 'investor_' + kind, note, user_id=user_id)


def _stream(page, event, data=None):
    try:
        from app.services import founder_stream
        founder_stream.publish(page.venture_id, event, dict({'actor_kind': 'system'}, **(data or {})), commit=False)
    except Exception as exc:
        logger.warning('investor access stream event %s failed: %s', event, exc)


def exemption(venture) -> str:
    """'506b' | '506c' | 'undecided' (missing or unreadable counts as undecided)."""
    try:
        from app.services import founder_compliance
        ex = founder_compliance.exemption_for(venture)
    except (ImportError, AttributeError):
        from app.models.founder import ComplianceProfile
        row = ComplianceProfile.query.filter_by(venture_id=venture.id).first()
        ex = row.exemption if row is not None else None
    except Exception as exc:
        logger.warning('exemption lookup failed for %s: %s', getattr(venture, 'id', '?'), exc)
        ex = None
    return ex if ex in ('506b', '506c') else 'undecided'


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------

def gate_open(page=None) -> bool:
    """Investor routes exist only while the startup pages are on, the
    founder_investor switch is not off and counsel sign-off is recorded. Under
    'allowlist', only pages whose owner is allowed for investor connection."""
    try:
        from app.services import founder_config, founder_feature
        if not founder_feature.public_pages_enabled():
            return False
        m = founder_feature.mode(founder_feature.INVESTOR)
        if m == 'off' or not founder_config.counsel_signed_off():
            return False
        if page is not None and m == 'allowlist':
            from app.extensions import db
            from app.models.user import User
            v = _venture(page)
            owner = db.session.get(User, v.owner_user_id) if v is not None else None
            return bool(owner is not None and founder_feature.is_enabled(owner, founder_feature.INVESTOR))
        return True
    except Exception as exc:
        logger.error('investor gate failed closed: %s', exc)
        return False


# ---------------------------------------------------------------------------
# Who decides for the founders
# ---------------------------------------------------------------------------

def investor_contact_holder(venture):
    """The active member holding the venture-wide 'investor_contact' right
    (owner if none is recorded or the holder left)."""
    from app.models.founder import DecisionRight, VentureMember
    rows = DecisionRight.query.filter_by(venture_id=venture.id, decision_type='investor_contact').all()
    for r in [r for r in rows if r.layer is None] + [r for r in rows if r.layer is not None]:
        m = VentureMember.query.filter_by(id=r.holder_member_id, venture_id=venture.id, status='active').first()
        if m is not None and m.user_id:
            return m
    return VentureMember.query.filter_by(venture_id=venture.id, role='owner', status='active').first()


def can_manage(venture, user) -> bool:
    """The owner, or the co-founder holding the investor_contact decision right."""
    uid = getattr(user, 'id', None)
    if venture is None or not uid:
        return False
    from app.services import founder_ventures
    if founder_ventures.get_member(venture, uid) is None:
        return False
    if venture.owner_user_id == uid:
        return True
    from app.models.founder import DecisionRight, VentureMember
    for r in DecisionRight.query.filter_by(venture_id=venture.id, decision_type='investor_contact').all():
        m = VentureMember.query.filter_by(id=r.holder_member_id, venture_id=venture.id, status='active').first()
        if m is not None and m.user_id == uid:
            return True
    return False


def _require_manage(venture, user):
    if not can_manage(venture, user):
        raise AccessError('Only the founder who decides on investor contact can do that.', 403)


def _managers(venture) -> list:
    """Users to tell about investor access: the owner and the right holder."""
    from app.extensions import db
    from app.models.user import User
    ids = [venture.owner_user_id]
    h = investor_contact_holder(venture)
    if h is not None and h.user_id and h.user_id not in ids:
        ids.append(h.user_id)
    out = []
    for uid in ids:
        u = db.session.get(User, uid)
        if u is not None and not getattr(u, 'deleted_at', None) and u.email:
            out.append(u)
    return out


# ---------------------------------------------------------------------------
# Private links
# ---------------------------------------------------------------------------

def link_url(page, link) -> str:
    return '%s/startup/%s/i/%s' % (_base_url(), page.slug, link.token)


def issue_link(page, user, label, investor_email=None, commit: bool = True):
    """A private link to the investor view. Owner or investor_contact holder only."""
    from app.extensions import db
    from app.models.founder import InvestorLink
    venture = _venture(page)
    _require_manage(venture, user)
    label = ' '.join(str(label or '').split())[:LABEL_MAX]
    email = norm_email(investor_email) if (investor_email or '').strip() else None
    if not label:
        label = email or 'Private link'
    link = InvestorLink(venture_id=venture.id, startup_page_id=page.id, label=label,
                        token=secrets.token_urlsafe(32), investor_email=email, created_by=user.id,
                        open_count=0, created_at=_now())
    db.session.add(link)
    db.session.flush()
    _log(page, 'link_issued', '%s (%s)' % (label, link.id), user.id)
    if commit:
        db.session.commit()
    return link


def revoke_link(link, user, commit: bool = True):
    from app.extensions import db
    page = _page(link.startup_page_id)
    _require_manage(_venture(page), user)
    if link.revoked_at is None:
        link.revoked_at = _now()
        _log(page, 'link_revoked', '%s (%s)' % (link.label, link.id), user.id)
    if commit:
        db.session.commit()
    return link


def links(page) -> list:
    from app.models.founder import InvestorLink
    return (InvestorLink.query.filter_by(startup_page_id=page.id)
            .order_by(InvestorLink.created_at.desc()).all())


def link_dict(page, link) -> dict:
    iso = (lambda d: d.isoformat() + 'Z' if d else None)
    return {'id': link.id, 'label': link.label, 'investor_email': link.investor_email,
            'url': link_url(page, link), 'revoked': link.revoked_at is not None, 'revoked_at': iso(link.revoked_at),
            'opened_at': iso(link.opened_at), 'open_count': link.open_count or 0, 'created_at': iso(link.created_at)}


def resolve_link(token, count: bool = True):
    """The unrevoked link for this token, or None. Counts the open."""
    from app.extensions import db
    from app.models.founder import InvestorLink
    token = (token or '').strip()
    if not token or len(token) > 64:
        return None
    link = InvestorLink.query.filter_by(token=token).first()
    if link is None or link.revoked_at is not None:
        return None
    if count:
        link.open_count = (link.open_count or 0) + 1
        if link.opened_at is None:
            link.opened_at = _now()
        db.session.commit()
    return link


def _link_ok(link_id, page) -> bool:
    if not link_id:
        return False
    from app.extensions import db
    from app.models.founder import InvestorLink
    link = db.session.get(InvestorLink, link_id)
    return bool(link is not None and link.revoked_at is None and link.startup_page_id == page.id)


# ---------------------------------------------------------------------------
# Session identity (no account, no login_user)
# ---------------------------------------------------------------------------

def session_emails() -> list:
    from flask import session
    v = session.get(SESSION_KEY) or []
    return [e for e in v if isinstance(e, str)][:SESSION_MAX_EMAILS]


def remember_email(email: str) -> None:
    from flask import session
    emails = [e for e in session_emails() if e != email]
    session[SESSION_KEY] = ([email] + emails)[:SESSION_MAX_EMAILS]
    session.modified = True


def remember_link(link) -> None:
    from flask import session
    ids = [i for i in (session.get(SESSION_LINKS_KEY) or []) if isinstance(i, str) and i != link.id]
    session[SESSION_LINKS_KEY] = ([link.id] + ids)[:20]
    session.modified = True


def session_link_for(page):
    """An unrevoked link for this page opened in this browser, or None."""
    from flask import session
    from app.extensions import db
    from app.models.founder import InvestorLink
    for lid in session.get(SESSION_LINKS_KEY) or []:
        link = db.session.get(InvestorLink, lid) if isinstance(lid, str) else None
        if link is not None and link.revoked_at is None and link.startup_page_id == page.id:
            return link
    return None


def forget_session() -> None:
    from flask import session
    session.pop(SESSION_KEY, None)
    session.pop(SESSION_LINKS_KEY, None)


# ---------------------------------------------------------------------------
# Email sign-in
# ---------------------------------------------------------------------------

def _serializer():
    from flask import current_app
    from itsdangerous import URLSafeTimedSerializer
    return URLSafeTimedSerializer(current_app.config['SECRET_KEY'], salt=SIGNIN_SALT)


def _base_url() -> str:
    try:
        from flask import request
        return request.url_root.rstrip('/')
    except Exception:
        from flask import current_app
        return (current_app.config.get('APP_BASE_URL') or 'https://simisi.io').rstrip('/')


def signin_limit() -> int:
    try:
        return int(_cfg('fnd_investor_signin_limit', SIGNIN_LIMIT_DEFAULT))
    except (TypeError, ValueError):
        return SIGNIN_LIMIT_DEFAULT


def signin_rate_limited(email: str, page) -> bool:
    from app.models.audit_log import AuditLog
    since = _now() - timedelta(hours=1)
    n = (AuditLog.query.filter(AuditLog.action == 'investor_signin_sent', AuditLog.resource_id == page.id,
                               AuditLog.created_at >= since,
                               AuditLog._metadata.like('%%"email_hash": "%s"%%' % _email_hash(email))).count())
    return n >= signin_limit()


def make_signin_token(email: str, page, link=None, nxt: str = '') -> str:
    return _serializer().dumps({'e': email, 'p': page.id, 'l': getattr(link, 'id', None) or None,
                                'n': nxt if nxt in ('view', 'unlock', 'request') else ''})


def load_signin(token, page=None):
    """The token payload ({'e', 'p', 'l', 'n'}) or None when bad, expired or for another page."""
    if not token or len(token) > 600:
        return None
    try:
        data = _serializer().loads(token, max_age=SIGNIN_MAX_AGE)
    except Exception:
        return None
    if not isinstance(data, dict) or not isinstance(data.get('e'), str) or not data.get('p'):
        return None
    if page is not None and data['p'] != page.id:
        return None
    return data


def send_signin(email, page, link=None, nxt: str = '') -> bool:
    """Email a sign-in link for this page. Raises AccessError for a bad address
    (400) or too many links for this address and page in the last hour (429)."""
    from app.extensions import db
    from app.models.audit_log import AuditLog
    email = norm_email(email)
    if signin_rate_limited(email, page):
        raise AccessError('We already sent several sign-in links to this address. Use the newest one, or try '
                          'again in an hour.', 429)
    url = '%s/startup/%s/signin/%s' % (_base_url(), page.slug, make_signin_token(email, page, link, nxt))
    AuditLog.log('investor_signin_sent', resource_id=page.id,
                 metadata={'email_hash': _email_hash(email), 'venture_id': page.venture_id})
    db.session.commit()
    mins = SIGNIN_MAX_AGE // 60
    subject = 'Your sign-in link for %s' % page.name
    text = ('Use this link to open the investor view of the %s startup page on SimiSI:\n\n%s\n\nThe link works '
            'for %d minutes in the browser where you open it. If you did not ask for it, ignore this email.'
            % (page.name, url, mins))
    _send(email, subject, text, url=url, button='Open the investor view', account_holder=False)
    return True


def verify_signin(token, page=None):
    """The verified email (also remembered in the session), or None."""
    data = load_signin(token, page)
    if data is None:
        return None
    try:
        email = norm_email(data['e'])
    except AccessError:
        return None
    remember_email(email)
    return email


# ---------------------------------------------------------------------------
# Access
# ---------------------------------------------------------------------------

def _rows(page, email):
    from app.models.founder import InvestorAccess
    return (InvestorAccess.query.filter_by(startup_page_id=page.id, email=(email or '').lower())
            .order_by(InvestorAccess.created_at.desc()).all())


def _row_active(row, page) -> bool:
    if row.status != 'active':
        return False
    if row.kind == 'invited' and row.investor_link_id:
        return _link_ok(row.investor_link_id, page)
    return True


def access_for(page, email):
    """The row that decides what this email sees: an active row first, then a
    paid one awaiting approval, then a pending request, else None."""
    if not email:
        return None
    rows = _rows(page, email)
    for r in rows:
        if _row_active(r, page):
            return r
    for r in rows:
        if r.status == 'requested' and r.kind == 'paid':
            return r
    for r in rows:
        if r.status == 'requested':
            return r
    return None


def has_access(page, email) -> bool:
    """Active access (invited through an unrevoked link, paid and approved, or
    an approved request), or an unrevoked named link for this email."""
    if not email:
        return False
    email = email.lower()
    if any(_row_active(r, page) for r in _rows(page, email)):
        return True
    from app.models.founder import InvestorLink
    return InvestorLink.query.filter(InvestorLink.startup_page_id == page.id, InvestorLink.investor_email == email,
                                     InvestorLink.revoked_at.is_(None)).first() is not None


def viewer(page):
    """(email, access_row, link) for the current browser: the first verified
    email with access, else a pending row, else a private link opened here."""
    best = (None, None, None)
    for e in session_emails():
        row = access_for(page, e)
        if row is not None and _row_active(row, page):
            return e, row, None
        if has_access(page, e):
            return e, grant_invited(page, None, e), None
        if row is not None and best[1] is None:
            best = (e, row, None)
    link = session_link_for(page)
    if link is not None and best[1] is None:
        return link.investor_email, None, link
    if best[0] is None and session_emails():
        best = (session_emails()[0], None, None)
    return best


def is_full(page, row, link=None) -> bool:
    """Does this viewer see the full investor view, with the ask?"""
    if link is not None and link.revoked_at is None and link.startup_page_id == page.id:
        return True
    return bool(row is not None and _row_active(row, page))


def grant_invited(page, link, email, commit: bool = True):
    """Record invited (free) access for an email that came through a private
    link (link=None for a named link matched by email)."""
    from app.extensions import db
    from app.models.founder import InvestorAccess, InvestorLink
    email = (email or '').lower()
    if link is None:
        link = InvestorLink.query.filter(InvestorLink.startup_page_id == page.id, InvestorLink.investor_email == email,
                                         InvestorLink.revoked_at.is_(None)).first()
    if link is None or link.revoked_at is not None or link.startup_page_id != page.id:
        raise AccessError('This private link is no longer active.', 410)
    for r in _rows(page, email):
        if r.kind == 'invited' and r.investor_link_id == link.id and r.status == 'active':
            return r
    row = InvestorAccess(startup_page_id=page.id, email=email, kind='invited', investor_link_id=link.id,
                         status='active', opted_in_updates=False, created_at=_now())
    db.session.add(row)
    # A pending request or paid-pending row from the same email is settled by the invitation.
    for r in _rows(page, email):
        if r.status == 'requested':
            r.status = 'active'
    db.session.flush()
    _log(page, 'access_invited', 'link %s' % link.id)
    _stream(page, 'investor_access', {'kind': 'invited', 'access_id': row.id})
    if commit:
        db.session.commit()
    return row


def request_access(page, email, message='', name='', commit: bool = True):
    """An uninvited investor asks for the investor view. Founders are told."""
    from app.extensions import db
    from app.models.founder import InvestorAccess
    email = norm_email(email)
    if has_access(page, email):
        return access_for(page, email)
    for r in _rows(page, email):
        if r.status == 'requested':
            return r
        if r.status == 'denied' and r.created_at >= _now() - timedelta(days=30):
            raise AccessError('The founders have not approved access for this address. You can still send '
                              'them an introduction request from the page.', 409)
    message = ' '.join(str(message or '').split())[:MESSAGE_MAX]
    name = ' '.join(str(name or '').split())[:120]
    row = InvestorAccess(startup_page_id=page.id, email=email, kind='requested', status='requested',
                         opted_in_updates=False, created_at=_now())
    db.session.add(row)
    db.session.flush()
    _log(page, 'access_requested', '%s (%s)' % (row.id, _email_hash(email)[:8]))
    _stream(page, 'investor_access', {'kind': 'requested', 'access_id': row.id})
    if commit:
        db.session.commit()
    _notify_managers(page, 'Investor access request: %s' % page.name,
                     '%s%s asked for the investor view of the %s startup page.%s\n\nApprove or decline the request '
                     'in the Founder Track. SimiSI has not replied for you.'
                     % (name + ' ' if name else '', '<%s>' % email if name else email, page.name,
                        ('\n\nTheir note: ' + message) if message else ''))
    return row


def _decidable(access, user):
    from app.extensions import db
    from app.models.founder import InvestorAccess
    if isinstance(access, str):
        access = db.session.get(InvestorAccess, access)
    if access is None:
        raise AccessError('Not found.', 404)
    page = _page(access.startup_page_id)
    _require_manage(_venture(page), user)
    return access, page


def approve_access(access, user):
    from app.extensions import db
    access, page = _decidable(access, user)
    if access.status == 'active':
        return access
    if access.status != 'requested':
        raise AccessError('Only a pending request can be approved.', 409)
    access.status = 'active'
    access.approved_by = user.id
    _log(page, 'access_approved', access.id, user.id)
    _stream(page, 'investor_access', {'kind': access.kind, 'access_id': access.id, 'status': 'active',
                                      'actor_kind': 'founder', 'actor_user_id': user.id})
    db.session.commit()
    url = '%s/startup/%s/signin' % (_base_url(), page.slug)
    _send(access.email, 'Access approved: %s' % page.name,
          'The founders of %s approved your access to the investor view of their startup page. Sign in with this '
          'email address to open it:\n\n%s' % (page.name, url), url=url, button='Sign in', account_holder=False)
    return access


def deny_access(access, user):
    """Decline. A paid request is refunded in full (the fee bought nothing)."""
    from app.extensions import db
    access, page = _decidable(access, user)
    if access.status == 'denied' or access.status == 'refunded':
        return access
    if access.status not in ('requested', 'active'):
        raise AccessError('This access cannot be declined.', 409)
    access.status = 'denied'
    access.approved_by = user.id
    refunded = False
    if access.kind == 'paid' and access.payment_intent_id and access.refunded_at is None:
        refunded = _refund(access, 'Investor access declined by the founders')
    _log(page, 'access_denied', access.id + (' (refunded)' if refunded else ''), user.id)
    db.session.commit()
    _send(access.email, 'About your access request: %s' % page.name,
          'The founders of %s did not open the investor view to this address.%s You can still send them an '
          'introduction request from the public page.'
          % (page.name, ' Your investor access fee has been refunded in full.' if refunded else ''),
          account_holder=False)
    return access


def access_list(page) -> list:
    from app.models.founder import InvestorAccess
    rows = (InvestorAccess.query.filter_by(startup_page_id=page.id)
            .order_by(InvestorAccess.created_at.desc()).limit(500).all())
    iso = (lambda d: d.isoformat() + 'Z' if d else None)
    return [{'id': r.id, 'email': r.email, 'kind': r.kind, 'status': r.status,
             'active': _row_active(r, page), 'investor_link_id': r.investor_link_id,
             'amount_cents': r.amount_cents if r.kind == 'paid' else None, 'approved_by': r.approved_by,
             'refunded_at': iso(r.refunded_at), 'created_at': iso(r.created_at)} for r in rows]


# ---------------------------------------------------------------------------
# Payment
# ---------------------------------------------------------------------------

def create_checkout(page, email) -> dict:
    """Stripe Checkout for one page. The price is the admin setting, never a
    client value; paid access needs a price of at least 50 cents."""
    from app.extensions import db
    from app.models.audit_log import AuditLog
    from app.services import stripe_service
    email = norm_email(email)
    if not paid_access_available():
        raise AccessError('Paid access is not offered right now. Ask the founders for a private link or send '
                          'an access request.', 409)
    if has_access(page, email):
        raise AccessError('This address already has access to the investor view.', 409)
    pending = access_for(page, email)
    if pending is not None and pending.kind == 'paid' and pending.status == 'requested':
        raise AccessError('You already paid; the founders are reviewing your access.', 409)
    reuse = _open_session(page, email)
    if reuse:
        return reuse
    result = stripe_service.create_investor_access_checkout(page, email)
    AuditLog.log('investor_checkout_started', resource_id=page.id,
                 metadata={'session_id': result.get('session_id'), 'email_hash': _email_hash(email),
                           'amount_cents': result.get('amount_cents')})
    db.session.commit()
    return result


def _open_session(page, email):
    """The last open Checkout Session for this page and email at today's
    price, so a double-click does not make a second chargeable session."""
    from app.models.audit_log import AuditLog
    from app.services import stripe_service
    row = (AuditLog.query.filter(AuditLog.action == 'investor_checkout_started', AuditLog.resource_id == page.id,
                                 AuditLog.created_at >= _now() - OPEN_SESSION_REUSE,
                                 AuditLog._metadata.like('%%"email_hash": "%s"%%' % _email_hash(email)))
           .order_by(AuditLog.created_at.desc()).first())
    sid = (row.extra or {}).get('session_id') if row is not None else None
    if not sid:
        return None
    try:
        cs = stripe_service._stripe().checkout.Session.retrieve(sid)
    except Exception as exc:
        logger.info('investor checkout %s could not be reused: %s', sid, exc)
        return None
    meta = _g(cs, 'metadata', {}) or {}
    if _g(meta, 'startup_page_id') != page.id or (_g(meta, 'email') or '').lower() != email:
        return None
    if _g(cs, 'payment_status') == 'paid':
        fulfil(cs)
        return None
    if (_g(cs, 'status') == 'open' and str(_g(cs, 'amount_total') or _g(meta, 'amount_cents') or '')
            == str(price_cents()) and _g(cs, 'url')):
        return {'checkout_url': _g(cs, 'url'), 'session_id': sid, 'amount_cents': price_cents(), 'reused': True}
    return None


def fulfil(session) -> bool:
    """Record a paid investor-access Checkout Session. Idempotent: True only
    for the call that created the row. Under 506(c) the access is active at
    once; under 506(b) or undecided an uninvited payer waits for the founders."""
    from app.extensions import db
    from app.models.audit_log import AuditLog
    from app.models.founder import InvestorAccess
    meta = _g(session, 'metadata', {}) or {}
    if _g(meta, 'product') != 'investor_access':
        return False
    sid = _g(session, 'id')
    if _g(session, 'payment_status') != 'paid' or not sid:
        logger.info('investor checkout %s not paid yet (%s)', sid, _g(session, 'payment_status'))
        return False
    page = _page(_g(meta, 'startup_page_id'))
    if page is None:
        logger.error('investor checkout %s names an unknown page %s', sid, _g(meta, 'startup_page_id'))
        return False
    try:
        email = norm_email(_g(meta, 'email'))
    except AccessError:
        logger.error('investor checkout %s has no usable email', sid)
        return False
    if InvestorAccess.query.filter_by(stripe_session_id=sid).first() is not None:
        return False                                   # webhook replay or the success return
    pi = _g(session, 'payment_intent')
    pi = _g(pi, 'id') if pi is not None and not isinstance(pi, str) else pi
    try:
        amount = int(_g(session, 'amount_total') or 0)
    except (TypeError, ValueError):
        amount = 0
    venture = _venture(page)
    invited = has_access(page, email)
    ex = exemption(venture)
    status = 'active' if (ex == '506c' or invited) else 'requested'
    already_paid = any(r.kind == 'paid' and r.status in ('active', 'requested') for r in _rows(page, email))
    row = InvestorAccess(startup_page_id=page.id, email=email, kind='paid', stripe_session_id=sid,
                         payment_intent_id=pi or None, amount_cents=amount or None, status=status,
                         opted_in_updates=False, created_at=_now())
    db.session.add(row)
    db.session.flush()
    if InvestorAccess.query.filter_by(stripe_session_id=sid).count() > 1:
        db.session.rollback()                          # lost a race with the other path
        return False
    _log(page, 'access_paid', '%s %s (%s, %d cents)' % (row.id, status, ex, amount))
    _stream(page, 'investor_access', {'kind': 'paid', 'access_id': row.id, 'status': status})
    if already_paid:
        AuditLog.log('investor_duplicate_payment', resource_id=page.id,
                     metadata={'payment_intent': pi, 'checkout': sid, 'access_id': row.id, 'amount_cents': amount})
        logger.error('investor access duplicate payment on page %s (%s); refund it in Stripe', page.id, pi)
    db.session.commit()
    from app.services import startup_page_service as sps
    if not sps.is_public(page) and pi:
        # The page went offline between checkout and payment: the fee buys nothing.
        if _refund(row, 'Startup page not published at payment'):
            db.session.commit()
        return True
    if status == 'active':
        _notify_managers(page, 'An investor unlocked the %s startup page' % page.name,
                         'An investor (%s) paid for investor access to the %s startup page and can now see the '
                         'investor view, including the ask. The fee goes to SimiSI and is not tied to any '
                         'investment.' % (email, page.name))
    else:
        _notify_managers(page, 'Approve investor access: %s' % page.name,
                         'An investor (%s) paid for investor access to the %s startup page. Because the offering '
                         'is %s, they see the ask only after you approve their access in the Founder Track. If you '
                         'decline, SimiSI refunds their fee.'
                         % (email, page.name, 'under 506(b)' if ex == '506b' else 'not yet under a chosen exemption'))
    return True


def verify_session(session_id, page, emails) -> bool:
    """The success-return fallback: fetch the session from Stripe and fulfil
    it when it belongs to this page and an email verified in this browser."""
    from app.services import stripe_service
    try:
        cs = stripe_service._stripe().checkout.Session.retrieve(session_id)
    except Exception as exc:
        logger.warning('investor checkout verify failed for %s: %s', session_id, exc)
        return False
    meta = _g(cs, 'metadata', {}) or {}
    if _g(meta, 'startup_page_id') != page.id or (_g(meta, 'email') or '').lower() not in [e.lower() for e in emails]:
        return False
    fulfil(cs)
    return True


def _refund(row, reason) -> bool:
    """Refund one paid row in full. Does not commit. False if Stripe refused."""
    from app.services import stripe_service
    try:
        stripe_service.issue_refund(row.payment_intent_id, reason=reason)
    except Exception as exc:
        logger.error('investor access refund failed for %s (%s): %s', row.id, row.payment_intent_id, exc)
        return False
    row.status = 'refunded'
    row.refunded_at = _now()
    page = _page(row.startup_page_id)
    if page is not None:
        _log(page, 'access_refunded', '%s: %s' % (row.id, reason))
    return True


def refund_if_unpublished(page) -> int:
    """Refund paid access bought within fnd_refund_window_days before the page
    was unpublished. Returns how many were refunded. Commits."""
    from app.extensions import db
    from app.models.founder import InvestorAccess
    since = _now() - timedelta(days=refund_window_days())
    rows = (InvestorAccess.query.filter(InvestorAccess.startup_page_id == page.id, InvestorAccess.kind == 'paid',
                                        InvestorAccess.status.in_(('active', 'requested', 'pending')),
                                        InvestorAccess.refunded_at.is_(None),
                                        InvestorAccess.payment_intent_id.isnot(None),
                                        InvestorAccess.created_at >= since).all())
    n = 0
    for r in rows:
        if _refund(r, 'Startup page unpublished'):
            n += 1
            _send(r.email, 'Refund: %s investor access' % page.name,
                  'The founders unpublished the %s startup page, so SimiSI refunded your investor access fee in '
                  'full. It can take a few days to appear on your statement.' % page.name, account_holder=False)
    db.session.commit()
    return n


def mark_refunded(payment_intent_id, fully_refunded: bool = True) -> bool:
    """charge.refunded webhook: a full refund ends that paid access. Touches
    nothing when the intent belongs to a Founder Track payment."""
    from app.extensions import db
    from app.models.founder import FounderVenture, InvestorAccess
    if not payment_intent_id or not fully_refunded:
        return False
    if FounderVenture.query.filter_by(payment_intent_id=payment_intent_id).first() is not None:
        return False
    row = InvestorAccess.query.filter_by(payment_intent_id=payment_intent_id).first()
    if row is None or row.refunded_at is not None:
        return False
    row.status = 'refunded'
    row.refunded_at = _now()
    page = _page(row.startup_page_id)
    if page is not None:
        _log(page, 'access_refunded', '%s: refunded in Stripe' % row.id)
    db.session.commit()
    return True


# ---------------------------------------------------------------------------
# Investor view
# ---------------------------------------------------------------------------

def full_proof(venture) -> list:
    """Every proof-of-concept result: measured flag, benefits, criteria, and a
    quote only when the customer gave permission. Customer names stay out."""
    from app.models.founder import ProofPlan, ProofResult
    plans = {p.id: p for p in ProofPlan.query.filter_by(venture_id=venture.id).all()}
    out = []
    for r in (ProofResult.query.filter_by(venture_id=venture.id).order_by(ProofResult.created_at.desc())
              .limit(20).all()):
        plan = plans.get(r.proof_plan_id)
        criteria = []
        for c in (plan.success_criteria or []) if plan is not None and isinstance(plan.success_criteria, list) else []:
            if isinstance(c, dict):
                t = ' '.join(str(c.get('metric') or c.get('label') or c.get('text') or '').split())
                tgt = ' '.join(str(c.get('target') or '').split())
                if t:
                    criteria.append((t + (': ' + tgt if tgt else ''))[:200])
            elif isinstance(c, str) and c.strip():
                criteria.append(' '.join(c.split())[:200])
        item = {'measured': bool(r.measured), 'benefits': ' '.join((r.benefits_text or '').split()),
                'quote': ' '.join((r.quote_text or '').split()) if r.quote_permission else '',
                'paid': bool(r.paid_at), 'criteria': criteria[:6],
                'duration': (plan.duration or '') if plan is not None else '',
                'date': r.created_at}
        if item['benefits'] or item['quote'] or item['criteria']:
            out.append(item)
    return out


def investor_context(page, access=None, link=None) -> dict:
    """The investor view. Full access (an active row or a private link) adds the
    ask and the data-room request; a paid row awaiting approval sees the full
    proof and traction detail but not the ask."""
    from app.services import startup_page_service as sps
    full = is_full(page, access, link)
    if access is None and link is None:
        raise AccessError('No investor access.', 403)
    ctx = sps.investor_context(page, access or link)
    venture = _venture(page)
    ctx['full_proof'] = full_proof(venture)
    ctx['ask_pending'] = not full
    if not full:
        ctx['ask'] = None
        ctx['request_kinds'] = {k: sps.REQUEST_KINDS[k] for k in sps.PUBLIC_REQUEST_KINDS}
    ctx['access'] = {'email': getattr(access, 'email', None) or getattr(link, 'investor_email', None),
                     'kind': getattr(access, 'kind', None) or 'invited',
                     'status': getattr(access, 'status', None) or 'active', 'full': full}
    try:
        from app.services import contact_masking
        ctx['full_proof'] = contact_masking.mask_deep(ctx['full_proof'], force=True)
        if ctx.get('ask'):
            ctx['ask'] = contact_masking.mask_deep(ctx['ask'], force=True)
    except Exception as exc:
        logger.warning('investor view masking failed: %s', exc)
    return ctx


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------

def _notify_managers(page, subject, text):
    venture = _venture(page)
    for u in _managers(venture):
        _send(u.email, subject, text)


def _send(to, subject, text, url=None, button=None, account_holder: bool = True) -> bool:
    """A plain transactional email through the house path; never raises."""
    try:
        from markupsafe import escape
        from app.services import email_service as es
        body = es._h1(str(escape(subject))) + ''.join(
            es._p(str(escape(p)).replace('\n', '<br>')) for p in text.split('\n\n') if p and p != url)
        if url and button:
            body += es._btn(url, button)
        footer = None if account_holder else ('SimiSI · Bay Area Experiences Ventures<br>\n        You received '
                                              'this email because this address was entered on a SimiSI startup page.')
        html = es._html_wrap(body, preheader=subject[:90], footer_html=footer)
        es._send(subject=subject + ' - SimiSI', recipients=[to], body=text + '\n\n- SimiSI', html=html)
        return True
    except Exception as exc:
        logger.error('investor access email failed: %s', exc)
        return False
