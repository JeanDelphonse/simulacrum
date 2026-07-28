"""SIM-PRD-CRM-002 — Prospect Discovery Agent.

Apollo finds companies matching firmographic filters; Claude judges which of them
fit the employer-pilot thesis. Apollo can filter by size and industry but cannot
judge fit, so the scoring layer is what turns a raw company list into a qualified
prospect list.

High-fit unflagged firms auto-save into the CRM-001 pipeline at 'researched' so
Touch 1 drafts on the next briefing; everything else waits in a review queue.
Nothing flagged as acquired/oversize/off-category ever auto-saves, whatever its
apparent fit — that guardrail is the point of the feature.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta

from app.extensions import db
from app.models.admin_prospect import AdminProspect
from app.models.discovery import DiscoveryCandidate, DiscoveryProfile

logger = logging.getLogger(__name__)

# Classification, not generation — Haiku, per the PRD and the model routing used
# elsewhere in the app. A batch of 50 scores cheaply.
SCORER_MODEL = 'claude-haiku-4-5-20251001'
ENRICH_MODEL = 'claude-haiku-4-5-20251001'

# Companies per scoring call. Chunked rather than one big call so a single
# malformed response cannot cost the whole run.
SCORE_CHUNK = 20

# Rough Apollo credit cost per company returned, used only for the pre-run
# estimate the founder sees. Apollo bills per record surfaced.
APOLLO_CREDITS_PER_COMPANY = 1


# ── Apollo ────────────────────────────────────────────────────────────────────

def apollo_available(user_id: str) -> bool:
    from app.models.integration import UserIntegration
    rec = UserIntegration.query.filter_by(user_id=user_id, provider='apollo').first()
    return bool(rec and rec.access_token_enc)


def _apollo_client(user_id: str):
    from app.models.integration import UserIntegration
    from app.services.token_crypto import decrypt_token
    from app.services.apollo_client import ApolloClient

    rec = UserIntegration.query.filter_by(user_id=user_id, provider='apollo').first()
    if not rec or not rec.access_token_enc:
        return None
    return ApolloClient(decrypt_token(rec.access_token_enc))


def _norm_domain(v: str) -> str:
    v = (v or '').strip().lower()
    v = re.sub(r'^https?://', '', v)
    v = re.sub(r'^www\.', '', v)
    return v.split('/')[0].strip()


def _normalize_company(org: dict) -> dict:
    """Flatten one Apollo organization record into the fields we keep."""
    domain = _norm_domain(org.get('primary_domain') or org.get('website_url') or '')
    loc = ', '.join(x for x in [org.get('city'), org.get('state'), org.get('country')] if x)
    return {
        'company': (org.get('name') or '').strip()[:200],
        'domain': domain[:200],
        'headcount': org.get('estimated_num_employees'),
        'industry': (org.get('industry') or '')[:120] or None,
        'location': loc[:160] or None,
        'leader_name': None,
        'leader_linkedin': (org.get('linkedin_url') or None),
    }


def known_domains() -> set:
    """Every domain already discovered or already in the pipeline.

    Covers dismissals too, so a firm the founder rejected never comes back
    (PRD section 5, 'Dedup + memory').
    """
    seen = {c.domain for c in db.session.query(DiscoveryCandidate.domain).all()
            if c.domain}
    for (site,) in db.session.query(AdminProspect.website).all():
        d = _norm_domain(site)
        if d:
            seen.add(d)
    return seen


# ── The Claude fit scorer ─────────────────────────────────────────────────────

_THESIS = (
    "Simulacrum is sold to small consultancies as an employer pilot. The thesis: a "
    "good-fit firm is one whose consultants ARE the product — senior people selling "
    "their own expertise — and whose growth constraint is business-development "
    "capacity. Staffing agencies, recruiters, franchises and productized-service "
    "businesses are NOT a fit. Firms that look acquired or like a subsidiary of a "
    "larger group are never a good fit."
)

_SCORE_PROMPT = """{thesis}

Score each company below for FIT. Return ONLY a JSON array, no prose, no markdown
fences. One object per company, in the same order, with exactly these keys:

  domain      - echo the company's domain unchanged, so results can be matched
  fit         - "high" | "medium" | "low"
  rationale   - ONE short sentence saying why. No hedging.
  flags       - array, any of: "possibly_acquired", "too_large", "off_category".
                Empty array if none apply.
  category    - a short label such as "Fractional Exec" or "Boutique Mgmt"

Rules:
- If anything suggests the firm was acquired or is a subsidiary, add
  "possibly_acquired" and do NOT score it high.
- If it reads like staffing, recruiting, a franchise, or a productized service,
  add "off_category".
- If the headcount looks well outside a 5-25 boutique band (or Apollo's number
  looks stale for the described business), add "too_large".
- Be strict. "high" means you would stake the first touch on it.

Companies:
{companies}
"""


def score_companies(companies: list) -> dict:
    """Score companies for fit. Returns domain -> {fit, rationale, flags, category}.

    Chunked; a chunk that fails to parse degrades to unscored rather than taking
    the run down. Unscored companies fall through to the review queue, which is
    the safe direction.
    """
    out = {}
    if not companies:
        return out

    try:
        import anthropic
        from flask import current_app
        client = anthropic.Anthropic(api_key=current_app.config['CLAUDE_API_KEY'])
    except Exception as exc:
        logger.warning('discovery: scorer unavailable: %s', exc)
        return out

    for i in range(0, len(companies), SCORE_CHUNK):
        chunk = companies[i:i + SCORE_CHUNK]
        listing = '\n'.join(
            '- {} | domain: {} | headcount: {} | industry: {} | location: {}'.format(
                c['company'], c['domain'], c.get('headcount') or '?',
                c.get('industry') or '?', c.get('location') or '?')
            for c in chunk
        )
        try:
            resp = client.messages.create(
                model=SCORER_MODEL,
                # A full chunk measures ~1k output tokens; 4k leaves real headroom,
                # because hitting the cap truncates the JSON and loses every row in
                # the chunk, not just the last one.
                max_tokens=4000,
                messages=[{'role': 'user', 'content': _SCORE_PROMPT.format(
                    thesis=_THESIS, companies=listing)}],
            )
            if getattr(resp, 'stop_reason', None) == 'max_tokens':
                logger.warning(
                    'discovery: scoring chunk %d hit max_tokens — JSON truncated, '
                    '%d companies fall through to review. Lower SCORE_CHUNK.',
                    i // SCORE_CHUNK, len(chunk))
            rows = _parse_json_array((resp.content[0].text or '').strip())
        except Exception as exc:
            logger.warning('discovery: scoring chunk %d failed: %s', i // SCORE_CHUNK, exc)
            continue

        for row in rows:
            dom = _norm_domain(str(row.get('domain') or ''))
            if not dom:
                continue
            fit = str(row.get('fit') or '').lower()
            out[dom] = {
                'fit': fit if fit in DiscoveryCandidate.FITS else DiscoveryCandidate.FIT_LOW,
                'rationale': (str(row.get('rationale') or '').strip() or None),
                'flags': [str(f) for f in (row.get('flags') or []) if f],
                'category': (str(row.get('category') or '').strip() or None),
            }
    return out


def _parse_json_array(raw: str) -> list:
    """Tolerate the usual model wrappers around a JSON array."""
    if not raw:
        return []
    raw = re.sub(r'^```(?:json)?\s*|\s*```$', '', raw.strip())
    try:
        parsed = json.loads(raw)
    except ValueError:
        m = re.search(r'\[.*\]', raw, re.S)
        if not m:
            return []
        try:
            parsed = json.loads(m.group(0))
        except ValueError:
            return []
    return parsed if isinstance(parsed, list) else []


# ── Guardrails & routing ──────────────────────────────────────────────────────

def apply_guardrails(company: dict, scored: dict, profile) -> list:
    """Deterministic flags layered on top of whatever the model flagged.

    The model is asked to flag these too, but headcount and negative keywords are
    checkable facts — leaving them to the model's judgement alone is how an
    acquired or oversize firm slips through and costs a touch (FR-DSC-04).
    """
    flags = set(scored.get('flags') or [])

    head = company.get('headcount')
    if isinstance(head, int) and head > 0 and profile:
        too_big = head > (profile.headcount_max or 25)
        too_small = head < (profile.headcount_min or 5)
        # A known headcount settles the size question, so the deterministic verdict
        # replaces whatever the model guessed. Otherwise a 2-person firm can end up
        # labelled both 'above the boutique band' and 'likely a solo shop', which
        # reads as a broken tool even though both route to review.
        flags.discard(DiscoveryCandidate.FLAG_TOO_LARGE)
        flags.discard(DiscoveryCandidate.FLAG_TOO_SMALL)
        if too_big:
            flags.add(DiscoveryCandidate.FLAG_TOO_LARGE)
        if too_small:
            flags.add(DiscoveryCandidate.FLAG_TOO_SMALL)

    neg = [k.lower() for k in ((profile.keywords_neg if profile else None) or [])]
    haystack = ' '.join(str(company.get(f) or '') for f in
                        ('company', 'industry', 'domain')).lower()
    if neg and any(k in haystack for k in neg):
        flags.add(DiscoveryCandidate.FLAG_OFF_CATEGORY)

    # Only recognised flags, so the UI never renders an unknown label.
    known = {DiscoveryCandidate.FLAG_ACQUIRED, DiscoveryCandidate.FLAG_TOO_LARGE,
             DiscoveryCandidate.FLAG_TOO_SMALL, DiscoveryCandidate.FLAG_OFF_CATEGORY}
    return sorted(f for f in flags if f in known)


def route_for(fit: str, flags: list, threshold: str) -> str:
    """Auto-save only a high-fit firm with no flags, and only if the profile allows.

    Any flag routes to review regardless of fit — that is the acquisition/size
    guardrail, and it is not overridable by score.
    """
    if flags:
        return DiscoveryCandidate.ROUTE_REVIEW
    if threshold == DiscoveryProfile.THRESHOLD_NONE:
        return DiscoveryCandidate.ROUTE_REVIEW
    if fit == DiscoveryCandidate.FIT_HIGH:
        return DiscoveryCandidate.ROUTE_AUTO_SAVE
    return DiscoveryCandidate.ROUTE_REVIEW


# ── Running discovery ─────────────────────────────────────────────────────────

def estimate_run(profile, limit: int = None) -> dict:
    """Pre-run estimate the founder sees before spending Apollo credits (FR-DSC-07)."""
    cap = min(int(limit or profile.batch_cap or DiscoveryProfile.DEFAULT_BATCH_CAP),
              int(profile.batch_cap or DiscoveryProfile.DEFAULT_BATCH_CAP))
    return {
        'batch_cap': cap,
        'apollo_credits_estimate': cap * APOLLO_CREDITS_PER_COMPANY,
        'scoring_calls_estimate': (cap + SCORE_CHUNK - 1) // SCORE_CHUNK,
        'scorer_model': SCORER_MODEL,
        'note': 'Apollo bills per company surfaced; already-known domains are '
                'discarded before scoring, so scoring cost is usually lower.',
    }


def run_discovery(profile, user_id: str, limit: int = None, enrich: bool = True) -> dict:
    """Full discovery pass for one profile (FR-DSC-01 … FR-DSC-07)."""
    cap = min(int(limit or profile.batch_cap or DiscoveryProfile.DEFAULT_BATCH_CAP),
              int(profile.batch_cap or DiscoveryProfile.DEFAULT_BATCH_CAP))

    client = _apollo_client(user_id)
    if client is None:
        return {'error': 'Apollo is not connected. Connect it in Settings → '
                         'Integrations to run discovery.',
                'found': 0, 'scored': 0, 'auto_saved': 0, 'queued': 0, 'skipped': 0}

    # ── Search ────────────────────────────────────────────────────────────────
    keywords = list((profile.keywords_pos or [])) + list((profile.categories or []))
    raw = []
    page = 1
    try:
        while len(raw) < cap and page <= 5:
            batch = client.organization_search(
                keywords=keywords or None,
                num_employees_ranges=[profile.employee_range],
                locations=[profile.geography] if profile.geography else None,
                per_page=min(25, cap - len(raw)),
                page=page,
            )
            if not batch:
                break
            raw.extend(batch)
            page += 1
    except Exception as exc:
        logger.warning('discovery: Apollo search failed: %s', exc)
        if not raw:
            return {'error': 'Apollo search failed: {}'.format(exc),
                    'found': 0, 'scored': 0, 'auto_saved': 0, 'queued': 0, 'skipped': 0}

    # ── Normalise + dedup ─────────────────────────────────────────────────────
    seen = known_domains()
    companies, batch_domains = [], set()
    for org in raw[:cap]:
        c = _normalize_company(org)
        if not c['company'] or not c['domain']:
            continue
        if c['domain'] in seen or c['domain'] in batch_domains:
            continue
        batch_domains.add(c['domain'])
        companies.append(c)

    skipped = len(raw) - len(companies)
    if not companies:
        profile.last_run_at = datetime.utcnow()
        db.session.commit()
        return {'found': len(raw), 'scored': 0, 'auto_saved': 0, 'queued': 0,
                'skipped': skipped,
                'note': 'Every company Apollo returned is already known.'}

    # ── Score ─────────────────────────────────────────────────────────────────
    scores = score_companies(companies)

    auto_saved, queued = 0, 0
    for c in companies:
        scored = scores.get(c['domain'], {})
        flags = apply_guardrails(c, scored, profile)
        # Unscored (scorer unavailable or chunk failed) must not auto-save.
        fit = scored.get('fit') or DiscoveryCandidate.FIT_LOW
        route = route_for(fit, flags, profile.auto_save_threshold)

        cand = DiscoveryCandidate(
            profile_id=profile.id,
            company=c['company'],
            domain=c['domain'],
            headcount=c.get('headcount'),
            industry=c.get('industry'),
            location=c.get('location'),
            leader_name=c.get('leader_name'),
            leader_linkedin=c.get('leader_linkedin'),
            fit=fit,
            rationale=scored.get('rationale'),
            flags=flags,
            route=route,
        )
        db.session.add(cand)
        try:
            db.session.flush()
        except Exception as exc:
            # Lost a race on uq_domain — another run already recorded it.
            db.session.rollback()
            logger.info('discovery: domain %s already recorded (%s)', c['domain'], exc)
            skipped += 1
            continue

        if route == DiscoveryCandidate.ROUTE_AUTO_SAVE:
            if enrich:
                enrich_candidate(cand, commit=False)
            save_candidate(cand, commit=False,
                           category=scored.get('category'))
            auto_saved += 1
        else:
            queued += 1

    profile.last_run_at = datetime.utcnow()
    db.session.commit()

    result = {'found': len(raw), 'scored': len(scores), 'auto_saved': auto_saved,
              'queued': queued, 'skipped': skipped}
    # Say so plainly when the scorer was down: everything routed to review, which
    # is the safe direction, but it is not the same as every firm scoring low.
    if companies and not scores:
        result['note'] = ('Fit scoring was unavailable, so nothing was auto-saved — '
                          'all {} firms are held for review, unscored.'.format(len(companies)))
    elif len(scores) < len(companies):
        result['note'] = ('{} of {} firms could not be scored and are held for '
                          'review.'.format(len(companies) - len(scores), len(companies)))
    return result


# ── Enrichment ────────────────────────────────────────────────────────────────

def enrich_candidate(cand, commit: bool = True) -> bool:
    """Best-effort web-search pass for a named leader and a recent signal.

    High-fit candidates only, and never blocking: a failed enrichment must still
    let the firm into the pipeline (FR-DSC-06).
    """
    try:
        from flask import current_app
        from app.services.consulting_outreach_service import _web_search
        api_key = current_app.config.get('CLAUDE_API_KEY')
        if not api_key:
            return False
        query = (
            "For the consulting firm '{}' ({}): who is the founder, managing "
            "partner or principal? Give the person's full name and LinkedIn URL if "
            "you can find it. Then give ONE recent signal (funding, growth, hiring, "
            "press) in a single sentence. If you cannot find a name, say 'unknown'. "
            "Answer in two short lines: 'Leader: ...' then 'Signal: ...'."
        ).format(cand.company, cand.domain)
        text = _web_search(query, api_key, ENRICH_MODEL) or ''
    except Exception as exc:
        logger.info('discovery: enrichment failed for %s: %s', cand.domain, exc)
        return False

    leader = re.search(r'Leader:\s*(.+)', text)
    signal = re.search(r'Signal:\s*(.+)', text)
    if leader:
        name = leader.group(1).strip()
        li = re.search(r'(https?://(?:www\.)?linkedin\.com/\S+)', name)
        if li:
            cand.leader_linkedin = li.group(1).rstrip('.,)')[:300]
            name = name.replace(li.group(1), '').strip(' -–—|,')
        if name and 'unknown' not in name.lower():
            cand.leader_name = name[:160]
    if signal:
        cand.signal = signal.group(1).strip()[:1000]
    if commit:
        db.session.commit()
    return bool(cand.leader_name or cand.signal)


# ── Saving into the CRM-001 pipeline ──────────────────────────────────────────

def save_candidate(cand, commit: bool = True, category: str = None):
    """Create the admin_prospects row at 'researched' so Touch 1 drafts next run.

    The fit rationale is carried into the prospect's notes, so the founder always
    sees WHY a firm was surfaced when they open it (FR-DSC-03).
    """
    from app.services import admin_crm_service as crm

    if cand.prospect_id:
        existing = AdminProspect.query.get(cand.prospect_id)
        if existing:
            return existing

    # The rationale is a first-class column, so notes carries only what has no
    # field of its own — no duplication between the two.
    note_parts = []
    if cand.signal:
        note_parts.append('Signal: {}'.format(cand.signal))
    if cand.headcount:
        note_parts.append('Headcount (Apollo): {}'.format(cand.headcount))
    if cand.flag_labels:
        note_parts.append('Flags: {}'.format('; '.join(cand.flag_labels)))

    p = AdminProspect(
        firm_name=cand.company,
        lead_name=cand.leader_name,
        lead_linkedin=cand.leader_linkedin,
        website=cand.domain,
        contact_path='linkedin',
        fit=cand.fit if cand.fit in AdminProspect.FITS else AdminProspect.FIT_MEDIUM,
        category=(category or cand.industry or None),
        stage=AdminProspect.STAGE_RESEARCHED,
        notes='\n'.join(note_parts) or None,
        discovery_rationale=cand.rationale,
        discovery_fit=cand.fit,
    )
    # Researched stage is due immediately, so the next briefing drafts Touch 1.
    p.next_followup = crm._followup_from(AdminProspect.STAGE_RESEARCHED)
    db.session.add(p)
    db.session.flush()

    cand.prospect_id = p.id
    cand.status = DiscoveryCandidate.STATUS_SAVED
    if commit:
        db.session.commit()
    return p


def dismiss_candidate(cand, commit: bool = True):
    """Dismissed firms stay dismissed — the row is kept as dedup memory."""
    cand.status = DiscoveryCandidate.STATUS_DISMISSED
    if commit:
        db.session.commit()
    return cand


# ── Queries ───────────────────────────────────────────────────────────────────

def review_queue(limit: int = 200) -> list:
    rows = (DiscoveryCandidate.query
            .filter_by(status=DiscoveryCandidate.STATUS_QUEUED)
            .order_by(DiscoveryCandidate.discovered_at.desc())
            .limit(limit).all())
    # High fit first, then flagged (they need a decision), then the rest.
    order = {DiscoveryCandidate.FIT_HIGH: 0, DiscoveryCandidate.FIT_MEDIUM: 1,
             DiscoveryCandidate.FIT_LOW: 2}
    rows.sort(key=lambda c: (order.get(c.fit, 3), not (c.flags or [])))
    return [c.to_dict() for c in rows]


def counters() -> dict:
    q = DiscoveryCandidate.query
    queued = q.filter_by(status=DiscoveryCandidate.STATUS_QUEUED).count()
    flagged = sum(1 for c in q.filter_by(status=DiscoveryCandidate.STATUS_QUEUED).all()
                  if c.flags)
    return {
        'awaiting_review': queued,
        'flagged': flagged,
        'saved': q.filter_by(status=DiscoveryCandidate.STATUS_SAVED).count(),
        'dismissed': q.filter_by(status=DiscoveryCandidate.STATUS_DISMISSED).count(),
        'profiles': DiscoveryProfile.query.count(),
    }


# ── Scheduled runs ────────────────────────────────────────────────────────────

def due_profiles(now=None) -> list:
    """Profiles whose cadence has come round (FR-DSC-01, section 5 'Scheduled')."""
    now = now or datetime.utcnow()
    windows = {DiscoveryProfile.SCHEDULE_WEEKLY: timedelta(days=7),
               DiscoveryProfile.SCHEDULE_MONTHLY: timedelta(days=30)}
    out = []
    for p in DiscoveryProfile.query.filter(DiscoveryProfile.schedule.isnot(None)).all():
        window = windows.get(p.schedule)
        if not window:
            continue
        if p.last_run_at is None or (now - p.last_run_at) >= window:
            out.append(p)
    return out


def scheduled_actor_id():
    """Which account's Apollo credentials a scheduled run should use.

    The briefing runs from the scheduler with no request context, so there is no
    current_user to borrow. Apollo is connected per user, so fall back to the
    first admin who actually has it connected.
    """
    from app.models.integration import UserIntegration
    from app.models.user import User
    row = (db.session.query(UserIntegration.user_id)
           .join(User, User.id == UserIntegration.user_id)
           .filter(UserIntegration.provider == 'apollo')
           .filter(UserIntegration.access_token_enc.isnot(None))
           .filter(User.is_admin.is_(True))
           .first())
    return row[0] if row else None


def run_scheduled(user_id: str = None, now=None) -> dict:
    """Run every due profile. Summarised in the morning briefing (FR-DSC-07)."""
    totals = {'profiles_run': 0, 'auto_saved': 0, 'queued': 0}
    user_id = user_id or scheduled_actor_id()
    if not user_id:
        return totals
    for p in due_profiles(now):
        if not apollo_available(user_id):
            logger.info('discovery: skipping scheduled run, Apollo not connected')
            break
        result = run_discovery(p, user_id)
        if result.get('error'):
            logger.warning('discovery: scheduled run of %s failed: %s',
                           p.name, result['error'])
            continue
        totals['profiles_run'] += 1
        totals['auto_saved'] += result.get('auto_saved', 0)
        totals['queued'] += result.get('queued', 0)
    return totals
