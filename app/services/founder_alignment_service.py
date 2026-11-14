"""Founder Track team alignment (FOUNDER_PLAN.md WP5; PRD "Founding team alignment").

- Co-founder invites: the owner invites a named person by email. The invite is
  a venture_member row (status invited) with a one-time token; /founder/join/<token>
  links the accepting account.
- Alignment check: every active member answers the same six questions on their
  own. An answer is shown to the others only once every active member has
  answered that question. compare() marks each question match or differ.
- Each difference becomes a venture_plan_step (kind alignment). Differences on
  equity and vesting, or on what happens if someone leaves, become a legal step
  telling the founders to take it to a lawyer together.
- Boundaries: SimiSI reports only what each founder stated. It doesn't profile
  personalities, take sides or settle anything.
- Decision rights: who has the final say on each decision type. Only the owner
  or the current holder can reassign one.
- Step ownership: one named owner per plan step; every hand-off is logged as a
  venture_step_event.
- Check-ins: a cadence on the venture and a summary of what's done, blocked and
  waiting on a decision.
"""
from __future__ import annotations

import json
import logging
import secrets
from datetime import datetime, timedelta
from html import escape

logger = logging.getLogger(__name__)

TEAM_LAYER = 4                 # the rubric's Team dimension sits in L4 Operate
NOTE_MAX = 1000
TITLE_MAX = 120
CADENCE_CHOICES = (7, 14, 30)
CADENCE_MAX_DAYS = 90
DEFAULT_SUMMARY_DAYS = 7
INVITE_DAILY_CAP = 10          # invites per venture per 24 hours (fnd_invite_daily_cap overrides)
INVITE_RESEND_MINUTES = 10     # a pending address can't be re-sent sooner than this
ID_MAX = 32
LAYER_LEAD = 'layer_lead'      # decision_right.decision_type for "who leads this layer"

# (key, label, question, legal, options). Options are (value, label). Matching
# compares the chosen option only; notes are shown side by side once revealed.
QUESTIONS = (
    ('vision', 'Vision and goals', 'Where do you want this company to be in five years?', False, (
        ('venture_scale', 'Grow fast with outside investment'),
        ('profitable', 'Grow steadily on revenue and stay independent'),
        ('sell', 'Build it and sell it within a few years'),
        ('unsure', 'Not decided yet'))),
    ('roles', 'Roles', 'How settled is who leads what?', False, (
        ('written', 'Agreed and written down'),
        ('agreed', 'Agreed, but not written down'),
        ('open', 'Still open'))),
    ('time', 'Time commitment', 'What time commitment do you expect from each founder?', False, (
        ('all_full', 'Everyone full time now'),
        ('full_later', 'Full time once there is funding or revenue'),
        ('mixed', 'Some full time, some part time'),
        ('unsure', 'Not discussed yet'))),
    ('equity', 'Equity and vesting', 'How should founder shares be split, and should they vest?', True, (
        ('equal_vest', 'Equal split, with vesting'),
        ('weighted_vest', 'Unequal split (by role, time or money), with vesting'),
        ('no_vest', 'Shares without vesting'),
        ('unsure', 'Not discussed yet'))),
    ('decisions', 'Decision making', 'How should big decisions be made?', False, (
        ('consensus', 'All founders agree'),
        ('lead', 'One founder has the final say, after discussion'),
        ('by_area', 'Each founder decides in their own area'),
        ('unsure', 'Not decided yet'))),
    ('leaving', 'If someone leaves', 'What should happen if a founder leaves?', True, (
        ('return_unvested', 'Unvested shares go back to the company'),
        ('keep_all', 'They keep all their shares'),
        ('decide_then', 'Work it out at the time'),
        ('unsure', 'Not discussed yet'))),
)
QUESTION_KEYS = tuple(q[0] for q in QUESTIONS)
_Q = {q[0]: q for q in QUESTIONS}

DECISION_LABELS = {
    'publish_page': 'Publishing the startup page', 'investor_contact': 'Contacting investors',
    'customer_contact': 'Contacting customers', 'spend': 'Spending money', 'hiring': 'Hiring',
    'product': 'Product decisions', 'legal': 'Legal matters',
}


class AlignmentError(ValueError):
    def __init__(self, message: str = '', status: int = 400):
        super().__init__(message)
        self.status = status


def _id(value):
    """A row id from a request body, or None when it isn't a plausible id."""
    return value if isinstance(value, str) and 0 < len(value) <= ID_MAX else None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _cfg(key: str, default):
    """A founder_config setting when it exists, else the default."""
    try:
        from app.services import founder_config
        value = founder_config.get(key)
        return default if value in (None, '') else value
    except Exception:
        return default


def invite_days() -> int:
    try:
        return max(1, int(_cfg('fnd_invite_days', 30)))
    except (TypeError, ValueError):
        return 30


def invite_daily_cap() -> int:
    try:
        return max(1, int(_cfg('fnd_invite_daily_cap', INVITE_DAILY_CAP)))
    except (TypeError, ValueError):
        return INVITE_DAILY_CAP


def _mask_email(email: str) -> str:
    local, _, domain = (email or '').partition('@')
    return '%s***@%s' % (local[:1], domain) if domain else '***'


def _lock_venture(venture) -> None:
    """Serialize check-then-insert writes per venture (a row lock on databases
    that support it; a no-op on SQLite)."""
    from app.models.founder import FounderVenture
    try:
        FounderVenture.query.filter_by(id=venture.id).with_for_update().first()
    except Exception as exc:
        logger.debug('venture row lock skipped: %s', exc)


def _log(venture_id, kind, note, user_id=None):
    from app.services import founder_ventures
    founder_ventures.log_event(venture_id, kind, note, user_id=user_id)


def _publish(venture_id, event, data):
    try:
        from app.services import founder_stream
        founder_stream.publish(venture_id, event, data, commit=False)
    except Exception as exc:
        logger.debug('founder stream publish skipped: %s', exc)


def _refresh_plan(venture):
    try:
        from app.services import founder_orchestrator
        founder_orchestrator.refresh(venture)
    except (ImportError, AttributeError):
        pass
    except Exception as exc:
        logger.warning('founder plan refresh after alignment change failed: %s', exc)


def active_members(venture) -> list:
    from app.models.founder import VentureMember
    rows = VentureMember.query.filter_by(venture_id=venture.id, status='active').all()
    return sorted(rows, key=lambda m: (m.role != 'owner', m.joined_at or m.created_at or datetime.min, m.id))


def pending_invites(venture) -> list:
    from app.models.founder import VentureMember
    return (VentureMember.query.filter_by(venture_id=venture.id, status='invited')
            .order_by(VentureMember.created_at.asc()).all())


def is_solo(venture) -> bool:
    return len(active_members(venture)) < 2


def _names(members) -> dict:
    from app.extensions import db
    from app.models.user import User
    out = {}
    for m in members:
        u = db.session.get(User, m.user_id) if m.user_id else None
        out[m.id] = ((u.full_name or u.email) if u else None) or m.invite_email or 'Co-founder'
    return out


def member_name(member) -> str:
    return _names([member]).get(member.id) if member else ''


def _member_for(venture, user):
    from app.services import founder_ventures
    m = founder_ventures.get_member(venture, getattr(user, 'id', None))
    if m is None:
        raise AlignmentError('Only members of this venture can do that.')
    return m


def holder_for(venture, decision_type: str, layer=None):
    """The active member with the final say on this decision type (a layer-
    specific right wins over the venture-wide one). Falls back to the owner."""
    from app.extensions import db
    from app.models.founder import DecisionRight, VentureMember
    rows = DecisionRight.query.filter_by(venture_id=venture.id, decision_type=decision_type).all()
    pick = next((r for r in rows if layer is not None and r.layer == layer), None) or \
        next((r for r in rows if r.layer is None), None)
    m = db.session.get(VentureMember, pick.holder_member_id) if pick else None
    if m is not None and m.status == 'active' and m.venture_id == venture.id:
        return m
    return VentureMember.query.filter_by(venture_id=venture.id, role='owner', status='active').first()


# ---------------------------------------------------------------------------
# Invites
# ---------------------------------------------------------------------------

def join_url(token: str) -> str:
    from flask import url_for
    return url_for('founder.tp_join_page', token=token, _external=True)


def _send_invite_email(venture, inviter_name: str, email: str, url: str) -> bool:
    """A transactional invite to one named person the owner knows."""
    try:
        from app.services import email_service as es
        days = invite_days()
        name, inviter = venture.name, inviter_name or 'A founder'
        subject = '%s invited you to join %s on SimiSI' % (inviter, name)
        plain = ('Hi,\n\n%s invited you to join %s as a co-founder on SimiSI\'s Founder Track.\n\n'
                 'You will share the venture\'s evaluation, plan and activity, and each of you answers a short '
                 'alignment check on your own.\n\nAccept: %s\n\nThis link works once and expires in %d days. '
                 'If you don\'t know %s, ignore this email.\n\n- SimiSI' % (inviter, name, url, days, inviter))
        e_inv, e_name = escape(inviter), escape(name)
        html = es._html_wrap(
            es._h1('%s invited you to join %s' % (e_inv, e_name)) +
            es._p('%s invited you to join <b>%s</b> as a co-founder on SimiSI\'s Founder Track.' % (e_inv, e_name)) +
            es._p('You will share the venture\'s evaluation, plan and activity. Each founder answers a short '
                  'alignment check on their own, and no one sees the others\' answers until everyone has answered.') +
            es._btn(url, 'Accept the invite') + es._fallback_link(url) + es._divider() +
            es._p('This link works once and expires in %d days. If you don\'t know %s, ignore this email.'
                  % (days, e_inv), muted=True),
            preheader='%s invited you to join %s.' % (e_inv, e_name),
            footer_html=('SimiSI &middot; Bay Area Experiences Ventures<br>\n'
                         '        You received this because %s invited you by name.' % e_inv))
        es._send(subject=subject, recipients=[email], body=plain, html=html)
        return True
    except Exception as exc:
        logger.error('Failed to send co-founder invite for venture %s: %s', venture.id, exc)
        return False


def invite(venture, user, email: str, title: str = ''):
    """Invite a co-founder by email (owner only). Re-inviting a pending address
    issues a fresh token. Returns (member, email_sent)."""
    from app.extensions import db
    from app.models.founder import VentureMember
    from app.models.user import User
    from utils.validators import ValidationError, validate_email
    me = _member_for(venture, user)
    if me.role != 'owner':
        raise AlignmentError('Only the venture owner can invite co-founders.')
    try:
        email = validate_email(str(email or ''))
    except ValidationError as exc:
        raise AlignmentError(str(exc))
    title = ' '.join(str(title or '').split())[:TITLE_MAX] or None
    if (getattr(user, 'email', '') or '').lower() == email:
        raise AlignmentError("That's your own address.")
    existing_user = User.query.filter(db.func.lower(User.email) == email).first()
    for m in active_members(venture):
        if (m.invite_email or '').lower() == email or (existing_user is not None and m.user_id == existing_user.id):
            raise AlignmentError('That person is already on the team.')
    now = datetime.utcnow()
    try:
        from app.services import suppression_service
        suppressed = suppression_service.check(user.id, email)
    except (ImportError, AttributeError):
        suppressed = None
    if suppressed:
        _log(venture.id, 'cofounder_invite_refused', 'Invite refused: address suppressed (%s)' % suppressed,
             user_id=user.id)
        db.session.commit()
        raise AlignmentError("SimiSI can't email that address (it unsubscribed, bounced or asked not to be "
                             "contacted). Ask them to join another way.", 409)
    _lock_venture(venture)
    m = VentureMember.query.filter_by(venture_id=venture.id, status='invited', invite_email=email).first()
    if m is not None and m.created_at and m.created_at > now - timedelta(minutes=INVITE_RESEND_MINUTES):
        raise AlignmentError('An invite went to that address a few minutes ago. You can send it again after '
                             '%d minutes.' % INVITE_RESEND_MINUTES, 429)
    from app.models.audit_log import AuditLog
    cap = invite_daily_cap()
    sent_today = AuditLog.query.filter(AuditLog.action == 'founder_cofounder_invited',
                                       AuditLog.resource_id == venture.id,
                                       AuditLog.created_at >= now - timedelta(hours=24)).count()
    if sent_today >= cap:
        raise AlignmentError("You've sent %d invites in the last 24 hours. You can send more tomorrow." % cap, 429)
    if m is None:
        m = VentureMember(venture_id=venture.id, invite_email=email, role='cofounder', status='invited',
                          created_at=now)
        db.session.add(m)
    else:
        m.created_at = now                     # the expiry window restarts with the new token
    m.title = title or m.title
    m.invite_token = secrets.token_urlsafe(24)
    db.session.flush()
    _log(venture.id, 'cofounder_invited', 'Co-founder invited (%s)' % m.id, user_id=user.id)
    db.session.commit()
    sent = _send_invite_email(venture, getattr(user, 'full_name', None), email, join_url(m.invite_token))
    return m, sent


def revoke_invite(venture, user, member_id: str):
    from app.extensions import db
    from app.models.founder import VentureMember
    me = _member_for(venture, user)
    if me.role != 'owner':
        raise AlignmentError('Only the venture owner can cancel an invite.')
    m = db.session.get(VentureMember, _id(member_id)) if _id(member_id) else None
    if m is None or m.venture_id != venture.id or m.status != 'invited':
        raise AlignmentError('That invite was not found.')
    m.status = 'left'
    _log(venture.id, 'cofounder_invite_revoked', 'Invite %s cancelled' % m.id, user_id=user.id)
    db.session.commit()
    return m


def resolve_invite(token: str) -> tuple:
    """(member, venture, problem). problem is None for a usable invite, else
    'invalid' | 'used' | 'expired'."""
    from app.extensions import db
    from app.models.founder import FounderVenture, VentureMember
    if not token or len(token) > 64:
        return None, None, 'invalid'
    m = VentureMember.query.filter_by(invite_token=token).first()
    if m is None:
        return None, None, 'invalid'
    v = db.session.get(FounderVenture, m.venture_id)
    if v is None:
        return None, None, 'invalid'
    if m.status == 'active':
        return m, v, 'used'
    if m.status != 'invited':
        return m, v, 'invalid'
    if m.created_at and m.created_at < datetime.utcnow() - timedelta(days=invite_days()):
        return m, v, 'expired'
    return m, v, None


INVITE_PROBLEMS = {
    'invalid': "This invite link isn't valid. It may have been cancelled. Ask the founder to invite you again.",
    'used': 'This invite has already been used.',
    'expired': 'This invite has expired. Ask the founder to send a new one.',
}


def email_matches(member, user) -> bool:
    """Whether the signed-in account is the address the invite went to."""
    return bool(member is not None and member.invite_email and user is not None
                and (getattr(user, 'email', '') or '').strip().lower() == member.invite_email.strip().lower())


def mismatch_message(member) -> str:
    return ('This invite was sent to %s. Sign in with that account to accept it, or ask the founder to invite '
            'the address you use.' % _mask_email(member.invite_email if member else ''))


def accept_invite(token: str, user):
    """Link the signed-in account to the invited member row. The account's
    email must be the invited address, so a forwarded link can't be used by
    someone else."""
    from app.extensions import db
    from app.models.audit_log import AuditLog
    from app.services import founder_feature, founder_ventures
    m, v, problem = resolve_invite(token)
    if problem:
        raise AlignmentError(INVITE_PROBLEMS[problem])
    if founder_ventures.get_member(v, user.id) is not None:
        raise AlignmentError("You're already on this venture's team.")
    if not email_matches(m, user):
        _log(v.id, 'cofounder_join_refused', 'Invite %s opened by a different account' % m.id, user_id=user.id)
        db.session.commit()
        raise AlignmentError(mismatch_message(m), 403)
    if founder_feature.mode(founder_feature.TRACK) == 'off':
        raise AlignmentError('The Founder Track is not available right now.')
    if not founder_feature.is_enabled(user, founder_feature.TRACK):
        # Early access: an invite to this account's own address, from a founder
        # with access, lets the co-founder in too, unless an admin blocked it.
        # Written in this transaction (not founder_feature.set_user_access,
        # which commits on its own) and tagged so admins can see and revoke it.
        from app.models.mail_crm import UserFeatureOverride
        row = UserFeatureOverride.query.filter_by(user_id=user.id, feature_key=founder_feature.TRACK).first()
        if row is not None and row.state == 'blocked':
            raise AlignmentError('The Founder Track is not available on this account.')
        if row is None:
            row = UserFeatureOverride(user_id=user.id, feature_key=founder_feature.TRACK)
            db.session.add(row)
        row.state, row.set_by, row.set_at = 'allowed', v.owner_user_id, datetime.utcnow()
        row.reason = ('Founder Track: co-founder invite to venture %s' % v.id)[:300]
        AuditLog.log('founder_access_via_invite', user_id=v.owner_user_id, resource_id=v.id,
                     metadata={'note': 'Early access granted to %s through invite %s' % (user.id, m.id),
                               'grantee_user_id': user.id, 'member_id': m.id})
    now = datetime.utcnow()
    m.user_id, m.status, m.joined_at = user.id, 'active', now
    _log(v.id, 'cofounder_joined', 'Co-founder joined (%s)' % m.id, user_id=user.id)
    _publish(v.id, 'member_joined', {'actor_user_id': user.id, 'actor_kind': 'founder',
                                     'note': '%s joined the team' % (user.full_name or 'A co-founder')})
    db.session.commit()
    return v


# ---------------------------------------------------------------------------
# Alignment answers
# ---------------------------------------------------------------------------

def _load(row) -> dict:
    try:
        d = json.loads(row.answer or '{}')
    except ValueError:
        d = {}
    return d if isinstance(d, dict) else {}


def _responses(venture) -> dict:
    """{member_id: {question_key: {choice, note}}} for active members only."""
    from app.models.founder import AlignmentResponse
    active = {m.id for m in active_members(venture)}
    out = {}
    for r in AlignmentResponse.query.filter_by(venture_id=venture.id).all():
        if r.member_id in active:
            out.setdefault(r.member_id, {})[r.question_key] = _load(r)
    return out


def _write_answers(venture, me, clean, revealed_before: dict, now) -> list:
    """Insert or update this member's rows. When an answer changes after the
    question was revealed, the answer given at the first check is kept as
    first_choice (PRD metric: agreement at the first check and after follow-up)."""
    from app.extensions import db
    from app.models.founder import AlignmentResponse
    saved = []
    for key, choice, note in clean:
        row = AlignmentResponse.query.filter_by(member_id=me.id, question_key=key).first()
        payload = {'choice': choice, 'note': note}
        if row is None:
            row = AlignmentResponse(venture_id=venture.id, member_id=me.id, question_key=key, created_at=now)
            db.session.add(row)
        else:
            old = _load(row)
            first = old.get('first_choice')
            if first is None and revealed_before.get(key) is not None and old.get('choice') != choice:
                first = old.get('choice')
            if first is not None:
                payload['first_choice'] = first
                payload['changes_after_reveal'] = int(old.get('changes_after_reveal') or 0) + \
                    (1 if old.get('choice') != choice and revealed_before.get(key) is not None else 0)
        row.answer = json.dumps(payload)
        row.updated_at = now
        saved.append(key)
    db.session.flush()
    return saved


def submit_answers(venture, user, answers: dict) -> dict:
    """Save this member's own answers ({key: {choice, note}}). Then turn any
    question everyone has now answered differently into a plan step, and note
    on an open step when its question has come back to a match."""
    from app.extensions import db
    from sqlalchemy.exc import IntegrityError
    me = _member_for(venture, user)
    if not isinstance(answers, dict) or not answers:
        raise AlignmentError('Answer at least one question.')
    clean = []
    for key, val in answers.items():
        if key not in _Q:
            raise AlignmentError('Unknown question.')
        val = val if isinstance(val, dict) else {'choice': val}
        choice = str(val.get('choice') or '').strip()
        if choice not in {o[0] for o in _Q[key][4]}:
            raise AlignmentError('Pick one of the options for "%s".' % _Q[key][1])
        note = ' '.join(str(val.get('note') or '').split())
        if len(note) > NOTE_MAX:
            raise AlignmentError('Keep each note under %d characters.' % NOTE_MAX)
        clean.append((key, choice, note))
    now = datetime.utcnow()
    before = compare(venture)
    member_id = me.id
    try:
        saved = _write_answers(venture, me, clean, before, now)
    except IntegrityError:
        # A concurrent request inserted the same (member, question) row first:
        # start again and update it instead.
        db.session.rollback()
        me = _member_for(venture, user)
        if me.id != member_id:
            raise AlignmentError('Your membership changed. Reload the page and try again.')
        before = compare(venture)
        saved = _write_answers(venture, me, clean, before, now)
    _log(venture.id, 'alignment_answered', '%d alignment answer(s) saved' % len(saved), user_id=user.id)
    after = compare(venture)
    newly_differ = {k for k in QUESTION_KEYS if after.get(k) == 'differ' and before.get(k) != 'differ'}
    now_match = [k for k in QUESTION_KEYS if after.get(k) == 'match' and before.get(k) == 'differ']
    created = sync_steps(venture, user_id=user.id, commit=False, newly_differ=newly_differ)
    for key in now_match:
        _note_now_match(venture, key, user.id)
    db.session.commit()
    if created:
        _refresh_plan(venture)
    return {'saved': saved, 'steps_created': len(created), 'now_match': now_match}


def _open_steps_for(venture, key: str) -> list:
    from app.models.founder import VenturePlanStep
    return VenturePlanStep.query.filter(
        VenturePlanStep.venture_id == venture.id, VenturePlanStep.kind.in_(('alignment', 'legal')),
        VenturePlanStep.title == _step_title(key), ~VenturePlanStep.state.in_(('done', 'skipped'))).all()


def _note_now_match(venture, key: str, user_id=None) -> None:
    """The team's answers to this question match again: say so on its open
    step. The founders still mark it done once they've written it down."""
    from app.extensions import db
    from app.models.founder import VentureStepEvent
    note = 'Your answers now match. Mark this step done once you have written down what you agreed.'
    for step in _open_steps_for(venture, key):
        db.session.add(VentureStepEvent(venture_id=venture.id, plan_step_id=step.id, from_state=step.state,
                                        to_state=step.state, actor_user_id=user_id, actor_kind='system',
                                        note=json.dumps({'event': 'alignment_now_match', 'note': note,
                                                         'question_key': key})))
        _publish(venture.id, 'alignment_now_match', {'plan_step_id': step.id, 'actor_user_id': user_id,
                                                     'actor_kind': 'system', 'note': note})
    _log(venture.id, 'alignment_now_match', 'Answers to %s now match' % key, user_id=user_id)


def agreement_rates(venture) -> dict:
    """Agreement at the first check (answers as they stood when the question
    was revealed) and now, over questions every active member has answered."""
    members = active_members(venture)
    resp = _responses(venture)
    revealed = first = now = 0
    for key in QUESTION_KEYS:
        if len(members) < 2 or any(key not in resp.get(m.id, {}) for m in members):
            continue
        revealed += 1
        cur = {resp[m.id][key].get('choice') for m in members}
        orig = {resp[m.id][key].get('first_choice') or resp[m.id][key].get('choice') for m in members}
        now += 1 if len(cur) == 1 else 0
        first += 1 if len(orig) == 1 else 0
    return {'questions': revealed, 'match_first_check': first, 'match_now': now}


def compare(venture) -> dict:
    """{question_key: 'match' | 'differ' | None}. None until every active
    member (two or more) has answered the question."""
    members = active_members(venture)
    resp = _responses(venture)
    out = {}
    for key in QUESTION_KEYS:
        if len(members) < 2 or any(key not in resp.get(m.id, {}) for m in members):
            out[key] = None
            continue
        choices = {resp[m.id][key].get('choice') for m in members}
        out[key] = 'match' if len(choices) == 1 else 'differ'
    return out


def _step_title(key: str) -> str:
    label = _Q[key][1]
    if _Q[key][3]:
        return '%s: agree it together with a lawyer' % label
    return 'Agree on %s' % label.lower()


def _step_description(key: str) -> str:
    label, question = _Q[key][1], _Q[key][2]
    if _Q[key][3]:
        try:
            from app.services import legal_text
            note = legal_text.get('founder_not_advice')
        except Exception:
            note = ''
        return ('Your answers to "%s" differ. Founder shares, vesting and what happens when someone leaves belong '
                'in a written founder agreement. Take this to a lawyer together and record what you agree. '
                'SimiSI shows what each of you stated; it does not take sides. %s' % (question, note)).strip()
    return ('Your answers to "%s" (%s) differ. Talk it through together and write down what you agree. '
            'SimiSI shows only what each of you stated; it does not take sides or settle it.' % (question, label.lower()))


def _add_step(venture, key: str, title: str, user_id=None):
    """One alignment or legal step: through founder_orchestrator.add_step
    (WP4) when it exists, else a ready row written directly."""
    from app.extensions import db
    from app.models.founder import VenturePlanStep
    legal = _Q[key][3]
    kind, effort = ('legal', 3) if legal else ('alignment', 2)
    sort_order = TEAM_LAYER * 100 + 10 + QUESTION_KEYS.index(key)
    try:
        from app.services import founder_orchestrator
        add = founder_orchestrator.add_step
    except (ImportError, AttributeError):
        add = None
    if add is not None:
        from app.models.user import User
        user = db.session.get(User, user_id) if user_id else None
        return add(venture, TEAM_LAYER, kind, title, description=_step_description(key), prereq_keys=[],
                   effort=effort, sort_order=sort_order, user=user, commit=False)
    step = VenturePlanStep(venture_id=venture.id, layer=TEAM_LAYER, kind=kind, title=title,
                           description=_step_description(key), state='ready', prereq_keys=[], effort=effort,
                           sort_order=sort_order)
    db.session.add(step)
    db.session.flush()
    _publish(venture.id, 'step_added', {'plan_step_id': step.id, 'to_state': 'ready', 'actor_user_id': user_id,
                                        'actor_kind': 'system', 'note': title})
    return step


def sync_steps(venture, user_id=None, commit: bool = True, newly_differ=()) -> list:
    """Create one plan step per question the team answered differently: when
    the question has never had a step, or when it has just moved to differ
    again and no step for it is open. Returns the new steps."""
    from app.extensions import db
    from app.models.founder import VenturePlanStep
    _lock_venture(venture)
    status = compare(venture)
    created = []
    steps = VenturePlanStep.query.filter(
        VenturePlanStep.venture_id == venture.id, VenturePlanStep.kind.in_(('alignment', 'legal'))).all()
    any_title = {s.title for s in steps}
    open_title = {s.title for s in steps if s.state not in ('done', 'skipped')}
    for key in QUESTION_KEYS:
        if status.get(key) != 'differ':
            continue
        title = _step_title(key)
        if title in open_title or (title in any_title and key not in set(newly_differ or ())):
            continue
        step = _add_step(venture, key, title, user_id)
        created.append(step)
        open_title.add(title)
        any_title.add(title)
    if created:
        _log(venture.id, 'alignment_steps', '%d alignment step(s) created' % len(created), user_id=user_id)
    if commit:
        db.session.commit()
    return created


def alignment_view(venture, user) -> dict:
    """What this member may see: their own answers always; others' answers
    only for questions every active member has answered."""
    me = _member_for(venture, user)
    members = active_members(venture)
    names = _names(members + pending_invites(venture))
    resp = _responses(venture)
    status = compare(venture)
    mine = resp.get(me.id, {})
    questions = []
    for key, label, question, legal, options in QUESTIONS:
        opt = dict(options)
        answered = sum(1 for m in members if key in resp.get(m.id, {}))
        revealed = status[key] is not None
        q = {'key': key, 'label': label, 'question': question, 'legal': legal, 'options': list(options),
             'mine': mine.get(key), 'answered': answered, 'total': len(members), 'revealed': revealed,
             'status': status[key], 'answers': []}
        if revealed:
            q['answers'] = [{'member_id': m.id, 'name': names.get(m.id), 'is_me': m.id == me.id,
                             'choice': resp[m.id][key].get('choice'),
                             'choice_label': opt.get(resp[m.id][key].get('choice'), ''),
                             'note': resp[m.id][key].get('note') or ''} for m in members]
        questions.append(q)
    counts = {'match': sum(1 for s in status.values() if s == 'match'),
              'differ': sum(1 for s in status.values() if s == 'differ'),
              'waiting': sum(1 for s in status.values() if s is None)}
    return {'me': me.to_dict(), 'is_owner': me.role == 'owner', 'solo': len(members) < 2,
            'members': [dict(m.to_dict(), name=names.get(m.id)) for m in members],
            'invites': [dict(m.to_dict(), name=names.get(m.id)) for m in pending_invites(venture)],
            'questions': questions, 'counts': counts}


# ---------------------------------------------------------------------------
# Decision rights
# ---------------------------------------------------------------------------

def _layer_names() -> dict:
    try:
        from app.services.founder_registry import LAYER_NAMES
        return dict(LAYER_NAMES)
    except (ImportError, AttributeError):
        return {1: 'Validate', 2: 'Build', 3: 'Sell', 4: 'Operate', 5: 'Capitalize'}


def decision_rights(venture) -> list:
    """Venture-wide final say per decision type, followed by any layer-specific
    rights (holder_for routes those first, so the page shows them too)."""
    from app.models.founder import DecisionRight
    members = active_members(venture)
    names = _names(members)
    owner = next((m for m in members if m.role == 'owner'), None)
    all_rows = DecisionRight.query.filter_by(venture_id=venture.id).all()
    rows = {(r.decision_type, r.layer): r for r in all_rows}
    layers = _layer_names()
    out = []
    for dt in DecisionRight.DECISION_TYPES:
        r = rows.get((dt, None))
        holder = r.holder_member_id if r and r.holder_member_id in names else (owner.id if owner else None)
        out.append({'decision_type': dt, 'label': DECISION_LABELS.get(dt, dt), 'holder_member_id': holder,
                    'holder_name': names.get(holder, ''), 'id': r.id if r else None, 'layer': None})
    for r in sorted((r for r in all_rows if r.layer is not None and r.decision_type in DecisionRight.DECISION_TYPES),
                    key=lambda r: (r.layer, DecisionRight.DECISION_TYPES.index(r.decision_type))):
        holder = r.holder_member_id if r.holder_member_id in names else (owner.id if owner else None)
        out.append({'decision_type': r.decision_type, 'layer': r.layer, 'id': r.id,
                    'label': '%s (L%d %s)' % (DECISION_LABELS.get(r.decision_type, r.decision_type), r.layer,
                                             layers.get(r.layer, '')),
                    'holder_member_id': holder, 'holder_name': names.get(holder, '')})
    return out


def layer_leads(venture) -> list:
    """Who leads each layer (L1-L5). The owner until the team names someone."""
    from app.models.founder import DecisionRight
    members = active_members(venture)
    names = _names(members)
    owner = next((m for m in members if m.role == 'owner'), None)
    rows = {r.layer: r for r in DecisionRight.query.filter_by(venture_id=venture.id, decision_type=LAYER_LEAD).all()}
    out = []
    for layer, label in sorted(_layer_names().items()):
        r = rows.get(layer)
        named = bool(r and r.holder_member_id in names)
        holder = r.holder_member_id if named else (owner.id if owner else None)
        out.append({'layer': layer, 'label': 'L%d %s' % (layer, label), 'holder_member_id': holder,
                    'holder_name': names.get(holder, ''), 'named': named})
    return out


def layer_lead(venture, layer: int):
    """The active member who leads this layer, else the owner."""
    from app.extensions import db
    from app.models.founder import DecisionRight, VentureMember
    r = DecisionRight.query.filter_by(venture_id=venture.id, decision_type=LAYER_LEAD, layer=layer).first()
    m = db.session.get(VentureMember, r.holder_member_id) if r else None
    if m is not None and m.status == 'active' and m.venture_id == venture.id:
        return m
    return VentureMember.query.filter_by(venture_id=venture.id, role='owner', status='active').first()


def reassign_right(venture, user, decision_type: str, to_member_id: str, layer=None):
    """Give the final say on a decision type (or the lead of a layer, with
    decision_type 'layer_lead') to another active member. Only the owner or
    the current holder may do this."""
    from app.extensions import db
    from app.models.founder import DecisionRight, VentureMember
    me = _member_for(venture, user)
    if not isinstance(decision_type, str) or (decision_type not in DecisionRight.DECISION_TYPES
                                              and decision_type != LAYER_LEAD):
        raise AlignmentError('Unknown decision type.')
    if layer not in (None, ''):
        try:
            layer = int(layer)
        except (TypeError, ValueError):
            raise AlignmentError('Unknown layer.')
        if layer < 1 or layer > 5:
            raise AlignmentError('Unknown layer.')
    else:
        layer = None
    if decision_type == LAYER_LEAD and layer is None:
        raise AlignmentError('Choose a layer.')
    target = db.session.get(VentureMember, _id(to_member_id)) if _id(to_member_id) else None
    if target is None or target.venture_id != venture.id or target.status != 'active':
        raise AlignmentError('Choose an active member of the team.')
    row = DecisionRight.query.filter_by(venture_id=venture.id, decision_type=decision_type, layer=layer).first()
    current = layer_lead(venture, layer) if decision_type == LAYER_LEAD else holder_for(venture, decision_type, layer)
    if me.role != 'owner' and (current is None or current.id != me.id):
        raise PermissionError('Only the owner or the person who holds this decision can hand it on.')
    if row is None:
        row = DecisionRight(venture_id=venture.id, decision_type=decision_type, layer=layer,
                            holder_member_id=target.id, created_at=datetime.utcnow())
        db.session.add(row)
    previous = row.holder_member_id if row.id else (current.id if current else None)
    row.holder_member_id = target.id
    what = decision_type if layer is None else '%s L%d' % (decision_type, layer)
    note = '%s: %s -> %s' % (what, previous, target.id)
    _log(venture.id, 'decision_right_changed', note, user_id=user.id)
    _publish(venture.id, 'decision_right_changed', {'actor_user_id': user.id, 'actor_kind': 'founder',
                                                    'decision_type': decision_type, 'layer': layer, 'note': note})
    db.session.commit()
    return row


# ---------------------------------------------------------------------------
# Step ownership and check-ins
# ---------------------------------------------------------------------------

def assign_step(venture, user, plan_step_id: str, member_id):
    """Set a plan step's owner. Every step has one named owner, so an owner
    can be handed on but not cleared. Allowed for the venture owner, the
    step's current owner, or anyone while the step has no owner yet. Logged as
    a venture_step_event hand-off."""
    from app.extensions import db
    from app.models.founder import VentureMember, VenturePlanStep
    me = _member_for(venture, user)
    step = db.session.get(VenturePlanStep, _id(plan_step_id)) if _id(plan_step_id) else None
    if step is None or step.venture_id != venture.id:
        raise AlignmentError('That plan step was not found.')
    if not member_id:
        raise AlignmentError('Every step needs an owner. Choose a member of the team.')
    target = db.session.get(VentureMember, _id(member_id)) if _id(member_id) else None
    if target is None or target.venture_id != venture.id or target.status != 'active':
        raise AlignmentError('Choose an active member of the team.')
    prev = step.owner_member_id
    if me.role != 'owner' and prev not in (None, me.id):
        raise PermissionError('Only the venture owner or the step owner can hand this step on.')
    new = target.id if target else None
    if new == prev:
        return step
    step.owner_member_id = new
    names = _names([m for m in (db.session.get(VentureMember, prev) if prev else None, target) if m])
    note = 'Hand-off: %s -> %s' % (names.get(prev, 'no owner') if prev else 'no owner',
                                   names.get(new, 'no owner') if new else 'no owner')
    from app.models.founder import VentureStepEvent
    db.session.add(VentureStepEvent(venture_id=venture.id, plan_step_id=step.id, from_state=step.state,
                                    to_state=step.state, actor_user_id=user.id, actor_kind='founder',
                                    note=json.dumps({'event': 'step_owner_changed', 'note': note,
                                                     'from_member_id': prev, 'to_member_id': new})))
    _log(venture.id, 'step_handoff', '%s (%s)' % (note, step.id), user_id=user.id)
    db.session.commit()
    return step


def set_cadence(venture, user, days):
    from app.extensions import db
    _member_for(venture, user)
    if days in (None, '', 0, '0'):
        value = None
    else:
        try:
            value = int(days)
        except (TypeError, ValueError):
            raise AlignmentError('The check-in cadence must be a number of days.')
        if value < 1 or value > CADENCE_MAX_DAYS:
            raise AlignmentError('Choose a cadence between 1 and %d days.' % CADENCE_MAX_DAYS)
    venture.checkin_cadence_days = value
    venture.updated_at = datetime.utcnow()
    _log(venture.id, 'checkin_cadence', 'Check-in every %s days' % value if value else 'Check-ins off',
         user_id=user.id)
    db.session.commit()
    return value


def _last_checkin(venture):
    from app.models.audit_log import AuditLog
    row = (AuditLog.query.filter_by(action='founder_checkin_held', resource_id=venture.id)
           .order_by(AuditLog.created_at.desc()).first())
    return row.created_at if row else None


def record_checkin(venture, user):
    from app.extensions import db
    _member_for(venture, user)
    s = checkin_summary(venture)
    _log(venture.id, 'checkin_held', 'Check-in: %d done, %d blocked, %d decisions pending'
         % (len(s['done']), len(s['blocked']), len(s['pending'])), user_id=user.id)
    db.session.commit()


def checkin_summary(venture) -> dict:
    """Done since the last check-in (or the last cadence window), blocked
    steps, and decisions waiting on someone."""
    from app.models.founder import VenturePlanStep
    now = datetime.utcnow()
    last = _last_checkin(venture)
    cadence = venture.checkin_cadence_days
    since = last or (now - timedelta(days=cadence or DEFAULT_SUMMARY_DAYS))
    names = _names(active_members(venture))
    steps = VenturePlanStep.query.filter_by(venture_id=venture.id).order_by(
        VenturePlanStep.layer.asc(), VenturePlanStep.sort_order.asc()).all()

    def row(s):
        return {'id': s.id, 'title': s.title, 'kind': s.kind, 'state': s.state, 'layer': s.layer,
                'owner_member_id': s.owner_member_id, 'owner_name': names.get(s.owner_member_id, '')}
    done = [row(s) for s in steps if s.state == 'done' and s.completed_at and s.completed_at >= since]
    blocked = [row(s) for s in steps if s.state == 'locked']
    pending = [row(s) for s in steps if s.state == 'awaiting_approval'
               or (s.kind in ('alignment', 'legal') and s.state not in ('done', 'skipped'))]
    drafts = 0
    try:
        from app.models.founder import VentureOutreachAction
        drafts = VentureOutreachAction.query.filter_by(venture_id=venture.id, status='awaiting_approval').count()
    except Exception:
        drafts = 0
    open_steps = [row(s) for s in steps if s.state not in ('done', 'skipped')]
    due = (last + timedelta(days=cadence)) if (last and cadence) else None
    return {'cadence_days': cadence, 'last_checkin': last.strftime('%Y-%m-%d') if last else None,
            'next_due': due.strftime('%Y-%m-%d') if due else None, 'overdue': bool(due and due < now),
            'since': since.strftime('%Y-%m-%d'), 'done': done, 'blocked': blocked, 'pending': pending,
            'drafts_awaiting_approval': drafts, 'open_steps': open_steps}
