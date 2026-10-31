"""Bio page writing checks (Bio Page Enhancements: hype checker, lead paragraph).

Owner-facing only, and advisory: nothing here blocks publishing or rewrites the
owner's text. The hype list is the single source for both the bio writer's
prohibited-language rule (bio_service.BIO_SYSTEM_PROMPT) and the checker, so
what the generator avoids and what the owner is warned about never drift.
"""
from __future__ import annotations

import re

# term -> what to do instead. Matched as whole words, case-insensitive.
HYPE_TERMS = {
    'world-class': 'name the standard or result, e.g. "led a 40-person data migration at JPMorgan Chase"',
    'visionary': 'say what was built or changed, and for whom',
    'passionate': 'show it with a fact: years in the field, work shipped',
    'results-driven': 'state one result with a number',
    'dynamic': 'drop it, or replace with what the person actually does',
    'innovative': 'name the new thing and who uses it',
    'thought leader': 'cite a talk, article or book instead',
    'guru': 'state the expertise and its evidence',
    'ninja': 'state the skill plainly',
    'rockstar': 'state the skill plainly',
    'game-changer': 'say what changed, with a before and after',
    'game-changing': 'say what changed, with a before and after',
    'disruptive': 'say what it replaced',
    'synergy': 'say what the combination produced',
    'cutting-edge': 'name the technology',
    'best-in-class': 'name the comparison and the measure',
    'seasoned': 'give the years and the roles',
}
_LEVERAGE_VERB = re.compile(r"\bleverag(?:e|es|ed|ing)\b", re.I)
_SECTION = re.compile(r'\*\*[^*]+\*\*')
_SENTENCE = re.compile(r'(?<=[.!?])\s+')


def prohibited_terms_for_prompt() -> str:
    """The comma list the bio writer is told never to use."""
    return ', '.join(list(HYPE_TERMS) + ['leverage (as a verb)'])


def _snippet(text: str, start: int, end: int, pad: int = 40) -> str:
    a, b = max(0, start - pad), min(len(text), end + pad)
    return ('…' if a else '') + text[a:b].replace('\n', ' ').strip() + ('…' if b < len(text) else '')


def find_hype(fields: dict) -> list:
    """[{term, field, snippet, suggestion}] for every hype term in the given
    {field_label: text} map. One finding per term per field."""
    findings = []
    for label, text in (fields or {}).items():
        if not text:
            continue
        text = str(text)
        for term, suggestion in HYPE_TERMS.items():
            m = re.search(r'(?<![\w-])' + re.escape(term) + r'(?![\w-])', text, re.I)
            if m:
                findings.append({'term': term, 'field': label, 'suggestion': suggestion,
                                 'snippet': _snippet(text, m.start(), m.end())})
        m = _LEVERAGE_VERB.search(text)
        if m:
            findings.append({'term': m.group(0).lower(), 'field': label,
                             'suggestion': 'use "used" or "applied", or say what it enabled',
                             'snippet': _snippet(text, m.start(), m.end())})
    return findings


def lead_of(about_text: str) -> str:
    """The opening paragraph: text before the first **Section** marker or blank line."""
    text = (about_text or '').strip()
    m = _SECTION.search(text)
    if m:
        text = text[:m.start()]
    return text.strip().split('\n\n')[0].strip()


def lead_tip(about_text: str, full_name: str, title: str = '') -> str | None:
    """Why the opening doesn't yet answer "who is this?", or None if it does.

    Heuristic, deliberately lenient: the first sentence should name the person
    (or start with their role), the lead should exist before the first section,
    and it should be 1-3 sentences rather than a wall of text.
    """
    if not (about_text or '').strip():
        return None
    lead = lead_of(about_text)
    if not lead:
        return 'Your About text starts with a section. Open with 2–3 sentences: who you are, what you do, and why it matters.'
    sentences = [s for s in _SENTENCE.split(lead) if s.strip()]
    first = sentences[0] if sentences else lead
    names = [p for p in re.split(r'\s+', (full_name or '').strip()) if len(p) > 1]
    title_words = [w for w in re.findall(r'[A-Za-z]{4,}', title or '')][:3]
    if not any(re.search(r'\b' + re.escape(n) + r'\b', first, re.I) for n in names + title_words):
        return 'The first sentence doesn\'t say who you are. Start with your name and what you do.'
    if len(first.split()) > 45:
        return 'The first sentence runs long. Keep it to who you are and what you do, then add why it matters.'
    if len(sentences) > 4:
        return 'The opening paragraph is long. Keep it to 2–3 sentences and move detail into the sections below.'
    return None


def bio_text_fields(ctx: dict) -> dict:
    """{label: text} for everything on the bio page the owner wrote or approved."""
    ctx = ctx or {}
    fields = {'Headline': ctx.get('professional_title'), 'Tagline': ctx.get('positioning'),
              'About': ctx.get('about_text')}
    for key, label, name_key in (('career_history', 'Career', 'company_name'),
                                 ('notable_work', 'Notable work', 'title'),
                                 ('ventures', 'Ventures', 'name'),
                                 ('projects', 'Projects', 'name')):
        for entry in ctx.get(key) or []:
            if isinstance(entry, dict) and entry.get('description'):
                name = entry.get(name_key) or entry.get('title') or entry.get('name') or ''
                fields['%s: %s' % (label, name) if name else label] = entry['description']
    return fields
