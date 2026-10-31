"""The bio page fact box (Bio Page Enhancements item 1) and "Updated" date (item 5).

Like a Wikipedia infobox, it summarises the page and adds no new claims: every
row is read from data the page already shows or stores. The one number, the
hourly rate, is shown only as the calibrated range with its reference dataset
(CAL-001), never parsed out of the rate card's prose. Income projections and
wealth-layer figures are never included.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime

logger = logging.getLogger(__name__)

_AVAILABILITY = {
    'available': 'Available for work',
    'limited': 'Limited availability',
    'unavailable': 'Not taking new work',
}
_YEAR = re.compile(r'\b(19[5-9]\d|20\d\d)\b')


def _is_current(entry: dict) -> bool:
    end = str(entry.get('end_date') or '').strip().lower()
    return not end or end == 'present'


def years_of_experience(career: list, now: datetime | None = None) -> int | None:
    years = []
    for e in career or []:
        m = _YEAR.search(str(e.get('start_date') or ''))
        if m:
            years.append(int(m.group(1)))
    if not years:
        return None
    n = (now or datetime.utcnow()).year - min(years)
    return n if n > 0 else None


def availability(sim_bios: list) -> str | None:
    """The most open status across the public simulations; 'hidden' shows nothing."""
    statuses = {str(b.get('availability') or '') for b in (sim_bios or [])}
    for key in ('available', 'limited', 'unavailable'):
        if key in statuses:
            return _AVAILABILITY[key]
    return None


def rate_range(user_id: str) -> dict | None:
    """{'text': '$150–$220/hr', 'source': 'BLS OEWS 2025', 'tier': 'Moderate confidence'} from
    the latest calibrated rate card, or None."""
    try:
        from app.models.agent_action import AgentAction
        from app.models.simulation import Simulation
        from app.services import calibration_service as cal
        action = (AgentAction.query.join(Simulation, Simulation.id == AgentAction.simulation_id)
                  .filter(Simulation.user_id == user_id, AgentAction.action_type == 'rate_card',
                          AgentAction.status == 'complete')
                  .order_by(AgentAction.completed_at.desc()).first())
        if not action:
            return None
        run = next((r for r in cal.runs_for_action(action.id)
                    if r.output_field == 'billable_hourly_rate'), None)
        if run is None:
            return None
        d = run.to_dict()
        low = cal.format_value(d['low'], d['unit'])
        high = cal.format_value(d['high'], d['unit'])
        return {'text': '%s–%s' % (low.replace('/hr', ''), high) if low != high else high,
                'source': d.get('dataset') or d.get('source'), 'tier': d.get('tier_label')}
    except Exception as exc:
        logger.warning('bio fact box: rate range unavailable for %s: %s', user_id, exc)
        return None


def last_updated(profile, bio_page) -> datetime | None:
    """Approximate content date (owner decision, no migration): the latest of the
    bio being generated, the page being published and the profile being saved.
    bio_pages.updated_at is not used — views and likes move it."""
    stamps = [getattr(profile, 'bio_generated_at', None), getattr(bio_page, 'published_at', None),
              getattr(profile, 'updated_at', None)]
    stamps = [s for s in stamps if s]
    return max(stamps) if stamps else None


def fact_box(profile, ctx: dict, bio_page=None) -> dict:
    """Rows for the infobox: [{'label', 'value', 'href'?, 'note'?}]."""
    ctx = ctx or {}
    career = [e for e in (ctx.get('career_history') or []) if isinstance(e, dict)]
    current = next((e for e in career if _is_current(e)), None)
    rows = []

    role = (ctx.get('professional_title') or (current or {}).get('role') or '').strip()
    if role:
        rows.append({'label': 'Role', 'value': role})
    if current and current.get('company_name'):
        rows.append({'label': 'Currently', 'value': current['company_name'],
                     'href': current.get('company_url') or None})
    if getattr(profile, 'location', None):
        rows.append({'label': 'Location', 'value': profile.location})
    years = years_of_experience(career)
    if years:
        rows.append({'label': 'Experience', 'value': '%d+ years' % years})
    zones = [z.get('zone') for z in (ctx.get('sim_zones') or []) if z.get('zone')][:3]
    if zones:
        rows.append({'label': 'Focus areas', 'value': ' · '.join(zones)})
    avail = availability(ctx.get('sim_bios'))
    if avail:
        rows.append({'label': 'Availability', 'value': avail})
    rate = rate_range(profile.user_id) if profile is not None else None
    if rate:
        note = ' · '.join(x for x in (('Source: ' + rate['source']) if rate.get('source') else '',
                                      rate.get('tier') or '') if x)
        rows.append({'label': 'Rate range', 'value': rate['text'], 'note': note or None})

    links = []
    for label, url in (('LinkedIn', ctx.get('linkedin_url')), ('Website', ctx.get('website_url')),
                       ('X / Twitter', ctx.get('twitter_url')),
                       (getattr(profile, 'other_link_label', None) or 'Link',
                        getattr(profile, 'other_link_url', None))):
        if url:
            links.append({'label': label, 'href': url})

    updated = last_updated(profile, bio_page)
    return {'rows': rows, 'links': links,
            'updated': '%s %d, %d' % (updated.strftime('%b'), updated.day, updated.year) if updated else None,
            'updated_iso': updated.date().isoformat() if updated else None}
