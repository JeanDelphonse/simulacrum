"""Employer Transition Package (PRD-SIM-TRANS-001, phase 1: design partners).

SimiSI staff set up employers (organizations with org_type 'transition'),
cohorts and seats at /admin/transition. Employees activate with a personal
invite, see what their employer can and can't see, and get the tier's
simulations, coaching sessions and access period. Employers get aggregate
numbers only, with small groups hidden.

Every price and tier knob is a platform setting (transition_*). Nothing here
reads an employee's simulation results, contacts, outreach or coaching notes
on the employer's behalf.
"""
from __future__ import annotations

import calendar
import csv
import io
import json
import logging
import re
import secrets
from datetime import date, datetime, timedelta

logger = logging.getLogger(__name__)

DEFAULT_TIERS = {
    'essentials': {'label': 'Essentials', 'access_months': 3, 'reruns': 1, 'coaching_sessions': 0,
                   'workshops': False, 'senior_coach': False},
    'plus': {'label': 'Plus', 'access_months': 6, 'reruns': 2, 'coaching_sessions': 3,
             'workshops': True, 'senior_coach': False},
    'executive': {'label': 'Executive', 'access_months': 12, 'reruns': 12, 'coaching_sessions': 8,
                  'workshops': True, 'senior_coach': True},
}
DEFAULT_SETTINGS = {
    'transition_tiers': json.dumps(DEFAULT_TIERS),
    'transition_price_essentials_cents': 0,     # 0 = not set yet; prices live in Admin
    'transition_price_plus_cents': 0,
    'transition_price_executive_cents': 0,
    'transition_volume_bands': '[]',            # [[min_seats, discount_pct], ...]
    'transition_coach_pay_cents': 0,
    'transition_min_report_size': 5,
    'transition_reminder_days': '3,7,14',
    'transition_unassigned_expiry_months': 12,
    'transition_block_employer_domains': 'on',
    'transition_coach_review_rating': '4.0',
    'transition_coach_min_ratings': 3,
    'transition_noshow_grace': 1,
}
OUTCOME_LABELS = {'new_role': 'New role', 'consulting': 'Consulting or independent income',
                  'venture': 'Started a venture', 'education': 'Further education',
                  'searching': 'Still searching', 'other': 'Other'}
_EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$')


class TransitionError(Exception):
    """A user-facing refusal."""


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

def setting(key: str):
    default = DEFAULT_SETTINGS[key]
    try:
        from app.models.platform_settings import PlatformSetting
        raw = PlatformSetting.get(key, None)
    except Exception:
        return default
    if raw in (None, ''):
        return default
    if isinstance(default, int):
        try:
            return int(raw)
        except (TypeError, ValueError):
            return default
    return raw


def tiers() -> dict:
    """The tier table (admin-editable JSON), each tier complete and sane."""
    try:
        custom = json.loads(setting('transition_tiers'))
    except (TypeError, ValueError):
        custom = {}
    out = {}
    for key, base in DEFAULT_TIERS.items():
        t = dict(base, **(custom.get(key) or {}))
        for k in ('access_months', 'reruns', 'coaching_sessions'):
            try:
                t[k] = max(0, int(t[k]))
            except (TypeError, ValueError):
                t[k] = base[k]
        t['access_months'] = max(1, t['access_months'])
        t['price_cents'] = setting('transition_price_%s_cents' % key)
        out[key] = t
    return out


def quote(tier_counts: dict) -> dict:
    """Order total from the admin prices and volume bands."""
    tt = tiers()
    lines, missing, seats = [], [], 0
    for tier, n in (tier_counts or {}).items():
        n = int(n or 0)
        if tier not in tt or n <= 0:
            continue
        unit = tt[tier]['price_cents']
        if not unit:
            missing.append(tt[tier]['label'])
        lines.append({'tier': tier, 'label': tt[tier]['label'], 'seats': n, 'unit_cents': unit,
                      'subtotal_cents': unit * n})
        seats += n
    try:
        bands = sorted(json.loads(setting('transition_volume_bands')) or [], key=lambda b: b[0])
    except (TypeError, ValueError):
        bands = []
    pct = 0
    for min_seats, discount in bands:
        if seats >= int(min_seats):
            pct = float(discount)
    gross = sum(l['subtotal_cents'] for l in lines)
    return {'lines': lines, 'seats': seats, 'gross_cents': gross, 'discount_pct': pct,
            'total_cents': int(round(gross * (1 - pct / 100.0))), 'missing_prices': missing}


def min_report_size(cohort=None) -> int:
    base = setting('transition_min_report_size')
    return max(2, int(getattr(cohort, 'min_report_size', None) or base))


# ---------------------------------------------------------------------------
# Employers, cohorts, seats (staff-run in phase 1)
# ---------------------------------------------------------------------------

def _encrypt(text: str) -> str:
    from app.services.token_crypto import encrypt_token
    return encrypt_token(text)


def _decrypt(blob: str | None) -> str | None:
    if not blob:
        return None
    from app.services.token_crypto import decrypt_token
    try:
        return decrypt_token(blob)
    except Exception:
        return None


def _email_hash(email: str) -> str:
    from app.models.mail_crm import email_hash
    return email_hash(email)


def create_employer(name: str, contact_name: str, contact_email: str, admin_id: str):
    from app.extensions import db
    from app.models.audit_log import AuditLog
    from app.models.corporate import CorporateAccount
    name, contact_email = (name or '').strip(), (contact_email or '').strip().lower()
    if not name or not _EMAIL_RE.match(contact_email):
        raise TransitionError('Enter the employer name and an HR contact email.')
    org = CorporateAccount(org_name=name[:200], contact_name=(contact_name or name)[:200],
                           contact_email=contact_email, org_type=CorporateAccount.ORG_TRANSITION,
                           status=CorporateAccount.STATUS_ACTIVE, seat_count=0, credits_purchased=0,
                           credits_remaining=0, activated_at=datetime.utcnow(), contract_start=date.today())
    db.session.add(org)
    db.session.flush()
    AuditLog.log('transition_employer_created', user_id=admin_id, resource_id=org.id, metadata={'name': name})
    db.session.commit()
    return org


def transition_org(org_id: str):
    from app.models.corporate import CorporateAccount
    org = CorporateAccount.query.get(org_id)
    if org is None or org.org_type != CorporateAccount.ORG_TRANSITION:
        raise TransitionError('Not a transition employer.')
    return org


def _parse_date(v):
    if isinstance(v, date) or v is None:
        return v
    v = str(v).strip()
    return datetime.strptime(v, '%Y-%m-%d').date() if v else None


def create_cohort(org, name: str, last_working_day=None, invite_date=None, employer_domains=None,
                  min_size=None, admin_id: str | None = None):
    from app.extensions import db
    from app.models.audit_log import AuditLog
    from app.models.transition import TransitionCohort
    if not (name or '').strip():
        raise TransitionError('Name the cohort (for example "March 2027 reduction").')
    try:
        c = TransitionCohort(org_id=org.id, name=name.strip()[:200], last_working_day=_parse_date(last_working_day),
                             invite_date=_parse_date(invite_date), created_by=admin_id,
                             min_report_size=int(min_size) if min_size not in (None, '') else None)
    except ValueError:
        raise TransitionError('Dates are YYYY-MM-DD.')
    if isinstance(employer_domains, str):
        employer_domains = re.split(r'[\s,;]+', employer_domains)
    c.employer_domains = employer_domains or []
    db.session.add(c)
    db.session.flush()
    AuditLog.log('transition_cohort_created', user_id=admin_id, resource_id=c.id, metadata={'org_id': org.id})
    db.session.commit()
    return c


def parse_roster(text: str) -> list:
    """CSV with columns name, personal email, tier (header optional)."""
    rows = []
    for rec in csv.reader(io.StringIO(text or '')):
        rec = [x.strip() for x in rec]
        if not any(rec):
            continue
        if rec[0].lower() in ('name', 'full name') and len(rec) > 1 and 'mail' in rec[1].lower():
            continue
        rows.append({'name': rec[0] if len(rec) > 0 else '', 'email': rec[1] if len(rec) > 1 else '',
                     'tier': (rec[2] if len(rec) > 2 else '').lower()})
    return rows


def add_seats(cohort, roster: list | None = None, unassigned: dict | None = None,
              default_tier: str = 'essentials', admin_id: str | None = None) -> dict:
    """Named seats from a roster, and/or unassigned seats by tier."""
    from app.extensions import db
    from app.models.audit_log import AuditLog
    from app.models.corporate import CorporateAccount
    from app.models.transition import TransitionSeat
    tt = tiers()
    existing = {s.invite_email_hash for s in TransitionSeat.query.filter(
        TransitionSeat.cohort_id == cohort.id, TransitionSeat.status != TransitionSeat.REVOKED).all()}
    added, skipped = 0, []
    for row in roster or []:
        email = (row.get('email') or '').strip().lower()
        tier = (row.get('tier') or default_tier).lower()
        if not _EMAIL_RE.match(email):
            skipped.append({'row': row, 'reason': 'Not a valid personal email'})
            continue
        if tier not in tt:
            skipped.append({'row': row, 'reason': 'Unknown tier "%s"' % tier})
            continue
        h = _email_hash(email)
        if h in existing:
            skipped.append({'row': row, 'reason': 'Already in this cohort'})
            continue
        existing.add(h)
        db.session.add(TransitionSeat(cohort_id=cohort.id, org_id=cohort.org_id, tier=tier,
                                      status=TransitionSeat.INVITED, full_name=(row.get('name') or '')[:200],
                                      invite_email_enc=_encrypt(email), invite_email_hash=h))
        added += 1
    for tier, n in (unassigned or {}).items():
        if tier not in tt:
            raise TransitionError('Unknown tier "%s".' % tier)
        for _ in range(max(0, int(n or 0))):
            db.session.add(TransitionSeat(cohort_id=cohort.id, org_id=cohort.org_id, tier=tier))
            added += 1
    org = CorporateAccount.query.get(cohort.org_id)
    if org is not None:
        org.seat_count = (org.seat_count or 0) + added
    AuditLog.log('transition_seats_added', user_id=admin_id, resource_id=cohort.id,
                 metadata={'added': added, 'skipped': len(skipped)})
    db.session.commit()
    return {'added': added, 'skipped': skipped}


def _seat(seat_id):
    from app.models.transition import TransitionSeat
    s = TransitionSeat.query.get(seat_id)
    if s is None:
        raise TransitionError('Seat not found.')
    return s


def assign_seat(seat_id: str, name: str, email: str, admin_id: str):
    """Give an unassigned seat to someone, or reassign an unused invite."""
    from app.extensions import db
    from app.models.audit_log import AuditLog
    from app.models.transition import TransitionSeat
    s = _seat(seat_id)
    if s.status not in (TransitionSeat.UNASSIGNED, TransitionSeat.INVITED):
        raise TransitionError('Only an unassigned or unused seat can be reassigned.')
    email = (email or '').strip().lower()
    if not _EMAIL_RE.match(email):
        raise TransitionError('Enter the person\'s personal email.')
    s.full_name, s.invite_email_enc, s.invite_email_hash = (name or '')[:200], _encrypt(email), _email_hash(email)
    s.status, s.invite_token, s.invite_sent_at, s.reminder_count, s.invited_at = (
        TransitionSeat.INVITED, None, None, 0, None)
    AuditLog.log('transition_seat_assigned', user_id=admin_id, resource_id=s.id)
    db.session.commit()
    return s


def revoke_seat(seat_id: str, admin_id: str):
    from app.extensions import db
    from app.models.audit_log import AuditLog
    from app.models.transition import TransitionSeat
    s = _seat(seat_id)
    if s.status in (TransitionSeat.ACTIVATED, TransitionSeat.EXPIRED):
        raise TransitionError('An activated seat can\'t be revoked; the employee keeps the full access period.')
    s.status, s.revoked_at, s.invite_token = TransitionSeat.REVOKED, datetime.utcnow(), None
    AuditLog.log('transition_seat_revoked', user_id=admin_id, resource_id=s.id)
    db.session.commit()
    return s


# ---------------------------------------------------------------------------
# Invites, reminders, expiry (cron)
# ---------------------------------------------------------------------------

def _join_url(token: str) -> str:
    from flask import current_app
    base = (current_app.config.get('BASE_URL') or 'https://simisi.io').rstrip('/')
    return '%s/transition/join/%s' % (base, token)


def send_invite(seat, reminder: bool = False) -> bool:
    from app.models.corporate import CorporateAccount
    from app.services.email_service import send_transition_invite_email
    email = _decrypt(seat.invite_email_enc)
    if not email:
        return False
    if not seat.invite_token:
        seat.invite_token = secrets.token_urlsafe(32)
    org = CorporateAccount.query.get(seat.org_id)
    t = tiers().get(seat.tier, {})
    send_transition_invite_email(email, seat.full_name or '', getattr(org, 'org_name', 'Your employer'),
                                 t.get('label', seat.tier), t.get('access_months', 3),
                                 _join_url(seat.invite_token), reminder=reminder)
    now = datetime.utcnow()
    if reminder:
        seat.reminder_count, seat.last_reminded_at = (seat.reminder_count or 0) + 1, now
    else:
        seat.invite_sent_at, seat.invited_at = now, seat.invited_at or now
    return True


def send_due_invites(now: datetime | None = None) -> int:
    from app.extensions import db
    from app.models.transition import TransitionCohort, TransitionSeat
    now = now or datetime.utcnow()
    n = 0
    for s in TransitionSeat.query.filter_by(status=TransitionSeat.INVITED, invite_sent_at=None).all():
        c = TransitionCohort.query.get(s.cohort_id)
        if c is None or (c.invite_date and c.invite_date > now.date()):
            continue
        if send_invite(s):
            n += 1
    db.session.commit()
    return n


def send_due_reminders(now: datetime | None = None) -> int:
    from app.extensions import db
    from app.models.transition import TransitionSeat
    now = now or datetime.utcnow()
    days = [int(d) for d in re.split(r'[\s,]+', str(setting('transition_reminder_days'))) if d.strip().isdigit()]
    n = 0
    for s in TransitionSeat.query.filter(TransitionSeat.status == TransitionSeat.INVITED,
                                         TransitionSeat.invite_sent_at.isnot(None)).all():
        k = s.reminder_count or 0
        if k < len(days) and (now - s.invite_sent_at).days >= days[k]:
            if send_invite(s, reminder=True):
                n += 1
    db.session.commit()
    return n


def expire_seats(now: datetime | None = None) -> int:
    """Activated seats past their access period; unassigned seats past the
    admin window. The employee keeps their account and bio page (free plan)."""
    from app.extensions import db
    from app.models.profile import UserProfile
    from app.models.transition import TransitionSeat
    now = now or datetime.utcnow()
    n = 0
    for s in TransitionSeat.query.filter(TransitionSeat.status == TransitionSeat.ACTIVATED,
                                         TransitionSeat.expires_at <= now).all():
        s.status = TransitionSeat.EXPIRED
        prof = UserProfile.query.filter_by(user_id=s.user_id).first()
        if prof and prof.org_id == s.org_id:
            prof.org_id = None                    # co-branding ends; account stays theirs
        n += 1
    cutoff = now - timedelta(days=30 * setting('transition_unassigned_expiry_months'))
    for s in TransitionSeat.query.filter(TransitionSeat.status == TransitionSeat.UNASSIGNED,
                                         TransitionSeat.created_at <= cutoff).all():
        s.status = TransitionSeat.EXPIRED
        n += 1
    db.session.commit()
    return n


def run_maintenance(force: bool = False) -> dict:
    """Called from cron_generate.py every minute; does the work at most hourly."""
    from app.extensions import db
    from app.models.platform_settings import PlatformSetting
    now = datetime.utcnow()
    last = PlatformSetting.get('transition_maintenance_at', None)
    if not force and last:
        try:
            if now - datetime.fromisoformat(last) < timedelta(hours=1):
                return {'skipped': True}
        except ValueError:
            pass
    PlatformSetting.set('transition_maintenance_at', now.isoformat())
    db.session.commit()
    return {'invites': send_due_invites(now), 'reminders': send_due_reminders(now), 'expired': expire_seats(now)}


# ---------------------------------------------------------------------------
# Employee: activation, entitlements, guards
# ---------------------------------------------------------------------------

def seat_for_token(token: str):
    from app.models.transition import TransitionSeat
    s = TransitionSeat.query.filter_by(invite_token=token).first() if token else None
    if s is None or s.status != TransitionSeat.INVITED:
        raise TransitionError('This invitation is no longer valid. Ask your HR contact for a new one.')
    return s


def _add_months(dt: datetime, months: int) -> datetime:
    y, m = divmod(dt.month - 1 + months, 12)
    y, m = dt.year + y, m + 1
    return dt.replace(year=y, month=m, day=min(dt.day, calendar.monthrange(y, m)[1]))


def activate(token: str, user, share_outcomes: bool, consent: bool):
    """The employee accepts. They must have seen the privacy summary."""
    from app.extensions import db
    from app.models.audit_log import AuditLog
    from app.models.profile import UserProfile
    from app.models.transition import TransitionSeat
    if consent is not True:
        raise TransitionError('Please confirm you have read what your employer can and can\'t see.')
    s = seat_for_token(token)
    if live_seat(user.id) is not None:
        raise TransitionError('You already have an active transition package.')
    t = tiers()[s.tier]
    now = datetime.utcnow()
    s.status, s.user_id, s.activated_at = TransitionSeat.ACTIVATED, user.id, now
    s.access_months = t['access_months']
    s.expires_at = _add_months(now, t['access_months'])
    s.sims_allowed = 1 + t['reruns']
    s.sessions_total = t['coaching_sessions']
    s.consent_at, s.share_outcomes, s.invite_token = now, bool(share_outcomes), None
    prof = UserProfile.query.filter_by(user_id=user.id).first()
    if prof and not prof.org_id:
        prof.org_id = s.org_id                    # co-branding during access
    if s.sessions_total:
        match_coach(s)
    AuditLog.log('transition_seat_activated', user_id=user.id, resource_id=s.id,
                 metadata={'tier': s.tier, 'share_outcomes': s.share_outcomes})
    db.session.commit()
    return s


def live_seat(user_id: str):
    from app.models.transition import TransitionSeat
    seats = TransitionSeat.query.filter_by(user_id=user_id, status=TransitionSeat.ACTIVATED).all()
    return next((s for s in seats if s.is_live), None)


def latest_seat(user_id: str):
    from app.models.transition import TransitionSeat
    return (TransitionSeat.query.filter(TransitionSeat.user_id == user_id)
            .order_by(TransitionSeat.activated_at.desc()).first())


def try_redeem_seat(user_id: str, sim) -> bool:
    """Fund a simulation (first run or re-run) from the employee's live seat."""
    from app.extensions import db
    from app.models.audit_log import AuditLog
    from app.models.transition import TransitionSeat
    s = live_seat(user_id)
    if s is None:
        return False
    updated = (db.session.query(TransitionSeat)
               .filter(TransitionSeat.id == s.id, TransitionSeat.status == TransitionSeat.ACTIVATED,
                       TransitionSeat.sims_used < TransitionSeat.sims_allowed)
               .update({TransitionSeat.sims_used: TransitionSeat.sims_used + 1}, synchronize_session=False))
    if not updated:
        return False
    AuditLog.log('transition_simulation_funded', user_id=user_id, resource_id=getattr(sim, 'id', None),
                 metadata={'seat_id': s.id})
    db.session.flush()
    return True


def employer_domains_for(user_id: str) -> set:
    from app.models.transition import TransitionCohort, TransitionSeat
    from app.models.corporate import CorporateAccount
    out = set()
    for s in TransitionSeat.query.filter(TransitionSeat.user_id == user_id).all():
        c = TransitionCohort.query.get(s.cohort_id)
        if c:
            out |= set(c.employer_domains)
        org = CorporateAccount.query.get(s.org_id)
        if org is not None and org.auto_join_domains:
            out |= {d.lower() for d in org.auto_join_domains}
    return out


def mailbox_blocked(user_id: str, address: str) -> bool:
    """Email-to-CRM must not connect a mailbox on the former employer's domains."""
    if str(setting('transition_block_employer_domains')).lower() != 'on':
        return False
    d = (address or '').rsplit('@', 1)[-1].lower()
    return any(d == x or d.endswith('.' + x) for x in employer_domains_for(user_id))


CONFIDENTIALITY_NOTE = ('Before this goes live: please don\'t share your former employer\'s confidential '
                        'information (clients, pricing, internal projects or data). You are responsible for '
                        'what you publish and send.')


def needs_confidentiality_ack(user_id: str) -> bool:
    s = live_seat(user_id)
    return s is not None and s.confidentiality_ack_at is None


def acknowledge_confidentiality(user_id: str) -> None:
    from app.extensions import db
    s = live_seat(user_id)
    if s is not None and s.confidentiality_ack_at is None:
        s.confidentiality_ack_at = datetime.utcnow()
        db.session.commit()


def report_outcome(user_id: str, outcome: str, share: bool):
    from app.extensions import db
    from app.models.transition import TransitionOutcome
    if outcome not in TransitionOutcome.OUTCOMES:
        raise TransitionError('Choose an outcome.')
    s = latest_seat(user_id)
    if s is None:
        raise TransitionError('No transition package on your account.')
    o = TransitionOutcome(seat_id=s.id, outcome=outcome, share_with_employer=bool(share))
    s.share_outcomes = bool(share)            # the seat switch is the employee's standing consent
    db.session.add(o)
    db.session.commit()
    return o


def set_sharing(user_id: str, share: bool) -> None:
    """The employee's consent switch. Turning it off withdraws every outcome
    they reported from the employer's totals at once."""
    from app.extensions import db
    s = latest_seat(user_id)
    if s is None:
        raise TransitionError('No transition package on your account.')
    s.share_outcomes = bool(share)
    db.session.commit()


def employee_view(user_id: str) -> dict | None:
    from app.models.corporate import CorporateAccount
    from app.models.sme import SimiSME
    from app.models.transition import CoachingSession, TransitionOutcome
    s = latest_seat(user_id)
    if s is None:
        return None
    t = tiers().get(s.tier, {})
    coach = SimiSME.query.get(s.coach_id) if s.coach_id else None
    sessions = CoachingSession.query.filter_by(seat_id=s.id).order_by(CoachingSession.created_at.desc()).all()
    outcomes = TransitionOutcome.query.filter_by(seat_id=s.id).order_by(TransitionOutcome.reported_at.desc()).all()
    org = CorporateAccount.query.get(s.org_id)
    return {
        'seat_id': s.id, 'tier': s.tier, 'tier_label': t.get('label', s.tier), 'status': s.status,
        'live': s.is_live, 'employer': getattr(org, 'org_name', ''),
        'activated_at': s.activated_at.isoformat() if s.activated_at else None,
        'expires_at': s.expires_at.isoformat() if s.expires_at else None,
        'sims_left': max(0, s.sims_allowed - s.sims_used), 'sims_allowed': s.sims_allowed,
        'sessions_left': max(0, s.sessions_total - s.sessions_used), 'sessions_total': s.sessions_total,
        'workshops': bool(t.get('workshops')), 'share_outcomes': s.share_outcomes,
        'coach_access': s.coach_access, 'can_change_coach': s.coach_changes < 1 and coach is not None,
        'coach': {'name': coach.full_name, 'credentials': coach.coach_credentials,
                  'booking_url': coach.coach_booking_url, 'specialties': coach.coach_specialties} if coach else None,
        'sessions': [x.to_dict() for x in sessions],
        'outcomes': [{'outcome': o.outcome, 'label': OUTCOME_LABELS[o.outcome], 'shared': o.share_with_employer,
                      'reported_at': o.reported_at.isoformat()} for o in outcomes],
        'confidentiality_ack': s.confidentiality_ack_at is not None,
    }


# ---------------------------------------------------------------------------
# Employer report: aggregate only, small groups hidden
# ---------------------------------------------------------------------------

def _cell(n: int, k: int):
    """Counts below the minimum group size are shown as "fewer than k"."""
    return n if n == 0 or n >= k else '<%d' % k


def report(org, cohort_id: str | None = None) -> dict:
    from app.extensions import db
    from app.models.bio_page import BioPage
    from app.models.transition import CoachingSession, TransitionCohort, TransitionOutcome, TransitionSeat
    q = TransitionSeat.query.filter(TransitionSeat.org_id == org.id)
    cohort = None
    if cohort_id:
        cohort = TransitionCohort.query.filter_by(id=cohort_id, org_id=org.id).first()
        if cohort is None:
            raise TransitionError('Cohort not found.')
        q = q.filter(TransitionSeat.cohort_id == cohort_id)
    seats = q.all()
    k = min_report_size(cohort)
    status_counts = {st: sum(1 for s in seats if s.status == st) for st in TransitionSeat.STATUSES}
    reached = [s for s in seats if s.status in (TransitionSeat.ACTIVATED, TransitionSeat.EXPIRED)]
    offered = [s for s in seats if s.status != TransitionSeat.REVOKED and s.status != TransitionSeat.UNASSIGNED]
    out = {'min_group': k, 'seats': status_counts, 'seats_total': len(seats),
           'activation_rate': round(100 * len(reached) / len(offered)) if offered else 0,
           'suppressed': len(reached) < k}
    if out['suppressed']:
        out['note'] = ('Fewer than %d employees have activated, so no other numbers are shown. '
                       'This protects individuals from being identified.' % k)
        return out
    uids = [s.user_id for s in reached if s.user_id]
    bio_live = BioPage.query.filter(BioPage.user_id.in_(uids or ['-']),
                                    BioPage.status == BioPage.STATUS_PUBLISHED).count()
    seat_ids = [s.id for s in reached]
    held = CoachingSession.query.filter(CoachingSession.seat_id.in_(seat_ids or ['-']),
                                        CoachingSession.used_credit.is_(True)).count()
    included = sum(s.sessions_total for s in reached)
    out['engagement'] = {
        'ran_simulation': _cell(sum(1 for s in reached if s.sims_used > 0), k),
        'bio_pages_live': _cell(bio_live, k),
        'coaching_sessions_used': held, 'coaching_sessions_included': included,
        'workshop_attendance': None,              # not tracked in phase 1
    }
    # Outcomes: only employees who opted in, latest report each.
    latest = {}
    for o in TransitionOutcome.query.filter(TransitionOutcome.seat_id.in_(seat_ids or ['-'])).order_by(
            TransitionOutcome.reported_at).all():
        latest[o.seat_id] = o
    consent = {s.id for s in reached if s.share_outcomes}
    shared = [o for o in latest.values() if o.share_with_employer and o.seat_id in consent]
    if len(shared) < k:
        out['outcomes'] = {'suppressed': True, 'reported': _cell(len(shared), k)}
    else:
        counts = {key: sum(1 for o in shared if o.outcome == key) for key in TransitionOutcome.OUTCOMES}
        out['outcomes'] = {'suppressed': False, 'reported': len(shared),
                           'by_outcome': [{'outcome': key, 'label': OUTCOME_LABELS[key], 'count': _cell(n, k)}
                                          for key, n in counts.items()]}
    return out


def hr_seat_list(org, cohort_id: str | None = None) -> list:
    """Seat status per person — the one per-person view HR gets. No activity."""
    from app.models.transition import TransitionCohort, TransitionSeat
    q = TransitionSeat.query.filter(TransitionSeat.org_id == org.id)
    if cohort_id:
        q = q.filter(TransitionSeat.cohort_id == cohort_id)
    names = {c.id: c.name for c in TransitionCohort.query.filter_by(org_id=org.id).all()}
    tt = tiers()
    return [{'id': s.id, 'cohort': names.get(s.cohort_id), 'name': s.full_name or '(unassigned)',
             'tier': tt.get(s.tier, {}).get('label', s.tier), 'status': s.status,
             'invited_at': s.invited_at.date().isoformat() if s.invited_at else None,
             'activated_at': s.activated_at.date().isoformat() if s.activated_at else None,
             'expires_at': s.expires_at.date().isoformat() if s.expires_at else None}
            for s in q.order_by(TransitionSeat.created_at).all()]


def seats_csv(org, cohort_id: str | None = None) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(['Cohort', 'Name', 'Tier', 'Status', 'Invited', 'Activated', 'Access ends'])
    for r in hr_seat_list(org, cohort_id):
        w.writerow([r['cohort'], r['name'], r['tier'], r['status'], r['invited_at'] or '',
                    r['activated_at'] or '', r['expires_at'] or ''])
    return buf.getvalue()


def can_view_employer(user, org) -> bool:
    return bool(user.is_authenticated and (getattr(user, 'is_admin', False) or org.admin_user_id == user.id))


# ---------------------------------------------------------------------------
# Coaching (coaches are SMEs with is_coach)
# ---------------------------------------------------------------------------

def coaches(active_only: bool = True) -> list:
    from app.models.sme import SimiSME
    q = SimiSME.query.filter(SimiSME.is_coach.is_(True))
    if active_only:
        q = q.filter(SimiSME.status == SimiSME.STATUS_ACTIVE, SimiSME.coach_status == 'active')
    return q.all()


def _load(coach_id: str) -> int:
    from app.models.transition import TransitionSeat
    return TransitionSeat.query.filter_by(coach_id=coach_id, status=TransitionSeat.ACTIVATED).count()


def match_coach(seat, exclude: str | None = None):
    """Seniority by tier, then specialty overlap with the employee's expertise
    zones, then the lightest caseload. One in ten matches goes to the coach
    with the fewest sessions so newer coaches get a chance (explore/exploit)."""
    import random
    from app.models.profile import UserProfile
    from app.models.transition import CoachingSession
    pool = [c for c in coaches() if c.id != exclude]
    if not pool:
        return None
    senior = tiers().get(seat.tier, {}).get('senior_coach')
    if senior and any(c.coach_seniority == 'senior' for c in pool):
        pool = [c for c in pool if c.coach_seniority == 'senior']
    prof = UserProfile.query.filter_by(user_id=seat.user_id).first() if seat.user_id else None
    zones = set()
    try:
        zones = {str(z.get('category', '')).lower() for z in (prof.canonical_zones or [])} if prof else set()
    except Exception:
        zones = set()

    def overlap(c):
        return len(zones & {s.lower() for s in c.coach_specialties})

    if random.random() < 0.1:
        choice = min(pool, key=lambda c: CoachingSession.query.filter_by(coach_id=c.id).count())
    else:
        choice = sorted(pool, key=lambda c: (-overlap(c), _load(c.id), c.id))[0]
    seat.coach_id = choice.id
    return choice


def change_coach(user_id: str):
    from app.extensions import db
    s = live_seat(user_id)
    if s is None or not s.sessions_total:
        raise TransitionError('Coaching isn\'t part of your package.')
    if s.coach_changes >= 1:
        raise TransitionError('You can ask for a different coach once.')
    new = match_coach(s, exclude=s.coach_id)
    if new is None:
        raise TransitionError('No other coach is available right now; contact simi@simisi.io.')
    s.coach_changes += 1
    s.coach_access = False                        # access was granted to the previous coach
    db.session.commit()
    return new


def set_coach_access(user_id: str, granted: bool) -> None:
    from app.extensions import db
    s = live_seat(user_id) or latest_seat(user_id)
    if s is None:
        raise TransitionError('No transition package on your account.')
    s.coach_access = bool(granted)
    db.session.commit()


def coach_for_login(user):
    from app.models.sme import SimiSME
    c = SimiSME.query.filter_by(auth_user_id=user.id).first() if user.is_authenticated else None
    return c if c is not None and c.is_coach else None


def coach_seat(coach, seat_id: str):
    from app.models.transition import TransitionSeat
    s = TransitionSeat.query.filter_by(id=seat_id, coach_id=coach.id).first()
    if s is None:
        raise TransitionError('Not one of your clients.')
    return s


def log_session(coach, seat_id: str, status: str, scheduled_at=None):
    """The coach records a session. Held sessions use a credit; a no-show uses
    one once the seat's grace is spent; cancellations never do."""
    from app.extensions import db
    from app.models.transition import CoachingSession
    s = coach_seat(coach, seat_id)
    if status not in (CoachingSession.HELD, CoachingSession.NO_SHOW, CoachingSession.CANCELLED,
                      CoachingSession.BOOKED):
        raise TransitionError('Unknown session status.')
    uses = status == CoachingSession.HELD or (
        status == CoachingSession.NO_SHOW and (s.no_shows or 0) >= setting('transition_noshow_grace'))
    if uses and s.sessions_used >= s.sessions_total:
        raise TransitionError('This client has no coaching sessions left.')
    when = scheduled_at
    if isinstance(when, str) and when:
        when = datetime.fromisoformat(when)
    sess = CoachingSession(seat_id=s.id, coach_id=coach.id, scheduled_at=when or datetime.utcnow(),
                           status=status, used_credit=uses)
    if status == CoachingSession.NO_SHOW:
        s.no_shows = (s.no_shows or 0) + 1
    if uses:
        s.sessions_used += 1
    if status == CoachingSession.HELD:
        sess.pay_cents = setting('transition_coach_pay_cents')
    db.session.add(sess)
    db.session.commit()
    return sess


def rate_session(user_id: str, session_id: str, rating: int):
    from app.extensions import db
    from app.models.sme import SimiSME
    from app.models.transition import CoachingSession, TransitionSeat
    sess = CoachingSession.query.get(session_id)
    seat = TransitionSeat.query.get(sess.seat_id) if sess else None
    if sess is None or seat is None or seat.user_id != user_id:
        raise TransitionError('Session not found.')
    if sess.status != CoachingSession.HELD or sess.rating is not None:
        raise TransitionError('Only a held session can be rated, once.')
    rating = int(rating)
    if not 1 <= rating <= 5:
        raise TransitionError('Rate from 1 to 5.')
    sess.rating = rating
    db.session.flush()
    coach = SimiSME.query.get(sess.coach_id)
    rated = [r.rating for r in CoachingSession.query.filter(CoachingSession.coach_id == sess.coach_id,
                                                            CoachingSession.rating.isnot(None)).all()]
    coach.coach_avg_rating = round(sum(rated) / len(rated), 2)
    if len(rated) >= setting('transition_coach_min_ratings') and \
            coach.coach_avg_rating < float(setting('transition_coach_review_rating')):
        coach.coach_status = 'review'             # paused from new matches until reviewed
    db.session.commit()
    return sess


def add_note(author: str, session_id: str, body: str, user=None, coach=None):
    """author 'employee' (user) or 'coach' (coach). Encrypted at rest."""
    from app.extensions import db
    from app.models.transition import CoachingNote, CoachingSession, TransitionSeat
    sess = CoachingSession.query.get(session_id)
    seat = TransitionSeat.query.get(sess.seat_id) if sess else None
    ok = seat is not None and ((author == 'employee' and user is not None and seat.user_id == user.id) or
                               (author == 'coach' and coach is not None and sess.coach_id == coach.id))
    if not ok:
        raise TransitionError('Session not found.')
    body = (body or '').strip()
    if not body:
        raise TransitionError('The note is empty.')
    n = CoachingNote(session_id=sess.id, author=author, body_enc=_encrypt(body[:5000]))
    db.session.add(n)
    db.session.commit()
    return n


def notes_for(session_id: str, user=None, coach=None) -> list:
    from app.models.transition import CoachingNote, CoachingSession, TransitionSeat
    sess = CoachingSession.query.get(session_id)
    seat = TransitionSeat.query.get(sess.seat_id) if sess else None
    if seat is None or not ((user is not None and seat.user_id == user.id) or
                            (coach is not None and sess.coach_id == coach.id)):
        raise TransitionError('Session not found.')
    return [{'author': n.author, 'body': _decrypt(n.body_enc), 'created_at': n.created_at.isoformat()}
            for n in CoachingNote.query.filter_by(session_id=sess.id).order_by(CoachingNote.created_at).all()]


def coach_caseload(coach) -> list:
    """The coach sees a client's simulation summary and bio only with access granted."""
    from app.models.bio_page import BioPage
    from app.models.simulation import Simulation
    from app.models.transition import CoachingSession, TransitionSeat
    out = []
    for s in TransitionSeat.query.filter(TransitionSeat.coach_id == coach.id,
                                         TransitionSeat.status.in_([TransitionSeat.ACTIVATED,
                                                                    TransitionSeat.EXPIRED])).all():
        row = {'seat_id': s.id, 'name': s.full_name, 'tier': tiers().get(s.tier, {}).get('label', s.tier),
               'sessions_used': s.sessions_used, 'sessions_total': s.sessions_total, 'live': s.is_live,
               'expires_at': s.expires_at.date().isoformat() if s.expires_at else None,
               'access': s.coach_access,
               'sessions': [x.to_dict() for x in CoachingSession.query.filter_by(seat_id=s.id).all()]}
        if s.coach_access and s.user_id:
            bp = BioPage.query.filter_by(user_id=s.user_id, status=BioPage.STATUS_PUBLISHED).first()
            row['bio_slug'] = bp.slug if bp else None
            row['simulations'] = [{'name': x.name, 'expertise_zone': x.expertise_zone, 'status': x.status}
                                  for x in Simulation.query.filter_by(user_id=s.user_id).all()]
        out.append(row)
    return out
