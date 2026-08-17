"""SIM-PRD-CAL-001 §5 — cohort resolution.

Cohort keys are the dimensions that match a user to reference rows. Nothing in
the existing data model carries them: UserProfile has a free-text `location` and
a canonical expertise-zone list, not an occupation code, metro or seniority. So
they are *derived* here and cached on the simulation.

Two paths, in order:
  1. One Haiku classification per simulation (mirrors classify_canonical_zones),
     reading the resume excerpt + zones + location.
  2. A deterministic zone → SOC-group table with a national geography, used when
     the classifier is unavailable, low-confidence, or returns something that
     doesn't validate.

Path 2 is not a degraded edge case — it is the honest floor. A coarse cohort
produces a wide band and a lower tier, which is exactly what the PRD asks for
when we don't really know someone's situation (G3).
"""
from __future__ import annotations

import hashlib
import json
import logging
import re

logger = logging.getLogger(__name__)

# The cohort dimensions, in descending order of how much they narrow a match.
COHORT_KEYS = ('soc_group', 'metro', 'seniority')

# Sentinels meaning "not narrowed on this dimension". Reference rows seeded at
# national scope with seniority 'any' are found by relaxing down to these.
METRO_ANY = 'national'
SENIORITY_ANY = 'any'

SENIORITY_LEVELS = ('entry', 'mid', 'senior', 'exec')

_SOC_RE = re.compile(r'^\d{2}-\d{4}$')


# ── Deterministic fallback: expertise zone → SOC detailed occupation ──────────
# Keys cover both vocabularies in the codebase: the 8 canonical
# expertise_categories slugs and the 15 agents.json expertise_relevance keys.
# The SOC chosen is the closest *individual-contributor professional* match,
# because that is the population Simulacrum's users belong to.
ZONE_TO_SOC = {
    'consulting':   '13-1111',   # Management Analysts
    'operations':   '11-1021',   # General and Operations Managers
    'leadership':   '11-1021',
    'training':     '13-1151',   # Training and Development Specialists
    'coaching':     '13-1151',
    'education':    '25-1099',   # Postsecondary Teachers, All Other
    'technology':   '15-1252',   # Software Developers
    'engineering':  '15-1252',
    'marketing':    '13-1161',   # Market Research Analysts and Marketing Specialists
    'sales':        '41-3091',   # Sales Reps of Services, All Other
    'finance':      '13-2051',   # Financial and Investment Analysts
    'design':       '27-1024',   # Graphic Designers
    'healthcare':   '11-9111',   # Medical and Health Services Managers
    'legal':        '23-1011',   # Lawyers
    'real_estate':  '41-9021',   # Real Estate Sales Agents
}

# Last-resort occupation when no zone matches at all. Management Analysts is the
# broadest professional-services occupation and the one most Simulacrum users
# read against, but a cohort that lands here is a proxy and is tiered as such.
DEFAULT_SOC = '13-1111'

# Metro tokens we hold reference rows for. Everything else resolves to national
# scope rather than guessing at a metro we have no data for.
_METRO_TOKENS = {
    'new york': 'us-nyc', 'nyc': 'us-nyc', 'brooklyn': 'us-nyc', 'manhattan': 'us-nyc',
    'san francisco': 'us-sfo', 'bay area': 'us-sfo', 'oakland': 'us-sfo', 'palo alto': 'us-sfo',
    'los angeles': 'us-lax', 'san diego': 'us-san',
    'seattle': 'us-sea', 'portland': 'us-pdx',
    'chicago': 'us-chi', 'boston': 'us-bos',
    'washington': 'us-dca', 'dc': 'us-dca', 'arlington': 'us-dca',
    'atlanta': 'us-atl', 'miami': 'us-mia', 'orlando': 'us-mco', 'tampa': 'us-tpa',
    'austin': 'us-aus', 'dallas': 'us-dfw', 'houston': 'us-hou',
    'denver': 'us-den', 'phoenix': 'us-phx',
    'philadelphia': 'us-phl', 'minneapolis': 'us-msp',
    'toronto': 'ca-yyz', 'vancouver': 'ca-yvr',
    'london': 'uk-lon',
}

_SENIORITY_HINTS = (
    ('exec',   ('chief', 'ceo', 'cfo', 'coo', 'cto', 'cmo', 'president',
                'partner', 'founder', 'managing director', 'evp', 'svp')),
    ('senior', ('head of', 'director', 'principal', 'vp', 'vice president',
                'senior', 'lead', 'staff', 'fractional')),
    ('entry',  ('junior', 'associate', 'assistant', 'coordinator',
                'intern', 'entry')),
)

# Hints are matched on word boundaries, not as bare substrings. Three-letter
# acronyms make substring matching actively wrong: 'cto' occurs inside
# "director", which read every Director as an executive.
_SENIORITY_PATTERNS = tuple(
    (level, re.compile(r'\b(?:{})\b'.format('|'.join(re.escape(h) for h in hints))))
    for level, hints in _SENIORITY_HINTS
)


# ── Hashing ──────────────────────────────────────────────────────────────────

def cohort_hash(cohort: dict) -> str:
    """Stable md5 of a cohort dict.

    Only COHORT_KEYS participate and they are serialised in a fixed order, so
    the hash is independent of dict ordering and of any extra descriptive keys
    (soc_title, confidence) that ride along for display.
    """
    canonical = {k: str(cohort.get(k) or '') for k in COHORT_KEYS}
    blob = json.dumps(canonical, sort_keys=True, separators=(',', ':'))
    return hashlib.md5(blob.encode('utf-8')).hexdigest()


def soc_major_group(soc_group: str) -> str:
    """'13-1111' → '13-0000'. The proxy rung of the matching ladder."""
    if soc_group and _SOC_RE.match(soc_group):
        return '{}-0000'.format(soc_group.split('-')[0])
    return '{}-0000'.format(DEFAULT_SOC.split('-')[0])


# ── The specificity ladder ───────────────────────────────────────────────────

def candidate_ladder(cohort: dict) -> list:
    """Candidate cohorts from most to least specific, with their match level.

    Returns [(cohort_dict, match_level), ...]. The engine probes each rung by
    hash and takes the first hit, so a user in a metro we have data for gets a
    metro-specific band while everyone else falls through to national — without
    either case needing its own code path.

    Levels: 'exact' (nothing relaxed), 'relaxed' (a dimension widened to its
    ANY sentinel), 'proxy' (occupation widened to its SOC major group — a
    different occupation's data standing in, which must lower the tier).
    """
    soc = cohort.get('soc_group') or DEFAULT_SOC
    metro = cohort.get('metro') or METRO_ANY
    seniority = cohort.get('seniority') or SENIORITY_ANY

    rungs = []
    seen = set()

    def add(s, m, sen, level):
        c = {'soc_group': s, 'metro': m, 'seniority': sen}
        h = cohort_hash(c)
        if h in seen:
            return
        seen.add(h)
        rungs.append((c, level))

    specific_metro = metro != METRO_ANY
    specific_sen = seniority != SENIORITY_ANY

    # Rung 1 — everything as asked.
    add(soc, metro, seniority, 'exact')
    # Rungs 2-4 — relax seniority, then metro, then both.
    if specific_sen:
        add(soc, metro, SENIORITY_ANY, 'exact' if not specific_metro else 'relaxed')
    if specific_metro:
        add(soc, METRO_ANY, seniority, 'relaxed')
        add(soc, METRO_ANY, SENIORITY_ANY, 'relaxed')
    # Rung 5 — a different occupation's data stands in. Always a proxy.
    add(soc_major_group(soc), METRO_ANY, SENIORITY_ANY, 'proxy')
    return rungs


# ── Deterministic derivation ─────────────────────────────────────────────────

def normalize_metro(location: str | None) -> str:
    """Map a free-text location to a metro key we hold data for, else national."""
    if not location:
        return METRO_ANY
    low = location.lower()
    for token, key in _METRO_TOKENS.items():
        if token in low:
            return key
    return METRO_ANY


def infer_seniority(title: str | None) -> str:
    """Infer seniority from a professional title, defaulting to 'mid'.

    Checked exec → senior → entry so that "Senior Director" reads as senior and
    "Chief of Staff" reads as exec, rather than whichever substring hits first.
    """
    if not title:
        return SENIORITY_ANY
    low = title.lower()
    for level, pattern in _SENIORITY_PATTERNS:
        if pattern.search(low):
            return level
    return 'mid'


def soc_from_zones(expertise_zone: str | None, canonical_zones: list | None = None) -> str:
    """Map expertise zones to a SOC occupation via ZONE_TO_SOC.

    Prefers the canonical zone list (structured, has an is_primary flag) and
    falls back to substring-matching the free-text expertise_zone string.
    """
    if canonical_zones:
        primary = [z for z in canonical_zones if z.get('is_primary')]
        for z in (primary + list(canonical_zones)):
            soc = ZONE_TO_SOC.get((z.get('category') or '').lower().strip())
            if soc:
                return soc

    low = (expertise_zone or '').lower()
    if low:
        # Longest key first so 'real_estate' isn't shadowed by a shorter match.
        for zone in sorted(ZONE_TO_SOC, key=len, reverse=True):
            if zone in low or zone.replace('_', ' ') in low:
                return ZONE_TO_SOC[zone]
    return DEFAULT_SOC


def derive_cohort(sim, profile=None) -> dict:
    """Deterministic cohort — no LLM. Always returns a usable cohort."""
    canonical = []
    location = None
    title = None
    if profile is not None:
        try:
            canonical = profile.canonical_zones or []
        except Exception:
            canonical = []
        location = profile.location
        title = profile.tagline or profile.display_name

    return {
        'soc_group': soc_from_zones(getattr(sim, 'expertise_zone', None), canonical),
        'metro': normalize_metro(location),
        'seniority': infer_seniority(title),
        'source': 'derived',
    }


# ── LLM classification ───────────────────────────────────────────────────────

_CLASSIFY_PROMPT = """You map a professional to standard labour-market cohort keys so their \
simulated earnings can be compared against government wage data.

PROFESSIONAL BACKGROUND (excerpt):
{background}

STATED EXPERTISE: {zones}
STATED LOCATION: {location}

Return ONLY a JSON object, no markdown fences and no commentary:
{{"soc_group":"##-####","soc_title":"...","metro":"...","seniority":"entry|mid|senior|exec","confidence":0.0}}

Rules:
- soc_group is a real 2018 SOC detailed occupation code in ##-#### form for the \
occupation this person is paid for today. If you are unsure of the exact code, \
give the closest one you are confident about rather than inventing a code.
- soc_title is the official SOC occupation title for that code.
- metro is the primary US/CA/UK metro city name, or "national" if remote, \
unclear, or outside those markets.
- seniority reflects scope of responsibility, not years served.
- confidence is your own 0-1 confidence in soc_group specifically. Be honest: a \
low number here widens the range we show the user, which is the correct outcome \
when the mapping is genuinely unclear."""

# Below this the classifier's answer is discarded in favour of the
# deterministic table — a guessed SOC code is worse than a coarse one, because
# it produces a narrow band around the wrong occupation.
MIN_CLASSIFY_CONFIDENCE = 0.55


def classify_cohort(parsed_text: str, expertise_zone: str, location: str,
                    user_id: str = None, simulation_id: str = None) -> dict | None:
    """One Haiku call mapping a professional to cohort keys. None on any failure."""
    try:
        import anthropic
        from utils.model_router import get_model
        from app.models.ai_interaction import AIInteraction
        from app.services.claude import _log_interaction

        prompt = _CLASSIFY_PROMPT.format(
            background=(parsed_text or '')[:2500],
            zones=expertise_zone or 'not stated',
            location=location or 'not stated',
        )
        client = anthropic.Anthropic()
        model = get_model('cohort_classification')
        resp = client.messages.create(
            model=model, max_tokens=300,
            messages=[{'role': 'user', 'content': prompt}],
        )
        try:
            _log_interaction(
                AIInteraction.TYPE_AGENT_ACTION, user_id, simulation_id,
                resp.usage, model=model,
            )
        except Exception:
            pass

        raw = resp.content[0].text.strip()
        # Tolerate a fenced or prose-wrapped reply.
        start, end = raw.find('{'), raw.rfind('}')
        if start == -1 or end <= start:
            return None
        data = json.loads(raw[start:end + 1])

        soc = str(data.get('soc_group') or '').strip()
        if not _SOC_RE.match(soc):
            logger.info('Cohort classify returned an invalid SOC code %r — falling back', soc)
            return None

        try:
            confidence = float(data.get('confidence') or 0)
        except (TypeError, ValueError):
            confidence = 0.0
        if confidence < MIN_CLASSIFY_CONFIDENCE:
            logger.info('Cohort classify confidence %.2f below floor — falling back', confidence)
            return None

        seniority = str(data.get('seniority') or '').strip().lower()
        if seniority not in SENIORITY_LEVELS:
            seniority = SENIORITY_ANY

        return {
            'soc_group': soc,
            'soc_title': str(data.get('soc_title') or '')[:120],
            'metro': normalize_metro(str(data.get('metro') or '')),
            'seniority': seniority,
            'confidence': round(confidence, 3),
            'source': 'classified',
        }
    except Exception as exc:
        logger.warning('Cohort classification failed: %s', exc)
        return None


# ── Public entry point ───────────────────────────────────────────────────────

def resolve_cohort(simulation_id: str, force: bool = False) -> dict:
    """Return this simulation's cohort keys, classifying once and caching.

    Never raises and never returns empty: a failed classification degrades to
    the deterministic table so calibration always has a cohort to look up.
    """
    from app.extensions import db
    from app.models.simulation import Simulation
    from app.models.profile import UserProfile
    from app.models.resume import Resume

    sim = Simulation.query.get(simulation_id)
    if not sim:
        return {'soc_group': DEFAULT_SOC, 'metro': METRO_ANY,
                'seniority': SENIORITY_ANY, 'source': 'default'}

    if sim.cohort_json and not force:
        return dict(sim.cohort_json)

    profile = UserProfile.query.filter_by(user_id=sim.user_id).first()
    cohort = derive_cohort(sim, profile)

    resume = Resume.query.get(sim.resume_id) if sim.resume_id else None
    parsed_text = (resume.parsed_text if resume else '') or ''
    if parsed_text.strip():
        classified = classify_cohort(
            parsed_text=parsed_text,
            expertise_zone=sim.expertise_zone or '',
            location=(profile.location if profile else '') or '',
            user_id=sim.user_id,
            simulation_id=sim.id,
        )
        if classified:
            # Keep the derived seniority when the classifier declined to commit.
            if classified['seniority'] == SENIORITY_ANY:
                classified['seniority'] = cohort['seniority']
            cohort = classified

    try:
        from datetime import datetime
        sim.cohort_json = cohort
        sim.cohort_resolved_at = datetime.utcnow()
        db.session.commit()
    except Exception as exc:
        logger.warning('Could not cache cohort on simulation %s: %s', simulation_id, exc)
        try:
            db.session.rollback()
        except Exception:
            pass

    return cohort


# SOC major-group names, so a proxy match reads as a recognisable occupation
# family rather than a bare '13-0000' in the methodology drawer.
_MAJOR_GROUP_NAMES = {
    '11-0000': 'Management occupations',
    '13-0000': 'Business and financial operations occupations',
    '15-0000': 'Computer and mathematical occupations',
    '23-0000': 'Legal occupations',
    '25-0000': 'Educational instruction and library occupations',
    '27-0000': 'Arts, design, entertainment, sports and media occupations',
    '29-0000': 'Healthcare practitioners and technical occupations',
    '41-0000': 'Sales and related occupations',
}


def describe_cohort(cohort: dict) -> str:
    """Short human label for the methodology drawer, e.g.
    'Management Analysts · New York metro · senior'."""
    if not cohort:
        return 'not resolved'
    soc = cohort.get('soc_group') or ''
    occupation = (
        cohort.get('soc_title')
        or _MAJOR_GROUP_NAMES.get(soc)
        or (soc + ' (broad occupation group)' if soc.endswith('-0000') else soc)
        or 'unknown occupation'
    )
    parts = [occupation]
    metro = cohort.get('metro')
    parts.append('national' if not metro or metro == METRO_ANY else '{} metro'.format(metro))
    sen = cohort.get('seniority')
    if sen and sen != SENIORITY_ANY:
        parts.append(sen)
    return ' · '.join(parts)
