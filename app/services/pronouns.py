"""The pronouns a member uses, for anything the AI writes about them.

Members choose he/him, she/her or they/them in Settings > Profile, or leave
it unset. Every prompt that writes about a member in the third person adds
prompt_rule(): with a choice it names the pronouns; without one it forbids
inferring gender (from a name, photo or anything else) and asks for the
member's name or they/them.

When a member saves a new choice, fix_existing() rewrites text already
generated about them (bio, simulation layer narratives, income stream
descriptions and reasoning) where it uses other pronouns. The rewrite is a
pronoun-only edit: a guard compares the old and new text word by word and
rejects any change beyond pronouns (and, for they/them, verb agreement).
"""
from __future__ import annotations

import difflib
import logging
import re
import threading

logger = logging.getLogger(__name__)

CHOICES = {
    'he': ('he/him', ('he', 'him', 'his', 'himself')),
    'she': ('she/her', ('she', 'her', 'hers', 'herself')),
    'they': ('they/them', ('they', 'them', 'their', 'theirs', 'themselves')),
}
_ALL = {w for _, ws in CHOICES.values() for w in ws} | {'themself'}
_GENDERED = {'he': {'she', 'her', 'hers', 'herself'},
             'she': {'he', 'him', 'his', 'himself'},
             'they': {'he', 'him', 'his', 'himself', 'she', 'her', 'hers', 'herself'}}
_WORD = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?")
# they/them changes verb agreement: "she is" -> "they are", "he leads" -> "they lead"
_AGREE = {frozenset(p) for p in (('is', 'are'), ('was', 'were'), ('has', 'have'), ('does', 'do'),
                                 ('isn\'t', 'aren\'t'), ('wasn\'t', 'weren\'t'), ('doesn\'t', 'don\'t'),
                                 ('hasn\'t', 'haven\'t'))}
_FIELDS = 'bio, simulation narratives and income stream descriptions'


def normalize(value) -> str:
    v = str(value or '').strip().lower().split('/')[0]
    return v if v in CHOICES else ''


def label(code) -> str:
    return CHOICES[code][0] if code in CHOICES else ''


def for_user(user_id) -> str:
    if not user_id:
        return ''
    try:
        from app.models.profile import UserProfile
        p = UserProfile.query.filter_by(user_id=user_id).first()
        return normalize(getattr(p, 'pronouns', None)) if p else ''
    except Exception:
        return ''


def _first_name(user_id) -> str:
    try:
        from app.models.profile import UserProfile
        from app.models.user import User
        p = UserProfile.query.filter_by(user_id=user_id).first()
        u = User.query.get(user_id)
        name = (p and p.display_name) or (u and u.full_name) or ''
        return name.split()[0] if name.strip() else ''
    except Exception:
        return ''


def prompt_rule(user_id=None, name: str | None = None, code: str | None = None) -> str:
    """One instruction line for any prompt that writes about the member."""
    code = normalize(code) if code is not None else for_user(user_id)
    who = (name or (_first_name(user_id) if user_id else '') or 'the person').strip()
    if code:
        words = CHOICES[code][1]
        return ('PRONOUNS: %s uses %s pronouns. Whenever you refer to %s in the third person, use only '
                '%s. Never use other gendered pronouns for %s.'
                % (who, label(code), who, ', '.join(words), who))
    return ('PRONOUNS: %s has not stated pronouns. Do not infer gender from the name, photo or anything '
            'else. In the third person, refer to %s by name or with they/them; never use he/him or '
            'she/her for %s.' % (who, who, who))


# ── fixing text already written ─────────────────────────────────────────────

def needs_fix(text, code) -> bool:
    if not text or code not in _GENDERED:
        return False
    return any(w.lower() in _GENDERED[code] for w in _WORD.findall(text))


def _tokens(text):
    out = []
    for w in _WORD.findall(text or ''):
        lw = w.lower()
        base = lw.split("'")[0]
        out.append('\x00P' if base in _ALL else lw)
    return out


def safe_rewrite(old: str, new: str, code: str) -> bool:
    """True if `new` differs from `old` only in pronouns (and, for they/them,
    verb agreement), and no longer uses the wrong gendered pronouns."""
    if not new or needs_fix(new, code):
        return False
    a, b = _tokens(old), _tokens(new)
    if a == b:
        return True
    if code != 'they':
        return False
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        if op == 'equal':
            continue
        if op != 'replace' or i2 - i1 != j2 - j1:
            return False
        for x, y in zip(a[i1:i2], b[j1:j2]):
            if frozenset((x, y)) in _AGREE:
                continue
            if x == y + 's' or x == y + 'es' or (x.endswith('ies') and y == x[:-3] + 'y'):
                continue                       # "leads" -> "lead", "carries" -> "carry"
            return False
    return True


_REWRITE = """Rewrite the text below so that every pronoun referring to {name} is {label} \
({words}). Change nothing else: keep every other word, the punctuation, line breaks and \
formatting exactly as they are.{agree} Pronouns that refer to other people stay as they are. \
Output only the rewritten text, with no preface.

TEXT:
{text}"""


def rewrite(text: str, code: str, name: str, client=None) -> str | None:
    """A pronoun-only rewrite, or None when the model's edit fails the guard."""
    from flask import current_app
    from utils.model_router import get_model
    if client is None:
        import anthropic
        client = anthropic.Anthropic(api_key=current_app.config['CLAUDE_API_KEY'])
    prompt = _REWRITE.format(name=name or 'the person', label=label(code), words=', '.join(CHOICES[code][1]),
                             agree=(' Adjust verb agreement only where they/them requires it '
                                    '("she is" becomes "they are").') if code == 'they' else '',
                             text=text)
    resp = client.messages.create(model=get_model('pronoun_rewrite'), max_tokens=min(4000, 200 + len(text)),
                                  temperature=0, messages=[{'role': 'user', 'content': prompt}])
    new = resp.content[0].text.strip()
    if text.endswith('\n') and not new.endswith('\n'):
        new += '\n'
    return new if safe_rewrite(text, new, code) else None


def _targets(user_id):
    """(object, attribute) pairs holding text generated about the member."""
    from app.models.profile import UserProfile
    from app.models.simulation import IncomeStream, Simulation, SimulationLayer
    out = []
    p = UserProfile.query.filter_by(user_id=user_id).first()
    if p:
        out.append((p, 'bio'))
    sim_ids = [s.id for s in Simulation.query.filter_by(user_id=user_id).all()]
    if sim_ids:
        layers = SimulationLayer.query.filter(SimulationLayer.simulation_id.in_(sim_ids)).all()
        for layer in layers:
            out.append((layer, 'ai_narrative'))
        layer_ids = [layer.id for layer in layers]
        if layer_ids:
            for s in IncomeStream.query.filter(IncomeStream.layer_id.in_(layer_ids)).all():
                out.append((s, 'description'))
                out.append((s, 'ai_reasoning'))
    return out


def fix_existing(user_id, code, client=None) -> dict:
    """Rewrite stored text about the member that uses other pronouns.
    Returns counts; never raises."""
    from app.extensions import db
    code = normalize(code)
    stats = {'checked': 0, 'fixed': 0, 'skipped': 0}
    if not code:
        return stats
    name = _first_name(user_id)
    try:
        for obj, attr in _targets(user_id):
            text = getattr(obj, attr, None)
            if not needs_fix(text, code):
                continue
            stats['checked'] += 1
            try:
                new = rewrite(text, code, name, client=client)
            except Exception as exc:
                logger.warning('pronoun rewrite failed for %s.%s: %s', type(obj).__name__, attr, exc)
                new = None
            if new and new != text:
                setattr(obj, attr, new)
                db.session.commit()
                stats['fixed'] += 1
            else:
                stats['skipped'] += 1
    except Exception as exc:
        logger.warning('pronoun fix for user %s stopped: %s', user_id, exc)
        db.session.rollback()
    logger.info('pronoun fix user=%s code=%s %s', user_id, code, stats)
    return stats


def fix_existing_async(app, user_id, code) -> None:
    """Run fix_existing in a background thread (the save returns at once)."""
    def _run():
        with app.app_context():
            fix_existing(user_id, code)
    threading.Thread(target=_run, daemon=True).start()
