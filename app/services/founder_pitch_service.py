"""Founder Track spoken pitch (FOUNDER_PLAN.md WP1).

The founder talks (browser speech-to-text; SimiSI never receives audio) or
types. Claude asks follow-up questions until it can restate the idea, then
offers the restatement. When the founder confirms it, the conversation is
saved as a pitch_transcript artifact through founder_intake_service.

    start(venture, user)                       -> {say, missing}
    turn(venture, user, text, input_mode)      -> {say, restatement?, ready_to_confirm, missing, confirmed?}
    confirm(venture, user, restatement=None)   -> VentureArtifact

This module has its own tool (pitch_turn), validator, prompt and crisis
wording; it does not touch guide_service's guide_turn tool or sessions. It
reuses the guide's crisis-language pattern (CRISIS_RE), cost pricing and its
limit settings (read only): turns and cost per conversation, conversations
per day and cost per month.

Spend is durable: every pitch start and every model call writes an AuditLog
row (founder_pitch_started / founder_pitch_turn with cost_cents), and the
daily and monthly caps are counted from those rows, so starting over does
not reset them.

The in-progress conversation lives in an encrypted draft file in the
venture's private folder (founder_intake_service.files_root(), never inside
the publicly served UPLOAD_FOLDER), one per member. Outside debug/testing it
is never written as plain text. A turn holds a per-draft lock file so two
turns at once cannot overwrite each other. The draft is deleted when the
founder confirms or restarts.

Crisis language pauses the conversation: the founder's message is not kept,
SimiSI's crisis reply is (with its resources), and nothing more can be said
or confirmed until the founder starts over. Crisis turns never reach the
saved transcript.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import datetime

logger = logging.getLogger(__name__)

AI_CLIENT = None        # tests substitute a fake Anthropic client
MODEL_KEY = 'fnd_pitch'  # utils.model_router falls back to Sonnet for unknown keys
PROMPT_VERSION = 'fnd_pitch_v1'
TEXT_MAX = 4000
LOCK_STALE_SECONDS = 180
_SEP = chr(10)            # draft file: "enc" or "plain", a newline, then the body

ELEMENTS = ('problem', 'customer', 'solution', 'market', 'business_model', 'traction', 'team', 'ask')
ELEMENT_LABELS = {'problem': 'Problem', 'customer': 'Customer', 'solution': 'Solution', 'market': 'Market',
                  'business_model': 'How it makes money', 'traction': 'Evidence so far', 'team': 'Team',
                  'ask': 'What you need next'}

OPENING = ("Tell me about your idea in your own words. What problem are you solving, who has it, "
           "and what are you building?")
SAFE_FALLBACK = "Let me make sure I follow. Could you say a little more about who has this problem and how they handle it today?"
CONFIRMED_SAY = "Saved. Your confirmed pitch is now part of your documents, and it will be read with them when you run the evaluation."
CRISIS_TEXT = ("I'm really sorry you're carrying this. I'm going to pause the pitch. You don't have to go through "
               "this alone. If you're in the U.S., you can call or text 988 to reach the Suicide & Crisis Lifeline "
               "any time, and if you're in danger right now, call 911. Your idea will still be here whenever you "
               "want to start over.")
CRISIS_RESOURCES = ({'label': '988 Suicide & Crisis Lifeline (call or text 988, U.S.)', 'href': 'tel:988'},
                    {'label': 'Emergency: 911', 'href': 'tel:911'})
PAUSED_MSG = "The pitch is paused. Whenever you're ready, press Start to begin again."

_AFFIRM_RE = re.compile(r"^\s*(yes|yep|yeah|correct|exactly|that'?s (right|correct|it)|that is (right|correct))"
                        r"[\s.!,]*(that'?s (right|it)|confirm(ed)?)?[\s.!]*$", re.I)

PITCH_TOOL = {
    'name': 'pitch_turn',
    'description': 'Your next turn in the pitch conversation.',
    'input_schema': {
        'type': 'object',
        'properties': {
            'say': {'type': 'string',
                    'description': '1-3 short spoken sentences. At most one question. Plain words.'},
            'restatement': {'type': 'string',
                            'description': 'When ready_to_confirm: the idea restated in 4-7 plain sentences, '
                                           'using only what the founder said. Otherwise empty.'},
            'ready_to_confirm': {'type': 'boolean',
                                 'description': 'True only when you can restate the idea without guessing.'},
            'missing': {'type': 'array', 'items': {'type': 'string', 'enum': list(ELEMENTS)},
                        'description': 'Pitch elements the founder has not covered yet.'},
            'wellbeing': {'type': 'string', 'enum': ['ok', 'stressed', 'crisis']},
        },
        'required': ['say', 'ready_to_confirm', 'missing'],
    },
}


class PitchError(Exception):
    """User-facing refusal."""


class DraftUnreadable(Exception):
    """A draft file exists but cannot be decrypted or parsed."""


# ---------------------------------------------------------------------------
# Gate and limits
# ---------------------------------------------------------------------------

def enabled(user) -> bool:
    from app.services import founder_feature
    return founder_feature.is_enabled(user, founder_feature.PITCH)


def _require_enabled(user) -> None:
    if not enabled(user):
        raise PitchError('The spoken pitch is not available right now. You can still upload your documents.')


def _limit(key: str):
    """Limits shared with the SME Console settings (read only)."""
    from app.services import guide_service
    return guide_service.setting(key)


def _cost_cents(usage) -> float:
    from app.services import guide_service
    try:
        return float(guide_service._cost_cents(usage))
    except Exception:
        return 0.0


def _ledger(user_id: str, action: str, since: datetime) -> list:
    from app.models.audit_log import AuditLog
    return (AuditLog.query.filter(AuditLog.user_id == user_id, AuditLog.action == action,
                                  AuditLog.created_at >= since).all())


def _check_member_limits(user, starting: bool) -> None:
    """The guide's per-member caps, counted from the durable pitch ledger:
    conversations started today, and model spend this month."""
    now = datetime.utcnow()
    if starting:
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        if len(_ledger(user.id, 'founder_pitch_started', today)) >= int(_limit('guide_sessions_per_day')):
            raise PitchError("You've reached today's limit of pitch conversations. Come back tomorrow, "
                             "or upload your documents in the meantime.")
    month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    spent = 0.0
    for row in _ledger(user.id, 'founder_pitch_turn', month):
        try:
            spent += float((row.extra or {}).get('cost_cents') or 0)
        except (TypeError, ValueError):
            continue
    if spent >= float(_limit('guide_cost_cap_month_cents')):
        raise PitchError("You've used this month's pitch conversation time. It resets on the 1st. "
                         "You can still upload your documents.")


def _record(venture, user, action: str, extra: dict) -> None:
    """Write one ledger row and commit it (the draft itself is a file)."""
    from app.extensions import db
    from app.models.audit_log import AuditLog
    try:
        AuditLog.log(action, user_id=user.id, resource_id=venture.id, metadata=extra)
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        logger.error('pitch ledger write failed for venture %s: %s', venture.id, exc)


# ---------------------------------------------------------------------------
# The draft
# ---------------------------------------------------------------------------

def _draft_path(venture, user) -> str:
    from app.services import founder_intake_service
    return os.path.join(founder_intake_service.venture_dir(venture), 'pitch_draft_%s.json' % user.id)


def _plain_allowed() -> bool:
    try:
        from flask import current_app
        return bool(current_app.debug or current_app.testing)
    except Exception:
        return False


def _load(venture, user):
    """The draft, or None when there is none. Raises DraftUnreadable when a
    draft exists but cannot be read (for example after a key rotation)."""
    from app.services.token_crypto import decrypt_token
    path = _draft_path(venture, user)
    if not os.path.exists(path):
        return None
    try:
        with open(path, 'r', encoding='utf-8') as fh:
            head, _, body = fh.read().partition(_SEP)
        if head == 'plain' and not _plain_allowed():
            raise ValueError('plain-text draft refused outside debug/testing')
        if head not in ('enc', 'plain'):
            raise ValueError('unknown draft header')
        draft = json.loads(decrypt_token(body) if head == 'enc' else body)
        if not isinstance(draft, dict) or not isinstance(draft.get('turns'), list):
            raise ValueError('malformed draft')
        return draft
    except Exception as exc:
        logger.error('pitch draft unreadable for venture %s: %s', venture.id, exc)
        raise DraftUnreadable(str(exc))


def _load_or_refuse(venture, user):
    try:
        return _load(venture, user)
    except DraftUnreadable:
        raise PitchError("Your unfinished pitch couldn't be opened. Press Start to begin a new one; "
                         "your uploaded documents are not affected.")


def _save(venture, user, draft: dict) -> None:
    """Write the draft, encrypted with the platform key (token_crypto). Plain
    text is only allowed in debug/testing (production refuses to boot without
    ENCRYPTION_KEY); an unusable key refuses the save."""
    from app.services.token_crypto import encrypt_token
    raw = json.dumps(draft)
    try:
        from flask import current_app
        key = current_app.config.get('ENCRYPTION_KEY')
    except Exception:
        key = None
    body = None
    if key:
        try:
            body = 'enc' + _SEP + encrypt_token(raw)
        except Exception as exc:
            logger.error('pitch draft encryption failed: %s', exc)
    if body is None:
        if not _plain_allowed():
            raise PitchError('The spoken pitch is not available right now. You can still upload your documents.')
        body = 'plain' + _SEP + raw
    path = _draft_path(venture, user)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as fh:
        fh.write(body)
    os.replace(tmp, path)


class _DraftLock:
    """A per-draft lock file held for one turn (including the model call), so
    two turns at once cannot both read-modify-write the draft. A lock older
    than LOCK_STALE_SECONDS (a crashed worker) is taken over."""

    def __init__(self, venture, user):
        self.path = _draft_path(venture, user) + '.lock'
        self.fd = None

    def __enter__(self):
        for _ in range(2):
            try:
                self.fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                return self
            except FileExistsError:
                try:
                    if time.time() - os.path.getmtime(self.path) > LOCK_STALE_SECONDS:
                        os.remove(self.path)
                        continue
                except OSError:
                    continue
                break
        raise PitchError('SimiSI is still answering your last message. Wait a moment, then send again.')

    def __exit__(self, *exc):
        try:
            os.close(self.fd)
        except OSError:
            pass
        try:
            os.remove(self.path)
        except OSError:
            pass
        return False


def discard(venture, user) -> None:
    for suffix in ('', '.tmp'):
        try:
            os.remove(_draft_path(venture, user) + suffix)
        except OSError:
            pass


def _new_draft() -> dict:
    return {'started_at': datetime.utcnow().isoformat(), 'turns': [{'role': 'simi', 'text': OPENING}],
            'restatement': '', 'ready': False, 'missing': list(ELEMENTS), 'cost_cents': 0.0, 'crisis': False}


def state(venture, user) -> dict:
    """The current draft for the page: turns, pending restatement, missing
    elements, and whether it is paused (crisis) or could not be opened."""
    empty = {'active': False, 'turns': [], 'restatement': '', 'ready_to_confirm': False,
             'missing': list(ELEMENTS), 'crisis': False, 'unreadable': False}
    try:
        d = _load(venture, user)
    except DraftUnreadable:
        return dict(empty, unreadable=True)
    if not d:
        return empty
    crisis = bool(d.get('crisis'))
    return {'active': True, 'turns': d['turns'], 'restatement': '' if crisis else (d.get('restatement') or ''),
            'ready_to_confirm': bool(d.get('ready')) and not crisis, 'missing': d.get('missing') or [],
            'crisis': crisis, 'unreadable': False}


# ---------------------------------------------------------------------------
# The model
# ---------------------------------------------------------------------------

def _client():
    if AI_CLIENT is not None:
        return AI_CLIENT
    import anthropic
    from flask import current_app
    return anthropic.Anthropic(api_key=current_app.config['CLAUDE_API_KEY'])


def _model() -> str:
    from utils.model_router import get_model
    return get_model(MODEL_KEY)


def _system_prompt(venture) -> str:
    return (
        "You are SimiSI, listening to a founder pitch their startup idea out loud. Your job is to understand the "
        "idea well enough to restate it accurately, not to judge it.\n\n"
        "Venture name: %s\nStage the founder chose: %s\n\n"
        "How to talk:\n"
        "- Short, warm, spoken sentences. Ask at most one question per turn.\n"
        "- Ask about what is missing, in this order of importance: problem, customer, solution, how it makes "
        "money, evidence so far, market, team, what they need next.\n"
        "- Never invent facts, numbers or customers. Never score, rank or predict funding or returns.\n"
        "- Give no legal, tax or investment advice. If asked, say the evaluation and a professional are the "
        "right places for that, and continue.\n"
        "- When you can restate problem, customer and solution without guessing, set ready_to_confirm, put the "
        "restatement (4-7 plain sentences, only the founder's own claims, numbers as they said them) in "
        "restatement, and in say ask whether you got it right. If they correct you, update the restatement.\n"
        "- If the founder sounds in distress or mentions harming themselves, set wellbeing to crisis.\n"
        "Always answer with the pitch_turn tool."
    ) % (venture.name, venture.stage)


def _messages(draft: dict) -> list:
    msgs = []
    for t in draft['turns']:
        if t.get('crisis'):
            continue
        role = 'user' if t['role'] == 'founder' else 'assistant'
        text = t['text']
        if msgs and msgs[-1]['role'] == role:
            msgs[-1]['content'] += '\n' + text
        else:
            msgs.append({'role': role, 'content': text})
    if msgs and msgs[0]['role'] == 'assistant':
        msgs.insert(0, {'role': 'user', 'content': '[The founder opened the pitch.]'})
    return msgs


def _call(system: str, messages: list):
    resp = _client().messages.create(model=_model(), max_tokens=900, temperature=0.3, system=system,
                                     tools=[PITCH_TOOL], tool_choice={'type': 'tool', 'name': 'pitch_turn'},
                                     messages=messages)
    out = next((b.input for b in resp.content if getattr(b, 'type', '') == 'tool_use'), None)
    return out or {}, getattr(resp, 'usage', None)


def validate_turn(out: dict) -> tuple:
    """(clean, ok). ok is False when there is nothing to say, or the model
    claims to be ready without a restatement."""
    say = re.sub(r'\s+', ' ', str(out.get('say') or '')).strip()[:900]
    restatement = re.sub(r'[ \t]+', ' ', str(out.get('restatement') or '')).strip()[:3000]
    ready = bool(out.get('ready_to_confirm')) and bool(restatement)
    missing = [m for m in (out.get('missing') or []) if m in ELEMENTS]
    clean = {'say': say, 'restatement': restatement if ready else '', 'ready_to_confirm': ready,
             'missing': list(dict.fromkeys(missing)),
             'wellbeing': out.get('wellbeing') if out.get('wellbeing') in ('ok', 'stressed', 'crisis') else 'ok'}
    ok = bool(say) and not (out.get('ready_to_confirm') and not restatement)
    return clean, ok


def _crisis_turn() -> dict:
    return {'say': CRISIS_TEXT, 'restatement': '', 'ready_to_confirm': False, 'missing': [], 'crisis': True,
            'resources': [dict(r) for r in CRISIS_RESOURCES]}


def _pause_for_crisis(venture, user, draft: dict) -> dict:
    """Keep SimiSI's crisis reply (with its resources), drop any pending
    restatement and pause the conversation until the founder starts over."""
    out = _crisis_turn()
    draft['turns'].append({'role': 'simi', 'text': out['say'], 'crisis': True, 'resources': out['resources']})
    draft['crisis'], draft['ready'], draft['restatement'] = True, False, ''
    _save(venture, user, draft)
    return out


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def start(venture, user) -> dict:
    """Begin a fresh pitch conversation (discarding any unconfirmed draft).
    Counts toward the member's daily conversation limit."""
    _require_enabled(user)
    _check_member_limits(user, starting=True)
    with _DraftLock(venture, user):
        _save(venture, user, _new_draft())
    _record(venture, user, 'founder_pitch_started', {'venture_id': venture.id})
    return {'say': OPENING, 'restatement': '', 'ready_to_confirm': False, 'missing': list(ELEMENTS)}


def turn(venture, user, text: str, input_mode: str = 'typed') -> dict:
    """One founder turn. Returns {say, restatement, ready_to_confirm, missing}
    plus confirmed/artifact when the founder simply says yes to the
    restatement SimiSI offered on its last turn, or crisis/resources on crisis
    language."""
    from app.services.guide_service import CRISIS_RE
    _require_enabled(user)
    text = re.sub(r'\s+', ' ', str(text or '')).strip()
    if not text:
        raise PitchError('Say or type something first.')
    text = text[:TEXT_MAX]
    mode = input_mode if input_mode in ('voice', 'typed') else 'typed'
    with _DraftLock(venture, user):
        draft = _load_or_refuse(venture, user)
        if draft is None:
            # No conversation yet: this opens one, so it counts like start().
            _check_member_limits(user, starting=True)
            draft = _new_draft()
            _record(venture, user, 'founder_pitch_started', {'venture_id': venture.id})
        if draft.get('crisis'):
            raise PitchError(PAUSED_MSG)

        # A plain "yes" to the restatement SimiSI offered on its last turn confirms
        # it, so a spoken pitch needs no click.
        last = draft['turns'][-1] if draft['turns'] else {}
        if (draft.get('ready') and draft.get('restatement') and last.get('role') == 'simi'
                and last.get('offered') and _AFFIRM_RE.match(text)):
            draft['turns'].append({'role': 'founder', 'text': text, 'mode': mode})
            art = _confirm_draft(venture, user, draft, None)
            return {'say': CONFIRMED_SAY, 'restatement': draft['restatement'], 'ready_to_confirm': False,
                    'missing': draft.get('missing') or [], 'confirmed': True, 'artifact': art.to_dict()}

        # Crisis language: pause before any model call. The founder's words are not kept.
        if CRISIS_RE.search(text):
            return _pause_for_crisis(venture, user, draft)

        founder_turns = sum(1 for t in draft['turns'] if t['role'] == 'founder')
        if founder_turns >= int(_limit('guide_turns_per_session')):
            raise PitchError('This pitch conversation has reached its length limit. Confirm the restatement or start again.')
        if float(draft.get('cost_cents') or 0) >= float(_limit('guide_cost_cap_session_cents')):
            raise PitchError('This pitch conversation has reached its limit. Confirm the restatement or start again.')
        _check_member_limits(user, starting=False)

        draft['turns'].append({'role': 'founder', 'text': text, 'mode': mode})
        system = _system_prompt(venture)
        msgs = _messages(draft)
        clean, ok, spent = None, False, 0.0
        for _attempt in range(1 + max(0, int(_limit('guide_regen_attempts')))):
            try:
                out, usage = _call(system, msgs)
            except Exception as exc:
                logger.error('pitch model call failed for venture %s: %s', venture.id, exc)
                out, usage = {}, None
            spent += _cost_cents(usage)
            clean, ok = validate_turn(out)
            if ok:
                break
            msgs = msgs + [{'role': 'assistant', 'content': json.dumps(out)[:1500]},
                           {'role': 'user', 'content': '[System check: answer with a short spoken turn. If you set '
                                                       'ready_to_confirm, include the restatement.]'}]
        draft['cost_cents'] = float(draft.get('cost_cents') or 0) + spent
        _record(venture, user, 'founder_pitch_turn', {'venture_id': venture.id, 'cost_cents': round(spent, 4)})
        if not ok:
            clean = {'say': SAFE_FALLBACK, 'restatement': '', 'ready_to_confirm': False,
                     'missing': draft.get('missing') or list(ELEMENTS), 'wellbeing': 'ok', 'fallback': True}
        if clean.get('wellbeing') == 'crisis':
            draft['turns'].pop()          # the founder's words in a crisis are not kept
            return _pause_for_crisis(venture, user, draft)
        clean.pop('wellbeing', None)
        # The pending restatement always reflects SimiSI's latest turn: a turn that
        # doesn't restate withdraws any earlier offer.
        draft['ready'] = bool(clean['ready_to_confirm'])
        draft['restatement'] = clean['restatement'] if draft['ready'] else ''
        draft['missing'] = clean['missing']
        draft['turns'].append({'role': 'simi', 'text': clean['say'], 'offered': draft['ready']})
        _save(venture, user, draft)
        return clean


def _norm(text: str) -> str:
    return re.sub(r'\s+', ' ', text or '').strip()


def _transcript_text(venture, user, draft: dict, edited: str = '') -> str:
    """The saved transcript. SimiSI's restatement and any founder edit are kept
    as separate, labelled sections. Crisis turns are never included."""
    who = getattr(user, 'full_name', None) or 'the founder'
    lines = ['Pitch transcript: %s' % venture.name,
             'Recorded %s UTC with %s. Spoken through the browser or typed; SimiSI received text only.'
             % (datetime.utcnow().strftime('%Y-%m-%d %H:%M'), who), '']
    if edited:
        lines += ['SimiSI restatement', draft['restatement'], '',
                  'Founder edits (the version the founder confirmed)', edited]
    else:
        lines += ['SimiSI restatement, confirmed by the founder without changes', draft['restatement']]
    lines += ['', 'Conversation']
    for t in draft['turns']:
        if t.get('crisis'):
            continue
        lines.append('%s: %s' % ('Founder' if t['role'] == 'founder' else 'SimiSI', t['text']))
    return '\n'.join(lines)


def _confirm_draft(venture, user, draft: dict, restatement):
    from app.services import founder_intake_service, founder_ventures
    if draft.get('crisis'):
        raise PitchError(PAUSED_MSG)
    if not any(t['role'] == 'founder' for t in draft['turns']):
        raise PitchError('There is no pitch to confirm yet. Start by telling SimiSI about your idea.')
    if not (draft.get('ready') and draft.get('restatement')):
        raise PitchError('SimiSI has not restated your idea yet. Keep talking it through first.')
    edited = re.sub(r'[ \t]+', ' ', str(restatement or '')).strip()[:3000]
    if _norm(edited) == _norm(draft['restatement']):
        edited = ''
    art = founder_intake_service.add_transcript(venture, _transcript_text(venture, user, draft, edited), user)
    founder_ventures.log_event(venture.id, 'pitch_confirmed', 'pitch_transcript v%d%s, model cost %.2f cents'
                               % (art.version, ' (founder edited)' if edited else '',
                                  float(draft.get('cost_cents') or 0)), user_id=user.id)
    from app.extensions import db
    db.session.commit()
    discard(venture, user)
    return art


def confirm(venture, user, restatement: str | None = None):
    """Save the confirmed pitch as a pitch_transcript artifact. Only possible
    once SimiSI has offered a restatement on its latest turn (and the pitch is
    not paused). The founder may send an edited version: it is saved beside
    SimiSI's restatement, labelled as the founder's edits. Raises PitchError
    otherwise."""
    _require_enabled(user)
    with _DraftLock(venture, user):
        draft = _load_or_refuse(venture, user)
        if not draft:
            raise PitchError('There is no pitch to confirm yet. Start by telling SimiSI about your idea.')
        return _confirm_draft(venture, user, draft, restatement)
