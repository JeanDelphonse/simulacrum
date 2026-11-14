"""Founder Track investor connection, founder side (FOUNDER_PLAN.md WP8, PRD
"Investor connection", "Compliance guardrails", "Algorithms to Live By").

Phase 3: behind the founder_investor switch and the counsel sign-off (the
routes check both). Nothing in this module sends an email except
send_update(), and only after a founder approved the update; one-to-one
outreach goes out through founder_autonomy.send().

    record_exemption(venture, user, '506b', note)   the exemption the founder's lawyer chose
    set_consent(venture, user, 'contacts', True)     contacts / mailbox access, one-click revoke
    match(venture)                                   ranked investor_target list, ~37% calibration
    calibration(venture)                             optimal stopping: the bar and the next match to beat it
    add_investor(venture, user, data)                a founder-added investor (investor_profile source 'founder')
    warm_paths(venture)                              DETERMINISTIC connector search over the founder's contacts
    draft_intro(venture, path, user)                 intro request + forwardable blurb (no connector data to the LLM)
    draft_application(venture, target, user)         answers from evaluated artifacts, each flagged without evidence
    draft_update / approve_update / send_update      investor updates, member-scoped unsubscribe

Rules kept here:
  * Nothing L5 runs before the exemption is recorded (require_exemption).
  * Gmail Limited Use: no contact or mail data from an email import reaches an
    LLM. Warm-path search is plain code; draft_intro passes only the target
    firm and the venture's evaluated facts, and the connector's first name is
    inserted after generation.
  * Applications are never auto-submitted at launch, even where the directory
    says the site allows it: the answers are prepared for the founder.
  * Unsubscribes from founder email are member-scoped (suppression_list for the
    venture owner), never the global opt-out.
"""
from __future__ import annotations

import json
import logging
import math
import re
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

AI_CLIENT = None            # tests: a fake with messages.create(**kw)
MAX_TOKENS = 2500
CAL_SHARE = 0.37            # optimal stopping: review ~37% of the list to calibrate
LEARN = 0.2                 # how much founder feedback moves the adjusted score
EXEMPTIONS = ('506b', '506c', 'undecided')
EXEMPTION_LABELS = {
    '506b': 'Rule 506(b)',
    '506c': 'Rule 506(c)',
    'undecided': 'Not decided yet',
}
CONSENT_KINDS = ('mailbox', 'contacts')
TARGET_STATUSES = ('candidate', 'calibration', 'pursuing', 'passed')
UNSUB_SALT = 'fnd-member-unsub'
CONTACT_SOURCE = 'founder_investor'
PATHS_PER_TARGET = 5
ARTIFACT_CHARS = 5000
ARTIFACTS_TOTAL = 16000
FIRST_NAME_TOKEN = '{first_name}'

APPLICATION_QUESTIONS = (
    'What does your company do, in one or two sentences?',
    'What problem are you solving, and for whom?',
    'How do you know customers have this problem?',
    'What have you built so far?',
    'What traction or evidence do you have?',
    'Who is on the founding team and why are you the right team?',
    'How big is the market, and how did you estimate it?',
    'How do you make money?',
)

_ORG_SUFFIX = re.compile(r'\b(inc|llc|ltd|lp|llp|plc|gmbh|co|corp|corporation|company)\b\.?$')
_ANY_GEO = {'global', 'any', 'anywhere', 'worldwide', 'remote', 'all'}


class InvestorError(ValueError):
    """A refusal safe to show. status is the HTTP status a route answers with."""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _now():
    return datetime.utcnow()


def _db():
    from app.extensions import db
    return db


def _log(venture, kind, note='', user_id=None):
    from app.services import founder_ventures
    founder_ventures.log_event(venture.id, kind, note, user_id=user_id)


def _member_user_ids(venture) -> list:
    from app.models.founder import VentureMember
    return [m.user_id for m in VentureMember.query.filter_by(venture_id=venture.id, status='active').all()
            if m.user_id]


def _require_member(venture, user):
    from app.services import founder_ventures
    if founder_ventures.get_member(venture, getattr(user, 'id', None)) is None:
        raise InvestorError('Only the founders of this venture can do that.', 403)


def _holder_uid(venture, decision_type: str):
    from app.services import founder_autonomy
    m = founder_autonomy.approver(venture, decision_type)
    return m.user_id if m is not None else None


# ---------------------------------------------------------------------------
# Exemption and consents (compliance_profile)
# ---------------------------------------------------------------------------

def profile(venture):
    from app.models.founder import ComplianceProfile
    return ComplianceProfile.query.filter_by(venture_id=venture.id).first()


def _profile_row(venture):
    from app.models.founder import ComplianceProfile
    row = profile(venture)
    if row is None:
        row = ComplianceProfile(venture_id=venture.id, exemption='undecided', created_at=_now())
        _db().session.add(row)
    return row


def exemption_recorded(venture) -> bool:
    row = profile(venture)
    return bool(row is not None and row.recorded_at is not None)


def exemption(venture) -> str:
    from app.services import founder_compliance
    return founder_compliance.exemption_for(venture) if exemption_recorded(venture) else 'undecided'


def require_exemption(venture) -> None:
    if not exemption_recorded(venture):
        raise InvestorError('Record which offering exemption your lawyer chose (or that it is not decided yet) '
                            'before any investor step runs.', 409)


def can_record_exemption(venture, user) -> bool:
    uid = getattr(user, 'id', None)
    if not uid:
        return False
    return uid == venture.owner_user_id or uid == _holder_uid(venture, 'legal')


def record_exemption(venture, user, exemption_value: str, note: str = ''):
    """Record the exemption. Only the owner or the holder of the 'legal'
    decision right may. Audit-logged; commits."""
    exemption_value = (exemption_value or '').strip().lower()
    if exemption_value not in EXEMPTIONS:
        raise InvestorError('Choose 506(b), 506(c) or not decided yet.')
    _require_member(venture, user)
    if not can_record_exemption(venture, user):
        raise InvestorError('Only the owner or the co-founder who holds legal decisions can record the exemption.',
                            403)
    note = ' '.join((note or '').split())[:500]
    row = _profile_row(venture)
    previous = row.exemption if row.recorded_at else None
    row.exemption, row.recorded_by, row.recorded_at = exemption_value, user.id, _now()
    from app.models.audit_log import AuditLog
    AuditLog.log('founder_exemption_recorded', user_id=user.id, resource_id=venture.id,
                 metadata={'exemption': exemption_value, 'previous': previous, 'note': note})
    _db().session.commit()
    return row


def consents(venture) -> dict:
    row = profile(venture)
    iso = (lambda d: d.isoformat() if d else None)
    return {'mailbox': iso(row.mailbox_consent_at) if row else None,
            'contacts': iso(row.contacts_consent_at) if row else None}


def contacts_consent(venture) -> bool:
    row = profile(venture)
    return bool(row is not None and row.contacts_consent_at is not None)


def mailbox_consent(venture) -> bool:
    row = profile(venture)
    return bool(row is not None and row.mailbox_consent_at is not None)


def set_consent(venture, user, kind: str, grant: bool):
    """Grant or revoke (one click) access to the owner's contacts or mailbox
    metadata. Only the owner: they are the owner's contacts. Revoking contacts
    removes every warm path found so far and holds unsent intro drafts that
    came from them. Commits."""
    from app.models.audit_log import AuditLog
    from app.models.founder import IntroPath, VentureOutreachAction
    if kind not in CONSENT_KINDS:
        raise InvestorError('Unknown consent.')
    _require_member(venture, user)
    if user.id != venture.owner_user_id:
        raise InvestorError('Only the venture owner can share their own contacts or mailbox.', 403)
    row = _profile_row(venture)
    col = kind + '_consent_at'
    setattr(row, col, _now() if grant else None)
    removed = held = 0
    if not grant:
        removed = IntroPath.query.filter(IntroPath.venture_id == venture.id,
                                         IntroPath.status == 'candidate').delete(synchronize_session=False)
        if kind == 'contacts':
            removed += IntroPath.query.filter_by(venture_id=venture.id).delete(synchronize_session=False)
            for a in VentureOutreachAction.query.filter(
                    VentureOutreachAction.venture_id == venture.id,
                    VentureOutreachAction.action_type == 'intro_request',
                    VentureOutreachAction.status.in_(('drafted', 'awaiting_approval', 'approved')),
                    VentureOutreachAction.target_ref.like('contact:%')).all():
                a.status, a.block_reason = 'blocked', 'Contacts access was turned off'
                held += 1
    AuditLog.log('founder_consent_' + ('granted' if grant else 'revoked'), user_id=user.id, resource_id=venture.id,
                 metadata={'kind': kind, 'paths_removed': removed, 'drafts_held': held})
    _db().session.commit()
    return {'kind': kind, 'granted': bool(grant), 'paths_removed': removed, 'drafts_held': held}


# ---------------------------------------------------------------------------
# Readiness gates (evidence rung, admin settings)
# ---------------------------------------------------------------------------

def _require_rung(venture, which: str) -> None:
    from app.services import founder_config
    need = int(founder_config.get('fnd_gate_rung_intros' if which == 'intros' else 'fnd_gate_rung_applications'))
    if int(venture.rung or 1) < need:
        raise InvestorError('%s open at evidence rung %d (you are on rung %d).'
                            % ('Intro requests' if which == 'intros' else 'Investor matching and applications',
                               need, int(venture.rung or 1)), 409)


# ---------------------------------------------------------------------------
# Matching (investor_profile -> investor_target)
# ---------------------------------------------------------------------------

def _lower_list(values) -> list:
    if isinstance(values, str):
        values = [v for v in re.split(r'[;,|]', values)]
    return [str(v).strip().lower() for v in (values or []) if str(v or '').strip()]


def scope(venture) -> dict:
    """The founder's scope for matching: the Investor Matching agent's scope
    parameter (recommended or overridden), the venture stage by default."""
    raw = {}
    try:
        from app.services import founder_team_service
        raw = founder_team_service.effective_param(venture, 'fnd_investor_matching', 'scope') or {}
    except Exception as exc:
        logger.debug('matching scope fallback: %s', exc)
    if not isinstance(raw, dict):
        raw = {}

    def _money(v):
        try:
            return int(float(v)) if v not in (None, '') else None
        except (TypeError, ValueError):
            return None
    return {'stage': (raw.get('stage') or venture.stage or '').strip().lower(),
            'sectors': _lower_list(raw.get('sectors')), 'geographies': _lower_list(raw.get('geographies')),
            'check_min': _money(raw.get('check_min')), 'check_max': _money(raw.get('check_max'))}


def _usd(n) -> str:
    if n is None:
        return '?'
    n = float(n)
    if n >= 1000000:
        return '$%gM' % round(n / 1000000.0, 2)
    if n >= 1000:
        return '$%gk' % round(n / 1000.0, 1)
    return '$%d' % n


def score(p, sc: dict) -> tuple:
    """(fit 0..1, fits [str], gaps [str]) for one directory entry. Plain,
    explainable weights: stage .35, check size .25, sector .25, geography .15.
    Where either side left a field empty it counts half (unknown, not a miss)."""
    fits, gaps, total = [], [], 0.0
    stages = _lower_list(p.stages)
    if sc['stage'] and stages:
        if sc['stage'] in stages:
            total += .35
            fits.append('Invests at %s' % sc['stage'])
        else:
            gaps.append('Focuses on %s, not %s' % (', '.join(stages), sc['stage']))
    else:
        total += .175
    lo, hi = sc['check_min'], sc['check_max']
    if (lo is not None or hi is not None) and (p.check_min is not None or p.check_max is not None):
        a_lo, a_hi = lo if lo is not None else 0, hi if hi is not None else 10 ** 12
        b_lo, b_hi = p.check_min if p.check_min is not None else 0, p.check_max if p.check_max is not None else 10 ** 12
        if a_lo <= b_hi and b_lo <= a_hi:
            total += .25
            fits.append('Writes checks of %s to %s' % (_usd(p.check_min), _usd(p.check_max)))
        else:
            gaps.append('Check size %s to %s is outside your range' % (_usd(p.check_min), _usd(p.check_max)))
    else:
        total += .125
    sectors = _lower_list(p.sectors)
    if sc['sectors'] and sectors:
        both = [s for s in sc['sectors'] if s in sectors]
        if both:
            total += .25
            fits.append('Backs %s' % ', '.join(both))
        else:
            gaps.append('Sectors: %s' % ', '.join(sectors[:4]))
    else:
        total += .125
    geos = _lower_list(p.geographies)
    if sc['geographies'] and geos:
        both = [g for g in sc['geographies'] if g in geos]
        if both or _ANY_GEO & set(geos):
            total += .15
            fits.append('Invests in %s' % (', '.join(both) if both else 'any region'))
        else:
            gaps.append('Regions: %s' % ', '.join(geos[:4]))
    else:
        total += .075
    if p.accepts_applications and p.apply_url:
        fits.append('Accepts applications through a published form')
    return round(min(1.0, total), 3), fits, gaps


def _profiles_for(venture) -> list:
    """The admin-curated directory plus investors this venture's founders added."""
    from app.models.founder import InvestorProfile
    members = _member_user_ids(venture)
    rows = InvestorProfile.query.all()
    return [p for p in rows if p.source == 'admin' or (p.source == 'founder' and p.added_by in members)]


def calibration_size(n: int) -> int:
    """How many of n ranked matches the founder reviews to calibrate (~37%)."""
    if n < 2:
        return 0
    return max(1, int(math.floor(n * CAL_SHARE + 0.5)))


def match(venture, user=None, commit: bool = True) -> list:
    """Build or refresh the venture's investor_target list from the directory,
    scored against the founder's scope. Targets the founder already decided
    (pursuing / passed) keep their status; the first ~37% of the ranked list
    is marked 'calibration' for review, the rest 'candidate'."""
    from app.models.founder import InvestorTarget
    require_exemption(venture)
    _require_rung(venture, 'applications')
    sc = scope(venture)
    existing = {t.investor_profile_id: t for t in InvestorTarget.query.filter_by(venture_id=venture.id).all()}
    seen = set()
    for p in _profiles_for(venture):
        fit, fits, gaps = score(p, sc)
        t = existing.get(p.id)
        if t is None:
            t = InvestorTarget(venture_id=venture.id, investor_profile_id=p.id, status='candidate', created_at=_now())
            _db().session.add(t)
            existing[p.id] = t
        t.name, t.firm, t.kind = p.name, p.firm, p.kind
        t.fit_score, t.fit_reasons = fit, {'fits': fits, 'gaps': gaps}
        seen.add(p.id)
    for pid, t in list(existing.items()):
        if pid not in seen and t.status in ('candidate', 'calibration'):
            _db().session.delete(t)
            existing.pop(pid)
    ranked = sorted(existing.values(), key=lambda t: (-(t.fit_score or 0), (t.name or '').lower(), t.id or ''))
    k = calibration_size(len(ranked))
    for i, t in enumerate(ranked):
        t.position = i
        if t.status in ('candidate', 'calibration'):
            t.status = 'calibration' if i < k else 'candidate'
    _log(venture, 'investor_match', '%d matches, %d to calibrate' % (len(ranked), k),
         user_id=getattr(user, 'id', None))
    if commit:
        _db().session.commit()
    return ranked


def targets(venture) -> list:
    from app.models.founder import InvestorTarget
    return (InvestorTarget.query.filter_by(venture_id=venture.id)
            .order_by(InvestorTarget.position, InvestorTarget.created_at).all())


def _features(t, profiles: dict) -> set:
    p = profiles.get(t.investor_profile_id)
    out = {'kind:' + (t.kind or 'investor')}
    if p is not None:
        out |= {'stage:' + s for s in _lower_list(p.stages)}
        out |= {'sector:' + s for s in _lower_list(p.sectors)}
        out |= {'geo:' + s for s in _lower_list(p.geographies)}
    return out


def calibration(venture) -> dict:
    """Optimal stopping over the ranked list. The founder reviews the first
    ~37% (pursue or pass). Their choices adjust every match's score (shared
    stages, sectors, regions and kind with what they pursued count up, with
    what they passed count down). The bar is the best adjusted score seen in
    calibration; after calibration the suggestion is the next match in list
    order that beats it."""
    from app.models.founder import InvestorProfile
    rows = targets(venture)
    k = calibration_size(len(rows))
    cal = [t for t in rows if t.position < k]
    reviewed = [t for t in cal if t.status in ('pursuing', 'passed')]
    out = {'size': k, 'total': len(rows), 'reviewed': len(reviewed), 'done': bool(cal) and len(reviewed) == len(cal),
           'bar': None, 'suggestion': None, 'note': ''}
    if not rows:
        out['note'] = 'No matches yet.'
        return out
    ids = [t.investor_profile_id for t in rows if t.investor_profile_id]
    profiles = {p.id: p for p in InvestorProfile.query.filter(InvestorProfile.id.in_(ids or ['-'])).all()}
    liked, disliked = set(), set()
    for t in reviewed:
        (liked if t.status == 'pursuing' else disliked).update(_features(t, profiles))
    disliked -= liked

    def adjusted(t):
        f = _features(t, profiles)
        n = float(max(1, len(f)))
        return round((t.fit_score or 0) + LEARN * len(f & liked) / n - LEARN * len(f & disliked) / n, 4)
    adj = {t.id: adjusted(t) for t in rows}
    out['adjusted'] = adj
    if not out['done']:
        out['note'] = ('Review the first %d match%s (about 37%% of the list) to calibrate what a good fit looks '
                       'like to you. Then SimiSI points you to the next match that beats the best one you saw.'
                       % (k, '' if k == 1 else 'es')) if k else 'Too few matches to calibrate; review them all.'
        return out
    pursued = [t for t in cal if t.status == 'pursuing']
    bar = max(adj[t.id] for t in (pursued or cal))
    out['bar'] = bar
    rest = [t for t in rows if t.position >= k and t.status in ('candidate', 'calibration')]
    better = [t for t in rest if adj[t.id] > bar]
    if better:
        out['suggestion'] = better[0]
        out['note'] = 'The next match that beats the best one you saw while calibrating.'
    elif rest:
        out['suggestion'] = max(rest, key=lambda t: (adj[t.id], -t.position))
        out['note'] = ('No remaining match beats the best one you saw while calibrating. This is the closest; '
                       'it may be worth widening your scope instead.')
    else:
        out['note'] = 'You have reviewed every match.'
    return out


def set_target_status(venture, user, target_id: str, status: str):
    from app.models.founder import InvestorTarget
    _require_member(venture, user)
    require_exemption(venture)
    if status not in ('pursuing', 'passed', 'candidate'):
        raise InvestorError('Choose pursue, pass or undo.')
    t = InvestorTarget.query.filter_by(id=target_id, venture_id=venture.id).first()
    if t is None:
        raise InvestorError('Match not found.', 404)
    if status == 'candidate':
        k = calibration_size(InvestorTarget.query.filter_by(venture_id=venture.id).count())
        status = 'calibration' if t.position < k else 'candidate'
    t.status = status
    _log(venture, 'investor_target_' + status, '%s %s' % (t.id, t.name), user_id=user.id)
    _db().session.commit()
    return t


def _clean_list(value, limit=12) -> list:
    return [s[:60] for s in _lower_list(value)][:limit]


def add_investor(venture, user, data: dict):
    """A founder adds an investor or program they know of. Stored as an
    investor_profile with source 'founder' (visible to this venture only), then
    the list is re-ranked. Returns the new investor_target."""
    from app.models.founder import InvestorProfile, InvestorTarget
    _require_member(venture, user)
    require_exemption(venture)
    data = data or {}
    name = ' '.join(str(data.get('name') or '').split())
    firm = ' '.join(str(data.get('firm') or '').split()) or None
    if not name:
        raise InvestorError('Give the investor or program a name.')
    if len(name) > 200 or len(firm or '') > 200:
        raise InvestorError('Name and firm are 200 characters at most.')
    kind = (data.get('kind') or 'investor').strip().lower()
    if kind not in InvestorProfile.KINDS:
        raise InvestorError('Kind is investor, accelerator or grant.')
    url = (data.get('apply_url') or '').strip()
    if url and (not re.match(r'^https?://\S+$', url, re.I) or len(url) > 500):
        raise InvestorError('The application link must start with http:// or https://.')

    def _money(v):
        if v in (None, ''):
            return None
        try:
            n = int(float(str(v).replace(',', '').replace('$', '')))
        except ValueError:
            raise InvestorError('Check sizes are whole dollar amounts.')
        if n < 0:
            raise InvestorError('Check sizes are whole dollar amounts.')
        return n
    lo, hi = _money(data.get('check_min')), _money(data.get('check_max'))
    if lo is not None and hi is not None and lo > hi:
        raise InvestorError('The smallest check is above the largest.')
    accepts = str(data.get('accepts_applications') or '').lower() in ('1', 'true', 'yes', 'on') and bool(url)
    p = InvestorProfile(name=name, firm=firm, kind=kind, stages=_clean_list(data.get('stages')), check_min=lo,
                        check_max=hi, sectors=_clean_list(data.get('sectors')),
                        geographies=_clean_list(data.get('geographies')), apply_url=url or None,
                        accepts_applications=accepts, auto_submit_allowed=False,
                        notes=(str(data.get('notes') or '')[:2000] or None), source='founder', added_by=user.id,
                        created_at=_now())
    _db().session.add(p)
    _db().session.flush()
    _log(venture, 'investor_added', '%s %s' % (p.id, name), user_id=user.id)
    try:
        match(venture, user, commit=False)
    except InvestorError:
        # Below the matching rung the target is still listed, unranked.
        _db().session.add(InvestorTarget(venture_id=venture.id, investor_profile_id=p.id, name=name, firm=firm,
                                         kind=kind, status='candidate', position=10 ** 6, created_at=_now()))
    _db().session.commit()
    return InvestorTarget.query.filter_by(venture_id=venture.id, investor_profile_id=p.id).first()


# ---------------------------------------------------------------------------
# Warm paths (deterministic: no LLM ever sees these contacts)
# ---------------------------------------------------------------------------

def _norm_org(s) -> str:
    s = re.sub(r'[^a-z0-9 ]+', ' ', (s or '').lower())
    s = ' '.join(s.split())
    prev = None
    while s and s != prev:
        prev = s
        s = _ORG_SUFFIX.sub('', s).strip()
    return s


def _mentions(text, org: str) -> bool:
    if not text or not org or len(org) < 3:
        return False
    return re.search(r'(^| )%s( |$)' % re.escape(org), ' '.join(re.sub(r'[^a-z0-9 ]+', ' ', text.lower()).split())) \
        is not None


def is_email_imported(contact) -> bool:
    """True when the contact came from (or is linked to) the member's mailbox."""
    from app.models.mail_crm import ContactAccountLink
    if (getattr(contact, 'source', '') or '') == 'email_import':
        return True
    return ContactAccountLink.query.filter_by(contact_id=contact.id).first() is not None


def _link_stats(contact_ids) -> dict:
    from app.models.mail_crm import ContactAccountLink
    out = {}
    if not contact_ids:
        return out
    for r in ContactAccountLink.query.filter(ContactAccountLink.contact_id.in_(list(contact_ids))).all():
        s = out.setdefault(r.contact_id, {'sent': 0, 'received': 0, 'last_seen': None})
        s['sent'] += r.sent_count or 0
        s['received'] += r.received_count or 0
        if r.last_seen and (s['last_seen'] is None or r.last_seen > s['last_seen']):
            s['last_seen'] = r.last_seen
    return out


def _replied_ids(contact_ids) -> set:
    from app.models.outreach_email import EmailLog
    if not contact_ids:
        return set()
    return {cid for (cid,) in EmailLog.query.with_entities(EmailLog.contact_id).filter(
        EmailLog.contact_id.in_(list(contact_ids)), EmailLog.replied_at.isnot(None)).distinct().all()}


def _strength(how: str, c, stats: dict, replied: bool, use_mailbox: bool, now) -> tuple:
    """(strength 0..1, evidence list). Match kind first, then how much and how
    recently the founder and the connector have been in touch."""
    base = {'same_firm': .5, 'affiliation': .35}[how]
    evidence = [{'kind': how, 'note': ('Works at the firm (company in your contacts)' if how == 'same_firm'
                                       else 'Title or headline names the firm')}]
    touch = 0.0
    if use_mailbox and stats:
        n = int(stats.get('sent', 0)) + int(stats.get('received', 0))
        touch += min(1.0, math.log1p(n) / math.log1p(50)) * .3
        if stats.get('sent') and stats.get('received'):
            touch += .05
        last = stats.get('last_seen')
        if last is not None:
            days = (now - last).days
            touch += .1 if days <= 90 else (.05 if days <= 365 else 0)
        evidence.append({'kind': 'mailbox', 'sent': int(stats.get('sent', 0)),
                         'received': int(stats.get('received', 0)),
                         'last_seen': last.date().isoformat() if last else None})
    else:
        n = int(getattr(c, 'outreach_count', 0) or 0)
        if n:
            touch += min(1.0, math.log1p(n) / math.log1p(20)) * .15
        last = getattr(c, 'last_contacted_at', None)
        if last is not None:
            touch += .05 if (now - last).days <= 365 else 0
        if n or last:
            evidence.append({'kind': 'crm', 'emails_sent': n,
                             'last_contacted': last.date().isoformat() if last else None})
    if replied:
        touch += .05
        evidence.append({'kind': 'replied'})
    return round(min(1.0, base + touch), 3), evidence


def warm_paths(venture, user=None) -> list:
    """Find who among the owner's contacts could introduce the founders to each
    target. Deterministic code only: company equality with the target firm,
    or the firm named in the contact's title or headline, ranked by how much
    and how recently the founder has been in touch (mailbox metadata from
    contact_account_links only with mailbox consent). Needs contacts consent.
    Replaces earlier 'candidate' paths; drafted ones are kept."""
    from app.models.contact import Contact
    from app.models.founder import IntroPath
    require_exemption(venture)
    if not contacts_consent(venture):
        raise InvestorError('Warm paths need your permission to look through your contacts. Turn on contacts '
                            'access first; you can turn it off in one click.', 409)
    now = _now()
    use_mailbox = mailbox_consent(venture)
    tlist = [t for t in targets(venture) if t.status != 'passed' and _norm_org(t.firm)]
    contacts = (Contact.query.filter(Contact.user_id == venture.owner_user_id, Contact.is_archived == False,  # noqa: E712
                                     Contact.do_not_contact == False).all())  # noqa: E712
    contacts = [c for c in contacts if (c.email or '').strip()]
    stats = _link_stats([c.id for c in contacts]) if use_mailbox else {}
    replied = _replied_ids([c.id for c in contacts])
    IntroPath.query.filter_by(venture_id=venture.id, status='candidate').delete(synchronize_session=False)
    kept = {(p.investor_target_id, p.connector_contact_id) for p in IntroPath.query.filter_by(venture_id=venture.id)}
    made = []
    for t in tlist:
        org = _norm_org(t.firm)
        tname = ' '.join((t.name or '').lower().split())
        found = []
        for c in contacts:
            full = ' '.join(('%s %s' % (c.first_name or '', c.last_name or '')).lower().split())
            if full and full == tname:
                continue                  # the investor themself is not a connector
            if _norm_org(c.company_name) == org:
                how = 'same_firm'
            elif _mentions(c.job_title, org) or _mentions(c.linkedin_headline, org):
                how = 'affiliation'
            else:
                continue
            if (t.id, c.id) in kept:
                continue
            s, ev = _strength(how, c, stats.get(c.id) or {}, c.id in replied, use_mailbox, now)
            found.append((s, c, ev))
        found.sort(key=lambda x: (-x[0], x[1].id))
        for s, c, ev in found[:PATHS_PER_TARGET]:
            p = IntroPath(venture_id=venture.id, investor_target_id=t.id, connector_contact_id=c.id, strength=s,
                          evidence=ev, status='candidate', created_at=now)
            _db().session.add(p)
            made.append(p)
    _log(venture, 'warm_paths', '%d paths across %d targets%s' % (len(made), len(tlist),
                                                                   ' (mailbox metadata used)' if use_mailbox else ''),
         user_id=getattr(user, 'id', None))
    _db().session.commit()
    return paths(venture)


def paths(venture) -> list:
    from app.models.founder import IntroPath
    return (IntroPath.query.filter_by(venture_id=venture.id)
            .order_by(IntroPath.strength.desc(), IntroPath.created_at).all())


# ---------------------------------------------------------------------------
# The model (drafts only; never sees email-imported data)
# ---------------------------------------------------------------------------

def _client():
    if AI_CLIENT is not None:
        return AI_CLIENT
    from app.services import claude
    return claude._client()


def _model() -> str:
    from utils.model_router import MODELS, ModelTier
    return MODELS[ModelTier.SONNET]


def _ask(system: str, prompt: str, tool: dict) -> dict:
    resp = _client().messages.create(model=_model(), max_tokens=MAX_TOKENS, system=system, tools=[tool],
                                     tool_choice={'type': 'tool', 'name': tool['name']},
                                     messages=[{'role': 'user', 'content': [{'type': 'text', 'text': prompt}]}])
    for block in getattr(resp, 'content', None) or []:
        if getattr(block, 'type', None) == 'tool_use' and isinstance(getattr(block, 'input', None), dict):
            return block.input
    raise InvestorError('The draft came back empty.', 502)


def _rules() -> str:
    return '\n'.join([
        'Rules:',
        '- Use only the facts given. Never invent numbers, customers, investors or names.',
        '- Do not negotiate, propose or accept terms, valuations, caps or discounts.',
        '- Do not project investor returns, valuations or the odds of raising.',
        '- Give no legal, tax or investment advice.',
        '- Do not invite the public to invest or suggest posting the raise publicly.',
        '- Plain, honest, short sentences. No hype.',
    ])


def _checked(venture, make, text_of, fallback):
    """Run make(stricter) once, check with founder_compliance, regenerate once
    with stricter rules, fall back to a plain template if it still fails or the
    model is unavailable. Returns (payload, outcome)."""
    from app.services import founder_compliance
    ex = exemption(venture)
    try:
        payload = make(None)
        issues = founder_compliance.check(text_of(payload), 5, exemption=ex)
        if issues:
            payload = make(founder_compliance.stricter_rules(issues))
            issues = founder_compliance.check(text_of(payload), 5, exemption=ex)
            if issues:
                return fallback(), 'fallback'
        return payload, 'ok'
    except InvestorError as exc:
        if exc.status != 502:
            raise
    except Exception as exc:
        logger.error('founder investor draft failed for %s: %s', venture.id, exc)
    return fallback(), 'fallback'


def _founder_name(user) -> str:
    return (getattr(user, 'full_name', '') or '').strip() or 'The founder'


def _page(venture):
    from app.models.founder import StartupPage
    return StartupPage.query.filter_by(venture_id=venture.id).first()


def venture_facts(venture) -> dict:
    """Evaluated facts about the venture, from founder uploads, the evaluation
    and the startup page. Never contact or mailbox data."""
    from app.services import founder_evaluation_service as fes
    page = _page(venture)
    strengths = [f.title for f in fes.findings(venture) if f.ftype == 'strength'][:5]
    facts = {'company': venture.name, 'one_liner': venture.one_liner or '', 'stage': venture.stage,
             'evidence_rung': int(venture.rung or 1), 'strengths': strengths}
    if page is not None:
        for k in ('problem', 'customer', 'solution', 'market', 'business_model'):
            v = getattr(page, k, None)
            if v:
                facts[k] = str(v)[:600]
        facts['founder_stated'] = list(page.founder_stated or [])
        if page.status == 'published':
            facts['public_page'] = '/startup/%s' % page.slug
    return facts


def _level(venture, agent_key: str) -> int:
    from app.services import founder_autonomy
    return founder_autonomy.level_for(venture, agent_key)


# ---------------------------------------------------------------------------
# Intro request
# ---------------------------------------------------------------------------

_INTRO_TOOL = {
    'name': 'deliver_intro',
    'description': 'Return the intro request and the forwardable blurb.',
    'input_schema': {'type': 'object', 'properties': {
        'subject': {'type': 'string'},
        'request': {'type': 'string', 'description': 'The note to the connector. Start with "Hi {first_name}," and '
                                                     'use no other name for them.'},
        'blurb': {'type': 'string', 'description': 'A short third-person note about the company the connector can '
                                                   'forward to the investor.'},
    }, 'required': ['subject', 'request', 'blurb']}}


def draft_intro(venture, path, user):
    """Draft an intro request to the connector and a forwardable blurb. The
    model gets the target firm and the venture's evaluated facts only: no
    connector name, address, company or history. The connector's first name
    replaces {first_name} after generation. The draft waits for approval."""
    from app.extensions import db
    from app.models.contact import Contact
    from app.models.founder import InvestorTarget, VentureOutreachAction
    _require_member(venture, user)
    require_exemption(venture)
    if not contacts_consent(venture):
        raise InvestorError('Contacts access is off, so warm paths are paused.', 409)
    _require_rung(venture, 'intros')
    if path is None or path.venture_id != venture.id:
        raise InvestorError('Warm path not found.', 404)
    target = db.session.get(InvestorTarget, path.investor_target_id)
    c = db.session.get(Contact, path.connector_contact_id)
    if target is None or c is None or c.user_id != venture.owner_user_id:
        raise InvestorError('That warm path is out of date. Find warm paths again.', 409)
    if c.do_not_contact:
        raise InvestorError('That contact asked not to be emailed.', 409)
    founder = _founder_name(user)
    facts = venture_facts(venture)
    who = target.firm or target.name
    prompt = ('<founder>%s</founder>\n<venture_facts>%s</venture_facts>\n<target kind="%s">%s</target>\n\n'
              'Draft a short note from the founder asking a contact who knows people at the target to make an '
              'introduction, and a forwardable blurb (under 120 words) about the company.'
              % (founder, json.dumps(facts, sort_keys=True), target.kind, who))
    system = ('You draft warm-introduction requests for a startup founder on SimiSI. The founder reviews and '
              'approves every message before it goes out under their name.\n' + _rules() +
              '\n- Address the connector only as {first_name}. You are not told who they are.')

    def make(stricter):
        return _ask(system + ('\n\n' + stricter if stricter else ''), prompt, _INTRO_TOOL)

    def text_of(p):
        return '\n'.join(str(p.get(k) or '') for k in ('subject', 'request', 'blurb'))

    def fallback():
        return {'subject': 'Could you introduce me to %s?' % who,
                'request': ('Hi {first_name},\n\nI am building %s (%s). I am getting to know investors at %s and '
                            'saw you are connected there. Would you be open to introducing me? A short note you can '
                            'forward is below.\n\nThanks,\n%s' % (venture.name, venture.one_liner or venture.stage,
                                                                  who, founder)),
                'blurb': '%s: %s. Founder: %s.' % (venture.name, venture.one_liner or venture.stage, founder)}
    payload, outcome = _checked(venture, make, text_of, fallback)
    first = (c.first_name or '').strip() or 'there'
    request = str(payload.get('request') or '')
    if FIRST_NAME_TOKEN in request:
        request = request.replace(FIRST_NAME_TOKEN, first)
    else:
        request = 'Hi %s,\n\n%s' % (first, request)
    body = request.strip() + '\n\n---\nA note you can forward:\n\n' + str(payload.get('blurb') or '').strip()
    a = VentureOutreachAction(venture_id=venture.id, action_type='intro_request',
                              level_required=_level(venture, 'fnd_warm_intro'), status='awaiting_approval',
                              target_ref='contact:%s' % c.id, subject=str(payload.get('subject') or '')[:300] or None,
                              body=body, created_at=_now())
    db.session.add(a)
    db.session.flush()
    path.status = 'drafted'
    path.evidence = list(path.evidence or []) + [{'kind': 'draft', 'action_id': a.id}]
    _log(venture, 'intro_drafted', '%s for %s (%s)' % (a.id, target.id, outcome), user_id=user.id)
    db.session.commit()
    return a


# ---------------------------------------------------------------------------
# Application forms
# ---------------------------------------------------------------------------

_APP_TOOL = {
    'name': 'deliver_answers',
    'description': 'Return one answer per question.',
    'input_schema': {'type': 'object', 'properties': {
        'answers': {'type': 'array', 'items': {'type': 'object', 'properties': {
            'question': {'type': 'string'},
            'answer': {'type': 'string'},
            'evidence': {'type': 'string', 'description': 'A sentence copied exactly from the documents that '
                                                          'supports the answer, or an empty string.'},
        }, 'required': ['question', 'answer', 'evidence']}}}, 'required': ['answers']}}


def _questions(p) -> list:
    qs = [ln.strip()[2:].strip() for ln in (p.notes or '').splitlines() if ln.strip().lower().startswith('q:')]
    return [q for q in qs if q][:15] or list(APPLICATION_QUESTIONS)


def _squash(s) -> str:
    return ' '.join(re.sub(r'[^a-z0-9$%. ]+', ' ', (s or '').lower()).split())


def draft_application(venture, target, user) -> dict:
    """Draft answers to a published application form from the evaluated
    artifacts. Each answer must quote a supporting passage; an answer whose
    quote is missing or not found in the documents is flagged 'needs
    evidence'. Never submitted by SimiSI: the founder submits it on the
    program's site. Returns {'action', 'answers', 'apply_url', 'auto_submit'}."""
    from app.extensions import db
    from app.models.founder import InvestorProfile, VentureOutreachAction
    from app.services import founder_evaluation_service as fes
    _require_member(venture, user)
    require_exemption(venture)
    _require_rung(venture, 'applications')
    if target is None or target.venture_id != venture.id:
        raise InvestorError('Match not found.', 404)
    p = db.session.get(InvestorProfile, target.investor_profile_id) if target.investor_profile_id else None
    if p is None or not p.accepts_applications or not p.apply_url:
        raise InvestorError('There is no published application form on file for this match.', 409)
    questions = _questions(p)
    docs, used = [], 0
    for art in fes.current_artifacts(venture):
        text = (art.text_content or '')[:min(ARTIFACT_CHARS, max(0, ARTIFACTS_TOTAL - used))]
        if not text:
            continue
        used += len(text)
        docs.append((art, text))
    corpus = _squash('\n'.join(t for _a, t in docs))
    prompt = ('<program kind="%s">%s</program>\n<venture_facts>%s</venture_facts>\n%s\n<questions>\n%s\n</questions>'
              % (p.kind, p.firm or p.name, json.dumps(venture_facts(venture), sort_keys=True),
                 '\n'.join('<document kind="%s" version="%d">\n%s\n</document>' % (a.kind, a.version, t)
                           for a, t in docs),
                 '\n'.join('- ' + q for q in questions)))
    system = ('You draft answers to a startup application form for the founder to review and submit themselves.\n'
              + _rules() + '\n- For each answer, copy one supporting sentence exactly from the documents as '
              'evidence, or leave evidence empty when the documents do not support it. Never make one up.')

    def make(stricter):
        return _ask(system + ('\n\n' + stricter if stricter else ''), prompt, _APP_TOOL)

    def text_of(pl):
        return '\n'.join('%s\n%s' % (a.get('question', ''), a.get('answer', ''))
                         for a in (pl.get('answers') or []) if isinstance(a, dict))

    def fallback():
        f = venture_facts(venture)
        base = {0: f.get('one_liner'), 1: f.get('problem'), 3: f.get('solution'), 6: f.get('market'),
                7: f.get('business_model')}
        return {'answers': [{'question': q, 'answer': base.get(i) or '', 'evidence': ''}
                            for i, q in enumerate(questions)]}
    payload, outcome = _checked(venture, make, text_of, fallback)
    answers = []
    for i, q in enumerate(questions):
        got = (payload.get('answers') or [])
        a = got[i] if i < len(got) and isinstance(got[i], dict) else {}
        ans = str(a.get('answer') or '').strip()
        ev = str(a.get('evidence') or '').strip()
        verified = bool(ev) and len(_squash(ev)) >= 12 and _squash(ev) in corpus
        answers.append({'question': q, 'answer': ans, 'evidence': ev if verified else '',
                        'needs_evidence': (not verified) or not ans})
    lines = ['Prepared for %s. SimiSI does not submit applications: open the form, check each answer and submit it '
             'yourself.' % (p.firm or p.name), 'Form: %s' % p.apply_url, '']
    for a in answers:
        lines.append('Q: %s' % a['question'])
        lines.append(a['answer'] or '(No answer drafted.)')
        lines.append('Needs evidence: no supporting passage was found in your documents.' if a['needs_evidence']
                     else 'Source: "%s"' % a['evidence'][:300])
        lines.append('')
    action = VentureOutreachAction(venture_id=venture.id, action_type='application',
                                   level_required=_level(venture, 'fnd_application_form'), status='awaiting_approval',
                                   target_ref='target:%s' % target.id,
                                   subject=('Application: %s' % (p.firm or p.name))[:300],
                                   body='\n'.join(lines).strip(), created_at=_now())
    db.session.add(action)
    _log(venture, 'application_drafted', '%s (%d of %d answers need evidence, %s)' % (
        target.id, sum(1 for a in answers if a['needs_evidence']), len(answers), outcome), user_id=user.id)
    db.session.commit()
    return {'action': action, 'answers': answers, 'apply_url': p.apply_url, 'auto_submit': False}


# ---------------------------------------------------------------------------
# Relationships and recipients (used by founder_autonomy.send)
# ---------------------------------------------------------------------------

def parse_ref(ref) -> tuple:
    """('contact'|'target'|'email'|'linkedin'|'text'|None, value)."""
    ref = (ref or '').strip()
    if not ref:
        return None, ''
    head, _, rest = ref.partition(':')
    head = head.lower()
    if head in ('contact', 'target', 'email', 'linkedin') and rest:
        return head, rest.strip()
    if 'linkedin.com' in ref.lower():
        return 'linkedin', ref
    return 'text', ref


def contact_for(owner_id: str, email: str, name: str = '', company: str = ''):
    """The owner's CRM contact for this address, created (source
    founder_investor) when there is none. Doesn't commit."""
    from sqlalchemy import func
    from app.models.contact import Contact
    e = (email or '').strip().lower()
    c = Contact.query.filter(Contact.user_id == owner_id, func.lower(Contact.email) == e).first()
    if c is not None:
        return c
    parts = (name or '').split()
    first = parts[0] if parts else re.sub(r'[^A-Za-z]+', ' ', e.split('@')[0]).strip().split(' ')[0].title()
    c = Contact(user_id=owner_id, first_name=(first or 'Investor')[:100], last_name=' '.join(parts[1:])[:100],
                email=e[:255], company_name=(company or None), source=CONTACT_SOURCE,
                source_notes='Added by the Founder Track for an investor email')
    _db().session.add(c)
    _db().session.flush()
    return c


def resolve(venture, action) -> dict:
    """Who the action is for: {'kind', 'contact', 'target', 'profile', 'email'}."""
    from app.extensions import db
    from app.models.contact import Contact
    from app.models.founder import InvestorProfile, InvestorTarget
    kind, value = parse_ref(action.target_ref)
    out = {'kind': kind, 'contact': None, 'target': None, 'profile': None, 'email': None, 'value': value}
    if kind == 'contact':
        c = db.session.get(Contact, value)
        if c is not None and c.user_id == venture.owner_user_id:
            out['contact'], out['email'] = c, (c.email or '').strip().lower() or None
    elif kind == 'target':
        t = InvestorTarget.query.filter_by(id=value, venture_id=venture.id).first()
        out['target'] = t
        if t is not None and t.investor_profile_id:
            out['profile'] = db.session.get(InvestorProfile, t.investor_profile_id)
    elif kind == 'email':
        out['email'] = value.lower()
        from sqlalchemy import func
        out['contact'] = Contact.query.filter(Contact.user_id == venture.owner_user_id,
                                              func.lower(Contact.email) == out['email']).first()
    return out


def has_replied(contact) -> bool:
    """The person wrote back: a reply to an email sent through SimiSI, or mail
    from them in the connected mailbox (counts only)."""
    if contact is None:
        return False
    from app.models.mail_crm import ContactAccountLink
    if contact.id in _replied_ids([contact.id]):
        return True
    return ContactAccountLink.query.filter(ContactAccountLink.contact_id == contact.id,
                                           ContactAccountLink.received_count > 0).first() is not None


def relationship(venture, action, rec: dict = None):
    """'published_form' | 'replied' | 'own_contact' | None. Under 506(b) or an
    undecided exemption only these may be reached (existing relationships and
    published application forms)."""
    rec = rec if rec is not None else resolve(venture, action)
    if action.action_type == 'application':
        p = rec.get('profile')
        return 'published_form' if p is not None and p.accepts_applications and p.apply_url else None
    c = rec.get('contact')
    if c is None or c.user_id != venture.owner_user_id:
        return None
    if has_replied(c):
        return 'replied'
    if (c.source or '') != CONTACT_SOURCE:
        return 'own_contact'          # in the founder's CRM before the Founder Track touched it
    return None


def set_recipient(venture, action, user, data: dict):
    """Point a draft at a recipient: one of the owner's contacts, an email
    address, or a LinkedIn profile (copy-only). Only pending or approved,
    unsent drafts."""
    from app.extensions import db
    from app.models.contact import Contact
    _require_member(venture, user)
    if action is None or action.venture_id != venture.id:
        raise InvestorError('Draft not found.', 404)
    if action.status not in ('drafted', 'awaiting_approval', 'approved'):
        raise InvestorError('This draft can no longer change.', 409)
    data = data or {}
    if data.get('contact_id'):
        c = db.session.get(Contact, str(data['contact_id']))
        if c is None or c.user_id != venture.owner_user_id:
            raise InvestorError('Contact not found.', 404)
        ref = 'contact:%s' % c.id
    elif data.get('email'):
        e = str(data['email']).strip().lower()
        if not re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', e) or len(e) > 190:
            raise InvestorError('Enter a valid email address.')
        ref = 'email:%s' % e
    elif data.get('linkedin'):
        ref = 'linkedin:%s' % str(data['linkedin']).strip()[:180]
    else:
        raise InvestorError('Choose a contact, an email address or a LinkedIn profile.')
    action.target_ref = ref[:200]
    _log(venture, 'draft_recipient', '%s -> %s' % (action.id, ref.split(':')[0]), user_id=user.id)
    db.session.commit()
    return action


# ---------------------------------------------------------------------------
# Member-scoped unsubscribe
# ---------------------------------------------------------------------------

def _serializer():
    from flask import current_app
    from itsdangerous import URLSafeSerializer
    return URLSafeSerializer(current_app.config['SECRET_KEY'], salt=UNSUB_SALT)


def unsubscribe_token(venture, email: str) -> str:
    return _serializer().dumps({'u': venture.owner_user_id, 'v': venture.id, 'e': (email or '').strip().lower()})


def unsubscribe_url(venture, email: str) -> str:
    from flask import url_for
    tok = unsubscribe_token(venture, email)
    try:
        return url_for('founder.investor_unsubscribe', token=tok, _external=True)
    except Exception:
        return '/outreach/founder-unsubscribe/%s' % tok


def apply_unsubscribe(token: str):
    """Put the address on the venture owner's own opt-out list (never the
    global one), mark the owner's contacts for it Do Not Contact and stop
    investor updates from this venture. Returns {'email', 'venture'} or None."""
    from itsdangerous import BadSignature
    from sqlalchemy import func
    from app.extensions import db
    from app.models.audit_log import AuditLog
    from app.models.contact import Contact
    from app.models.founder import FounderVenture, InvestorAccess, StartupPage
    from app.models.mail_crm import UserSuppression, email_hash
    try:
        data = _serializer().loads(token)
    except BadSignature:
        return None
    if not isinstance(data, dict) or not data.get('u') or not data.get('e'):
        return None
    uid, e = data['u'], str(data['e']).strip().lower()
    h = email_hash(e)
    if not UserSuppression.query.filter_by(user_id=uid, email_hash=h).first():
        db.session.add(UserSuppression(user_id=uid, email_hash=h, reason='fnd_unsubscribe'))
    for c in Contact.query.filter(Contact.user_id == uid, func.lower(Contact.email) == e).all():
        c.do_not_contact = True
    v = db.session.get(FounderVenture, data.get('v')) if data.get('v') else None
    page = StartupPage.query.filter_by(venture_id=v.id).first() if v is not None else None
    if page is not None:
        for r in InvestorAccess.query.filter(InvestorAccess.startup_page_id == page.id,
                                             func.lower(InvestorAccess.email) == e).all():
            r.opted_in_updates = False
    AuditLog.log('founder_member_unsubscribe', user_id=uid, resource_id=data.get('v'),
                 metadata={'email_hash': h})
    db.session.commit()
    return {'email': e, 'venture': v}


def _html(body: str) -> str:
    from app.services.send_pipeline import _html as to_html
    return to_html(body)


def on_behalf_html(founder_name: str) -> str:
    from html import escape
    from app.services import legal_text
    return ('<p style="font-size:12px;line-height:1.5;color:#68738C;margin-top:20px;">%s</p>'
            % escape(legal_text.get('founder_on_behalf_note', founder_name=founder_name)))


# ---------------------------------------------------------------------------
# Investor updates
# ---------------------------------------------------------------------------

_UPDATE_TOOL = {
    'name': 'deliver_update',
    'description': 'Return the investor update.',
    'input_schema': {'type': 'object', 'properties': {
        'subject': {'type': 'string'},
        'body': {'type': 'string', 'description': 'Plain text, short paragraphs: progress, evidence, metrics, '
                                                  'what is next, one ask for help (not money).'},
    }, 'required': ['subject', 'body']}}


def _last_sent(venture):
    from app.models.founder import InvestorUpdate
    return (InvestorUpdate.query.filter_by(venture_id=venture.id, status='sent')
            .order_by(InvestorUpdate.sent_at.desc()).first())


def update_facts(venture) -> dict:
    """Plan progress, evidence rung changes and chosen metrics since the last
    update that was sent (or the last 30 days)."""
    from app.models.audit_log import AuditLog
    from app.models.founder import VenturePlanStep
    last = _last_sent(venture)
    since = last.sent_at if last is not None and last.sent_at else _now() - timedelta(days=30)
    done = (VenturePlanStep.query.filter(VenturePlanStep.venture_id == venture.id, VenturePlanStep.state == 'done',
                                         VenturePlanStep.completed_at >= since)
            .order_by(VenturePlanStep.completed_at).all())
    rungs = []
    try:
        for r in AuditLog.query.filter(AuditLog.action == 'founder_rung_changed', AuditLog.resource_id == venture.id,
                                       AuditLog.created_at >= since).all():
            rungs.append(((r.extra or {}) if isinstance(r.extra, dict) else {}).get('note') or 'Evidence rung changed')
    except Exception as exc:
        logger.debug('rung history unavailable: %s', exc)
    page = _page(venture)
    metrics = [{'label': m.get('label'), 'value': m.get('value'), 'as_of': m.get('as_of') or None}
               for m in (page.traction or []) if isinstance(m, dict) and m.get('show', True)] if page else []
    return {'company': venture.name, 'since': since.date().isoformat(), 'evidence_rung': int(venture.rung or 1),
            'steps_done': [s.title for s in done][:20], 'rung_changes': rungs[:5], 'metrics': metrics[:10]}


def draft_update(venture, user):
    """Draft an investor update from plan progress, new rungs and the metrics
    the founders chose to show. Stored as a draft; nothing is sent."""
    from app.models.founder import InvestorUpdate
    _require_member(venture, user)
    require_exemption(venture)
    facts = update_facts(venture)
    founder = _founder_name(user)
    prompt = ('<founder>%s</founder>\n<facts>%s</facts>\n\nWrite this period\'s investor update.'
              % (founder, json.dumps(facts, sort_keys=True)))
    system = ('You draft a periodic investor update for a startup founder. The founders approve it before it is '
              'sent to investors who opted in.\n' + _rules() + '\n- State metrics exactly as given, with their dates.')

    def make(stricter):
        return _ask(system + ('\n\n' + stricter if stricter else ''), prompt, _UPDATE_TOOL)

    def fallback():
        lines = ['Hi all,', '', 'Here is what changed at %s since %s.' % (venture.name, facts['since'])]
        if facts['steps_done']:
            lines += ['', 'Done:'] + ['- %s' % t for t in facts['steps_done']]
        if facts['rung_changes']:
            lines += ['', 'Evidence:'] + ['- %s' % t for t in facts['rung_changes']]
        if facts['metrics']:
            lines += ['', 'Metrics:'] + ['- %s: %s%s' % (m['label'], m['value'], ' (as of %s)' % m['as_of']
                                                          if m['as_of'] else '') for m in facts['metrics']]
        lines += ['', 'Thanks,', founder]
        return {'subject': '%s update' % venture.name, 'body': '\n'.join(lines)}
    payload, outcome = _checked(venture, make, lambda p: '%s\n%s' % (p.get('subject', ''), p.get('body', '')),
                                fallback)
    u = InvestorUpdate(venture_id=venture.id, subject=str(payload.get('subject') or '')[:300] or None,
                       body=str(payload.get('body') or ''), status='draft', created_at=_now())
    _db().session.add(u)
    _db().session.flush()
    _log(venture, 'investor_update_drafted', '%s (%s)' % (u.id, outcome), user_id=user.id)
    _db().session.commit()
    return u


def _update_for(venture, update):
    if update is None or update.venture_id != venture.id:
        raise InvestorError('Update not found.', 404)
    return update


def edit_update(venture, update, user, subject: str, body: str):
    from app.services import founder_compliance
    _require_member(venture, user)
    _update_for(venture, update)
    if update.status == 'sent':
        raise InvestorError('This update was already sent.', 409)
    subject, body = ' '.join((subject or '').split())[:300], (body or '').strip()
    if not subject or not body:
        raise InvestorError('An update needs a subject and a body.')
    issues = founder_compliance.check('%s\n%s' % (subject, body), 5, exemption=exemption(venture))
    if issues:
        raise InvestorError('The update breaks a SimiSI rule (%s). Edit it and try again.'
                            % ', '.join(sorted({i['label'] for i in issues})), 422)
    update.subject, update.body = subject, body
    if update.status == 'approved':
        update.status, update.approved_by = 'draft', None       # an edit needs a fresh approval
    _log(venture, 'investor_update_edited', update.id, user_id=user.id)
    _db().session.commit()
    return update


def approve_update(venture, update, user):
    """Updates state traction, so the founder who holds investor contact
    decisions approves each one before it is sent."""
    from app.services import founder_compliance
    _require_member(venture, user)
    _update_for(venture, update)
    if update.status != 'draft':
        raise InvestorError('This update was already %s.' % update.status, 409)
    holder = _holder_uid(venture, 'investor_contact')
    if holder != user.id:
        raise InvestorError('The co-founder who holds investor contact decisions approves updates.', 403)
    issues = founder_compliance.check('%s\n%s' % (update.subject or '', update.body or ''), 5,
                                      exemption=exemption(venture))
    if issues:
        raise InvestorError('The update breaks a SimiSI rule (%s). Edit it first.'
                            % ', '.join(sorted({i['label'] for i in issues})), 422)
    update.status, update.approved_by = 'approved', user.id
    _log(venture, 'investor_update_approved', update.id, user_id=user.id)
    _db().session.commit()
    return update


def update_recipients(venture) -> list:
    """Investors with access to the startup page who opted in to updates or
    were invited by the founders (active access only), one per address."""
    from app.models.founder import InvestorAccess
    page = _page(venture)
    if page is None:
        return []
    out, seen = [], set()
    for r in InvestorAccess.query.filter_by(startup_page_id=page.id, status='active').order_by(
            InvestorAccess.created_at).all():
        e = (r.email or '').strip().lower()
        if e and e not in seen and (r.opted_in_updates or r.kind == 'invited'):
            seen.add(e)
            out.append(e)
    return out


def send_update(venture, update, user) -> dict:
    """Send an approved update, one email per recipient, through the compliant
    send path: founder's name as display name, reply-to the founder, the
    on-behalf label, the update footer and a member-scoped unsubscribe."""
    from app.extensions import db
    from app.models.audit_log import AuditLog
    from app.services import founder_feature, legal_text, send_pipeline
    from app.services.outreach_email_service import send_outreach_email
    _require_member(venture, user)
    _update_for(venture, update)
    if not founder_feature.is_enabled(user, founder_feature.INVESTOR):
        raise InvestorError('Investor connection is not switched on for your account.', 403)
    require_exemption(venture)
    if update.status == 'sent':
        raise InvestorError('This update was already sent.', 409)
    if update.status != 'approved' or not update.approved_by:
        raise InvestorError('A founder needs to approve this update before it is sent.', 409)
    recipients = update_recipients(venture)
    if not recipients:
        raise InvestorError('No investors have opted in to updates yet.', 409)
    try:
        sender = send_pipeline.sender_for(user)
    except send_pipeline.SendRefused as exc:
        raise InvestorError(str(exc), 409)
    founder = _founder_name(user)
    reply_to = (user.email or '').strip() or None
    results = []
    for e in recipients:
        c = contact_for(venture.owner_user_id, e)
        db.session.commit()
        url = unsubscribe_url(venture, e)
        html = (_html(update.body) + on_behalf_html(founder) +
                '<p style="font-size:12px;line-height:1.5;color:#68738C;">%s</p>'
                % _esc(legal_text.get('investor_update_footer', sender_name=founder, unsubscribe_url=url)))
        res = send_outreach_email(simulation_id=venture.id, contact_id=c.id, subject=update.subject or venture.name,
                                  html_body=html, from_email=sender['from_email'], from_name=founder,
                                  action_id=update.id, api_key=sender['api_key'], unsubscribe=True,
                                  reply_to=reply_to, unsubscribe_url=url)
        results.append({'email': e, 'status': res.get('status'), 'reason': res.get('reason')})
    sent = sum(1 for r in results if r['status'] == 'sent')
    update = db.session.get(type(update), update.id)
    update.status, update.sent_at, update.recipient_count = 'sent', _now(), sent
    AuditLog.log('founder_investor_update_sent', user_id=user.id, resource_id=venture.id,
                 metadata={'update_id': update.id, 'sent': sent, 'total': len(results)})
    db.session.commit()
    return {'sent': sent, 'total': len(results), 'results': results}


def _esc(s) -> str:
    from html import escape
    return escape(s or '')


# ---------------------------------------------------------------------------
# Page view
# ---------------------------------------------------------------------------

def _contact_display(c) -> dict:
    if c is None:
        return {'name': 'Contact removed', 'company': '', 'title': ''}
    return {'name': ('%s %s' % (c.first_name or '', c.last_name or '')).strip() or c.email,
            'company': c.company_name or '', 'title': c.job_title or '',
            'from_mailbox': is_email_imported(c)}


def view(venture, user) -> dict:
    """Everything the Investors page shows."""
    from app.extensions import db
    from app.models.contact import Contact
    from app.models.founder import InvestorProfile, InvestorUpdate, VentureOutreachAction
    from app.services import founder_autonomy
    row = profile(venture)
    tl = targets(venture)
    cal = calibration(venture) if tl else {'size': 0, 'total': 0, 'reviewed': 0, 'done': False, 'bar': None,
                                            'suggestion': None, 'note': ''}
    by_target = {t.id: t for t in tl}
    plist = []
    for p in paths(venture):
        t = by_target.get(p.investor_target_id)
        plist.append({'path': p, 'target': t, 'connector': _contact_display(db.session.get(Contact,
                                                                                         p.connector_contact_id))})
    profiles = {p.id: p for p in InvestorProfile.query.filter(
        InvestorProfile.id.in_([t.investor_profile_id for t in tl if t.investor_profile_id] or ['-'])).all()}
    actions = (VentureOutreachAction.query.filter_by(venture_id=venture.id)
               .order_by(VentureOutreachAction.created_at.desc()).limit(100).all())
    investor_types = ('intro_request', 'followup', 'first_contact', 'financial_message', 'application')
    drafts = []
    for a in actions:
        if a.action_type not in investor_types and not str(a.action_type).startswith('negotiat'):
            continue
        lim = founder_autonomy.limits(a.action_type)
        rec = resolve(venture, a)
        if rec['contact'] is not None:
            to = '%s (%s)' % (_contact_display(rec['contact'])['name'], rec['contact'].email)
        elif rec['target'] is not None:
            to = ' / '.join(x for x in (rec['target'].name, rec['target'].firm) if x)
        elif rec['kind'] in ('email', 'linkedin'):
            to = rec['value']
        else:
            to = a.target_ref or ''
        drafts.append({'a': a, 'limits': lim, 'level': a.level_required, 'to': to,
                       'apply_url': rec['profile'].apply_url if rec['profile'] is not None else None,
                       'needs_recipient': rec['kind'] in (None, 'text'),
                       'level_label': founder_autonomy.LEVEL_LABELS.get(a.level_required or 0, ''),
                       'approver': founder_autonomy.approver_name(a),
                       'blocker': founder_autonomy.send_check(a, user) if a.status in ('awaiting_approval', 'approved',
                                                                                        'drafted') else None,
                       'ref': parse_ref(a.target_ref)})
    updates = (InvestorUpdate.query.filter_by(venture_id=venture.id)
               .order_by(InvestorUpdate.created_at.desc()).limit(20).all())
    return {
        'exemption': row.exemption if row and row.recorded_at else None,
        'exemption_label': EXEMPTION_LABELS.get(row.exemption) if row and row.recorded_at else None,
        'exemption_at': row.recorded_at if row else None,
        'can_record': can_record_exemption(venture, user),
        'is_owner': user.id == venture.owner_user_id,
        'consents': consents(venture),
        'targets': tl, 'profiles': profiles, 'calibration': cal,
        'paths': plist, 'drafts': drafts,
        'applications': [d for d in drafts if d['a'].action_type == 'application'],
        'updates': updates, 'recipients': len(update_recipients(venture)),
        'scope': scope(venture), 'page': _page(venture),
    }
