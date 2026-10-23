import json
import logging
import re as _re
from flask import current_app
from utils.model_router import get_model

logger = logging.getLogger(__name__)

BIO_SYSTEM_PROMPT = """You are a professional biographer writing encyclopedia-style biographical \
articles for accomplished professionals. Your writing must adhere strictly to the conventions \
of Wikipedia biographical articles.

MANDATORY STYLE RULES:
1. Write entirely in the third person. Never use 'I', 'my', 'we', 'you', or any first- or \
second-person pronoun.
2. Open with a lede: a single dense sentence identifying the person by full name, their primary \
professional domain, and the organizations or sectors most associated with their career.
3. Every factual claim must be traceable to the SOURCE material provided. Do not invent roles, \
employers, dates, degrees, numbers, or outcomes. If the source does not state something, the \
bio does not state it.
4. Use specific named deliverables and measurable outcomes where the source data provides them. \
Prefer precision over generality.
5. Structure the bio with these plain-text section markers, in this order, using ONLY the ones \
the source supports: \
**Career** — chronological narrative from earliest to most recent role \
**Notable Work** — specific projects or deliverables with impact \
**Ventures** — entrepreneurial or independent work \
**Current Work** — present roles and active projects. \
Omit any section the source gives no facts for. Never write a section to fill space, and never \
write a section heading followed by general or speculative prose.
6. Length follows the evidence: roughly 100-150 words for a single documented role, up to 800 \
words for a long, well-documented career. A short accurate bio is correct; a padded one is a \
failure. Each section is a prose paragraph, not a list.
7. Tense: past tense for completed roles and work, present tense for current roles and active ventures.
8. PROHIBITED LANGUAGE — never use these words or their synonyms: passionate, visionary, \
results-driven, dynamic, innovative, thought leader, guru, ninja, rockstar, game-changer, \
disruptive, synergy, leverage (as a verb), cutting-edge, best-in-class.
9. Do not begin any sentence with 'He is known for' or 'She is known for'. State facts directly.
10. Do not speculate about the person's motivations, feelings, or future intentions.
11. EXPERTISE ZONES are skill areas inferred by software. Use them only to frame what the \
person works on. Never derive an employer, title, date, client, or outcome from them."""


def _client():
    """The Anthropic client. One seam, so tests can substitute a fake."""
    import anthropic
    return anthropic.Anthropic(api_key=current_app.config['CLAUDE_API_KEY'])


def generate_wikipedia_bio(profile, resume_text: str, expertise_zones: list,
                           existing_bio=None) -> str:
    """Write a bio from `resume_text`, which must be real source material.

    Callers go through bio_readiness first: this function no longer has a
    "nothing to go on" fallback, because writing from a name alone is how
    fabricated bios got published.
    """
    client = _client()
    model = get_model('bio_generation')

    zone_summary = json.dumps([
        {
            'zone': z.get('zone_name', str(z)) if isinstance(z, dict) else str(z),
            'evidence': z.get('evidence', '') if isinstance(z, dict) else '',
        }
        for z in (expertise_zones or [])
    ], indent=2)

    if existing_bio:
        user_prompt = (
            f'Refine and update the following professional biography. The EXISTING BIO is a '
            f'draft, not a source: keep a detail from it only if the SOURCE below supports it, '
            f'and drop every detail the SOURCE does not support. Keep the person\'s voice.\n\n'
            f'FULL NAME: {profile.display_name or "Not provided"}\n'
            f'LOCATION: {profile.location or "Not specified"}\n\n'
            f'EXISTING BIO (draft to refine, NOT evidence):\n{existing_bio}\n\n'
            f'SOURCE — RESUME / LINKEDIN / CAREER HISTORY:\n{resume_text[:8000]}\n\n'
            f'PUBLISHED SIMULATION ZONES:\n{zone_summary}\n\n'
            f'Output the refined bio now. Follow all style rules. Plain text only. '
            f'Use **Section Name** for section markers. No markdown beyond that.'
        )
    else:
        user_prompt = (
            f'Write a Wikipedia-style professional biography for the following individual.\n\n'
            f'FULL NAME: {profile.display_name or "Not provided"}\n'
            f'LOCATION: {profile.location or "Not specified"}\n\n'
            f'SOURCE — RESUME / LINKEDIN / CAREER HISTORY:\n{resume_text[:8000]}\n\n'
            f'EXPERTISE ZONES (skill areas inferred by software — framing only, not facts):\n'
            f'{zone_summary}\n\n'
            f'Write the bio now. Follow all style rules. Output plain text only. '
            f'Use **Section Name** for section markers. No markdown beyond that.'
        )

    response = client.messages.create(
        model=model,
        max_tokens=2400,
        system=BIO_SYSTEM_PROMPT,
        messages=[{'role': 'user', 'content': user_prompt}],
    )
    return response.content[0].text


# ---------------------------------------------------------------------------
# Grounding: check a generated bio against its source before anyone sees it
# ---------------------------------------------------------------------------

_YEAR_RE = _re.compile(r'\b(19[5-9]\d|20\d\d)\b')

_VERIFY_PROMPT = """You are a fact-checker. Compare the BIO against the SOURCE.

List every specific factual claim in the BIO that the SOURCE does not support. A claim is \
specific if it names or states an employer, client, job title, date or year, duration, degree, \
institution, award, number, percentage, or outcome. Paraphrase is fine; a claim is supported if \
the SOURCE states the same fact in other words. General descriptive framing ("works in product \
design") is not a specific claim and must not be listed.

Return ONLY a JSON object: {"unsupported": ["<the claim, quoted or closely paraphrased>", ...]}
Return {"unsupported": []} if every specific claim is supported.

SOURCE:
<<SOURCE>>

BIO:
<<BIO>>"""

_REWRITE_PROMPT = """Rewrite the BIO below, removing each of these unsupported claims entirely. \
Do not replace them with anything else and do not add any new fact. Keep everything the SOURCE \
supports, keep the section markers for sections that still have content, and drop a section \
that has nothing left. Output the bio only.

UNSUPPORTED CLAIMS TO REMOVE:
<<CLAIMS>>

SOURCE:
<<SOURCE>>

BIO:
<<BIO>>"""


def unsupported_years(bio_text, source_text):
    """Years the bio states that never appear in the source. Deterministic backstop."""
    source_years = set(_YEAR_RE.findall(source_text or ''))
    return sorted({y for y in _YEAR_RE.findall(bio_text or '') if y not in source_years})


def _verify_claims(bio_text, source_text):
    """Model-checked list of unsupported specific claims."""
    client = _client()
    prompt = (_VERIFY_PROMPT.replace('<<SOURCE>>', source_text[:12000])
                            .replace('<<BIO>>', bio_text[:8000]))
    response = client.messages.create(
        model=get_model('bio_fact_check'),
        max_tokens=1200,
        messages=[{'role': 'user', 'content': prompt}],
    )
    raw = response.content[0].text.strip()
    match = _re.search(r'\{.*\}', raw, _re.DOTALL)
    data = json.loads(match.group(0) if match else raw)
    claims = data.get('unsupported') if isinstance(data, dict) else None
    return [str(c).strip() for c in (claims or []) if str(c).strip()][:40]


def _remove_claims(bio_text, source_text, claims):
    client = _client()
    prompt = (_REWRITE_PROMPT.replace('<<CLAIMS>>', '\n'.join('- ' + c for c in claims))
                             .replace('<<SOURCE>>', source_text[:12000])
                             .replace('<<BIO>>', bio_text[:8000]))
    response = client.messages.create(
        model=get_model('bio_generation'),
        max_tokens=2400,
        system=BIO_SYSTEM_PROMPT,
        messages=[{'role': 'user', 'content': prompt}],
    )
    return response.content[0].text


class UngroundedBioError(Exception):
    """The bio still states facts the source does not support after correction."""

    def __init__(self, claims):
        super().__init__('Bio contains unsupported claims: %s' % '; '.join(claims[:5]))
        self.claims = claims


def ground_bio(bio_text, source_text):
    """Return (bio, report): the bio with unsupported claims removed, and what was done.

    One verify pass, at most one rewrite, then a deterministic recheck. If the
    model check is unavailable the deterministic year check still runs; a bio
    that fails it is never returned, so an API outage cannot reopen the hole.
    """
    report = {'status': 'verified', 'removed': []}
    try:
        claims = _verify_claims(bio_text, source_text)
    except Exception:
        logger.warning('bio grounding: verifier unavailable', exc_info=True)
        claims = None
        report['status'] = 'unverified'

    years = unsupported_years(bio_text, source_text)
    if years and claims is not None:
        claims = claims + ['Any mention of the year %s' % y for y in years
                           if not any(y in c for c in claims)]

    if claims:
        try:
            bio_text = _remove_claims(bio_text, source_text, claims)
            report['status'] = 'corrected'
            report['removed'] = claims
        except Exception:
            logger.warning('bio grounding: rewrite failed', exc_info=True)
            raise UngroundedBioError(claims)

    leftover = unsupported_years(bio_text, source_text)
    if leftover:
        raise UngroundedBioError(['Any mention of the year %s' % y for y in leftover])
    return bio_text, report


def suggest_zone_tagline(expertise_zone: str, deliverables: list) -> str:
    import anthropic
    client = anthropic.Anthropic(api_key=current_app.config['CLAUDE_API_KEY'])
    model = get_model('bio_generation')

    deliv_text = '\n'.join(f'- {d}' for d in (deliverables or [])[:10])
    response = client.messages.create(
        model=model,
        max_tokens=150,
        messages=[{
            'role': 'user',
            'content': (
                f'Write a 1-2 sentence public-facing tagline for a professional offering services in '
                f'"{expertise_zone}". The tagline describes who they help and how.\n\n'
                f'Evidence from their background:\n{deliv_text}\n\n'
                f'Write only the tagline text. No quotes, no labels. Max 200 characters.'
            ),
        }],
    )
    return response.content[0].text.strip()[:200]


def suggest_service_bullets(expertise_zone: str, l1_streams: list, l2_streams: list) -> list:
    import anthropic
    client = anthropic.Anthropic(api_key=current_app.config['CLAUDE_API_KEY'])
    model = get_model('bio_generation')

    streams = (
        [s.get('name', '') for s in (l1_streams or [])[:3]] +
        [s.get('name', '') for s in (l2_streams or [])[:3]]
    )
    streams_text = '\n'.join(f'- {s}' for s in streams if s)

    response = client.messages.create(
        model=model,
        max_tokens=300,
        messages=[{
            'role': 'user',
            'content': (
                f'Based on these income streams from "{expertise_zone}":\n{streams_text}\n\n'
                f'Write 4-6 short service bullet phrases a consultant might list on their profile. '
                f'Each should be 2-6 words, specific, and actionable. '
                f'Return one per line. No numbers, no dashes, no punctuation at the end.'
            ),
        }],
    )
    lines = [ln.strip().lstrip('-•* ') for ln in response.content[0].text.strip().split('\n') if ln.strip()]
    return [ln[:60] for ln in lines if ln][:6]
