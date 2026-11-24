"""Year-to-date US layoffs from the Labor Department (BLS JOLTS).

The Transition Package walkthrough opens on these figures; the video itself
carries no numbers, and the page draws them over it. Two JOLTS series, not
seasonally adjusted so the months add up to a year-to-date total:

    JTU000000000000000LDL   layoffs and discharges, total nonfarm (thousands)
    JTU510000000000000LDL   layoffs and discharges, information (thousands)

Results are cached in platform_settings (shared by every Passenger process)
and refreshed at most every CACHE_HOURS. If BLS can't be reached the last
good figures stay up; a failed fetch is not retried for RETRY_MINUTES. Set
BLS_API_KEY to use a registered key (higher daily limits); the public API
works without one.
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

API_URL = 'https://api.bls.gov/publicAPI/v2/timeseries/data/'
TOTAL = 'JTU000000000000000LDL'
INFO = 'JTU510000000000000LDL'
CACHE_KEY = 'labor_layoffs_cache'
CACHE_HOURS = 12
RETRY_MINUTES = 60
TIMEOUT = 4
SOURCE = 'U.S. Bureau of Labor Statistics, JOLTS'
SOURCE_URL = 'https://www.bls.gov/jlt/'
MONTHS = ('Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec')

_memo: dict = {}          # this process: {'data': ..., 'checked': epoch, 'failed': epoch}


def summarize(series: dict) -> dict | None:
    """Year-to-date totals from BLS series data ({series_id: [rows]}).

    Uses the latest year with total-nonfarm data and only the months both
    series report, so the two figures always cover the same period.
    """
    def monthly(rows):
        out = {}
        for r in rows or []:
            p = str(r.get('period', ''))
            if not (p.startswith('M') and p[1:].isdigit()) or not 1 <= int(p[1:]) <= 12:
                continue                      # M13 is the annual average
            try:
                val = float(str(r.get('value', '')).replace(',', ''))
                year = int(r.get('year'))
            except (TypeError, ValueError):
                continue
            prelim = any((f or {}).get('code') == 'P' for f in (r.get('footnotes') or []))
            out[(year, int(p[1:]))] = (val, prelim)
        return out

    tot, inf = monthly(series.get(TOTAL)), monthly(series.get(INFO))
    if not tot:
        return None
    year = max(y for y, _ in tot)
    months = sorted(m for (y, m) in tot if y == year and (y, m) in inf)
    if not months:
        return None
    first, last = months[0], months[-1]
    return {
        'year': year,
        'first_month': first,
        'last_month': last,
        'period': ('%s %d' % (MONTHS[first - 1], year) if first == last
                   else '%s–%s %d' % (MONTHS[first - 1], MONTHS[last - 1], year)),
        'total': int(round(sum(tot[(year, m)][0] for m in months) * 1000)),
        'info': int(round(sum(inf[(year, m)][0] for m in months) * 1000)),
        'preliminary': tot[(year, last)][1] or inf[(year, last)][1],
        'source': SOURCE,
        'source_url': SOURCE_URL,
    }


def fetch() -> dict | None:
    """Ask BLS for this year and last; None on any failure."""
    import requests
    year = datetime.utcnow().year
    body = {'seriesid': [TOTAL, INFO], 'startyear': str(year - 1), 'endyear': str(year)}
    key = (os.environ.get('BLS_API_KEY') or '').strip()
    if key:
        body['registrationkey'] = key
    try:
        r = requests.post(API_URL, json=body, timeout=TIMEOUT)
        r.raise_for_status()
        payload = r.json()
    except Exception as exc:
        logger.warning('BLS fetch failed: %s', exc)
        return None
    if payload.get('status') != 'REQUEST_SUCCEEDED':
        logger.warning('BLS fetch refused: %s', payload.get('message'))
        return None
    series = {s.get('seriesID'): s.get('data') for s in (payload.get('Results') or {}).get('series') or []}
    data = summarize(series)
    if data:
        data['fetched_at'] = datetime.utcnow().isoformat(timespec='seconds')
    return data


def _load_cache() -> dict | None:
    try:
        from app.models.platform_settings import PlatformSetting
        raw = PlatformSetting.get(CACHE_KEY)
        return json.loads(raw) if raw else None
    except Exception:
        return None


def _save_cache(data: dict) -> None:
    try:
        from app.models.platform_settings import PlatformSetting
        PlatformSetting.set(CACHE_KEY, json.dumps(data))
    except Exception as exc:
        logger.warning('could not cache BLS figures: %s', exc)
        try:
            from app.extensions import db
            db.session.rollback()
        except Exception:
            pass


def _fresh(data: dict | None) -> bool:
    try:
        at = datetime.fromisoformat(data['fetched_at'])
    except Exception:
        return False
    return datetime.utcnow() - at < timedelta(hours=CACHE_HOURS)


def layoffs() -> dict | None:
    """The latest year-to-date figures, or None if none have ever loaded
    (the page then shows the walkthrough without the opening numbers)."""
    now = time.time()
    data = _memo.get('data')
    if data and now - _memo.get('checked', 0) < 600 and _fresh(data):
        return data
    cached = _load_cache()
    if cached and _fresh(cached):
        _memo.update(data=cached, checked=now)
        return cached
    if now - _memo.get('failed', 0) < RETRY_MINUTES * 60:
        return cached or data
    new = fetch()
    if new:
        _save_cache(new)
        _memo.update(data=new, checked=now, failed=0)
        return new
    _memo['failed'] = now
    return cached or data
