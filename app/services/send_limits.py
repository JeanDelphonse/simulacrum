"""Send limits for commercial email (COMPLIANCE_PLAN C3).

Admin settings (platform_settings) with safe defaults; nothing is hardcoded at
the call sites. Enforcement lives in check_send() and the auto-pause in
review_campaign(); both are added in step 13.
"""
from __future__ import annotations

DEFAULTS = {
    'send_cap_per_user_day': '50',          # commercial emails per member per UTC day
    'send_cap_per_campaign_day': '25',      # per agent action (campaign) per UTC day
    'warmup_schedule': '10,15,20,25,30,40,50',   # daily cap for days 1..7 of a new sender
    'pause_bounce_rate_pct': '5',           # 7-day bounce rate that pauses a campaign
    'pause_complaint_rate_pct': '0.3',      # 7-day spam-complaint rate that pauses it
    'pause_min_sample': '20',               # sends needed before the rates are judged
}


def parse(key: str, value: str):
    """Validate an Admin value; returns the parsed form."""
    v = (value or '').strip()
    if key == 'warmup_schedule':
        try:
            steps = [int(x) for x in v.split(',') if x.strip()]
        except ValueError:
            raise ValueError('Warm-up schedule must be whole numbers separated by commas.')
        if not steps or any(n < 1 for n in steps):
            raise ValueError('Warm-up schedule needs at least one positive number.')
        return steps
    try:
        n = float(v)
    except ValueError:
        raise ValueError('%s must be a number.' % key)
    if n < 0:
        raise ValueError('%s cannot be negative.' % key)
    return n


def setting(key: str):
    from app.models.platform_settings import PlatformSetting
    try:
        raw = PlatformSetting.get(key, DEFAULTS[key])
        return parse(key, raw if raw not in (None, '') else DEFAULTS[key])
    except Exception:
        return parse(key, DEFAULTS[key])
