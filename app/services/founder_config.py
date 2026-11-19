"""Founder Track admin settings (FOUNDER_PLAN.md section 2).

Every price, cap, gate and target the Founder Track uses comes from here. The
values live in platform_settings under keys prefixed fnd_; a key with no row
reads its default from DEFAULTS. set() validates by type before saving.

    founder_config.get('fnd_price_cents')            -> 49900
    founder_config.set('fnd_price_cents', 39900, admin_id)
    founder_config.pricing()                         -> {...}
"""
from __future__ import annotations

import copy
import json
import logging
import re
from datetime import datetime

logger = logging.getLogger(__name__)

PREFIX = 'fnd_'

DIMENSIONS = ('problem', 'customer', 'market', 'solution', 'gtm', 'business_model', 'operations', 'team',
              'fundraising')

# Autonomy levels (PRD, Investor connection): 1 advisor only, 2 approve each
# action, 3 approve the campaign, 4 act within limits. 0 = not permitted.
AUTONOMY_LEVELS = (0, 1, 2, 3, 4)

TARGET_KEYS = ('eval_completion', 'finding_accuracy', 'paid_poc_rate', 'step_completion_per_week',
               'intro_acceptance', 'application_submission', 'alignment_agreement')

PREVIEW_LEVELS = ('rung', 'scores', 'top3')
# Concierge uploads may only be types the app can read (PyMuPDF, python-docx).
CC_READABLE_TYPES = ('pdf', 'docx')
CC_MAX_UPLOAD_MB = 20        # same ceiling as self-serve uploads (founder_intake_service.UPLOAD_CAP_BYTES)
FEE_MODES = ('one_time', 'recurring')
FEE_MODES_BUILT = ('one_time',)          # 'recurring' is rejected until it is built

# key -> (type, default, extra). Types: int, str, choice, json, datetime.
_SPEC = {
    # Rubric
    'fnd_rubric_weights': ('weights', {d: 1.0 for d in DIMENSIONS}, None),
    # Readiness gates (minimum evidence rung before L5 agents unlock)
    'fnd_gate_rung_applications': ('int', 2, (1, 5)),
    'fnd_gate_rung_intros': ('int', 4, (1, 5)),
    # Next steps
    'fnd_next_alternates': ('int', 3, (0, 10)),
    # Free preview cut-off
    'fnd_preview_level': ('choice', 'scores', PREVIEW_LEVELS),
    # Prices (cents) and refunds
    'fnd_price_cents': ('int', 49900, (0, 10000000)),
    'fnd_addon_price_cents': ('int', 29900, (0, 10000000)),
    'fnd_investor_access_cents': ('int', 4900, (0, 10000000)),
    'fnd_refund_window_days': ('int', 14, (0, 365)),
    'fnd_fee_mode': ('choice', 'one_time', FEE_MODES_BUILT),
    # Agent caps
    'fnd_action_cap_per_day': ('int', 10, (0, 1000)),
    'fnd_budget_cap_cents': ('int', 0, (0, 100000000)),
    # Autonomy
    'fnd_autonomy_matrix': ('matrix', {
        'research': {'default': 4, 'max': 4},
        'intro_request': {'default': 3, 'max': 3},
        'followup': {'default': 3, 'max': 3},
        'first_contact': {'default': 2, 'max': 3},
        'application': {'default': 2, 'max': 3},
        'financial_message': {'default': 2, 'max': 2},
        'customer_message': {'default': 2, 'max': 3},
        'negotiate_terms': {'default': 0, 'max': 0},
    }, None),
    'fnd_first_contact_cap': ('int', 3, (1, 4)),
    # Targets (0-100 percentages, except step_completion_per_week: steps per venture per week)
    'fnd_targets': ('targets', {
        'eval_completion': 60, 'finding_accuracy': 80, 'paid_poc_rate': 20, 'step_completion_per_week': 2,
        'intro_acceptance': 30, 'application_submission': 70, 'alignment_agreement': 70,
    }, None),
    # Alerts
    'fnd_alert_error_rate_pct': ('int', 20, (0, 100)),
    'fnd_alert_cost_per_venture_cents': ('int', 2000, (0, 10000000)),
    'fnd_alert_stuck_days': ('int', 14, (1, 365)),
    # Co-founder invites (WP5) and startup-page requests (WP6)
    'fnd_invite_days': ('int', 30, (1, 365)),
    'fnd_invite_daily_cap': ('int', 10, (1, 1000)),
    'fnd_startup_request_limit': ('int', 5, (1, 1000)),
    'fnd_startup_page_request_limit': ('int', 30, (1, 10000)),
    # Retention
    'fnd_retention_days': ('int', 365, (30, 3650)),
    # Counsel sign-off (ISO date or datetime; empty = not signed off)
    'fnd_counsel_signoff_at': ('datetime', '', None),
    # Concierge onboarding (FOUNDER_CONCIERGE_PLAN.md 3.7). The consultation fee
    # has no price in code: 0 means not offered until an admin sets it.
    'fnd_concierge_fee_cents': ('int', 0, (0, 10000000)),
    'fnd_concierge_active': ('bool', False, None),
    'fnd_concierge_currency': ('currency', 'usd', None),
    'fnd_concierge_stripe_price_id': ('price_id', '', None),
    'fnd_cc_window_days': ('int', 30, (1, 365)),
    'fnd_cc_reminder_days': ('reminders', [7, 21, -3], None),
    'fnd_cc_extend_days': ('int', 14, (1, 365)),
    'fnd_cc_allowed_types': ('types', ['pdf', 'docx'], CC_READABLE_TYPES),
    'fnd_cc_max_upload_mb': ('int', 20, (1, CC_MAX_UPLOAD_MB)),
}

DEFAULTS = {k: v[1] for k, v in _SPEC.items()}


class FounderConfigError(ValueError):
    pass


def _raw(key: str):
    try:
        from app.models.platform_settings import PlatformSetting
        return PlatformSetting.get(key, None)
    except Exception as exc:
        logger.error('founder setting %s unreadable, using default: %s', key, exc)
        return None


def _parse_json(value):
    if isinstance(value, (dict, list)):
        return value
    return json.loads(value)


def _validate(key: str, value):
    """Return the clean value, or raise FounderConfigError."""
    if key not in _SPEC:
        raise FounderConfigError('Unknown Founder Track setting: %s' % key)
    kind, default, extra = _SPEC[key]
    if kind == 'int':
        if isinstance(value, bool):
            raise FounderConfigError('%s must be a whole number.' % key)
        try:
            n = int(str(value).strip())
        except (TypeError, ValueError):
            raise FounderConfigError('%s must be a whole number.' % key)
        lo, hi = extra
        if n < lo or n > hi:
            raise FounderConfigError('%s must be between %d and %d.' % (key, lo, hi))
        return n
    if kind == 'choice':
        v = str(value or '').strip()
        if key == 'fnd_fee_mode' and v == 'recurring':
            raise FounderConfigError('Recurring fees are not built yet; the fee mode stays one_time.')
        if v not in extra:
            raise FounderConfigError('%s must be one of: %s.' % (key, ', '.join(extra)))
        return v
    if kind == 'bool':
        if isinstance(value, bool):
            return value
        v = str(value if value is not None else '').strip().lower()
        if v in ('1', 'true', 'yes', 'on'):
            return True
        if v in ('0', 'false', 'no', 'off', ''):
            return False
        raise FounderConfigError('%s must be true or false.' % key)
    if kind == 'currency':
        v = str(value or '').strip().lower()
        if not re.match(r'^[a-z]{3}$', v):
            raise FounderConfigError('%s must be a three-letter currency code, e.g. usd.' % key)
        return v
    if kind == 'price_id':
        v = str(value or '').strip()
        if v and not re.match(r'^price_[A-Za-z0-9]{3,200}$', v):
            raise FounderConfigError('%s must be a Stripe price id (price_...) or empty.' % key)
        return v
    if kind in ('reminders', 'types'):
        try:
            data = _parse_json(value) if not isinstance(value, (list, tuple)) else list(value)
        except (TypeError, ValueError):
            data = [p for p in re.split(r'[\s,;]+', str(value or '')) if p]
        if isinstance(data, (int, float, str)) and not isinstance(data, bool):
            data = [data]
        if not isinstance(data, list):
            raise FounderConfigError('%s must be a list.' % key)
        if kind == 'types':
            out = []
            for t in data:
                t = str(t or '').strip().lower().lstrip('.')
                if t not in extra:
                    raise FounderConfigError('Allowed types can only be types SimiSI can read: %s.' % ', '.join(extra))
                if t not in out:
                    out.append(t)
            if not out:
                raise FounderConfigError('Allow at least one file type.')
            return out
        out = []
        for d in data:
            try:
                if isinstance(d, bool):
                    raise ValueError
                n = int(str(d).strip())
            except (TypeError, ValueError):
                raise FounderConfigError('Reminder days must be whole numbers (negative = days before expiry).')
            if n == 0 or abs(n) > 365:
                raise FounderConfigError('Reminder days must be between -365 and 365, and not 0.')
            if n not in out:
                out.append(n)
        if len(out) > 10:
            raise FounderConfigError('Ten reminders at most.')
        return out
    if kind == 'datetime':
        v = str(value or '').strip()
        if not v:
            return ''
        try:
            datetime.fromisoformat(v)
        except ValueError:
            raise FounderConfigError('%s must be a date (YYYY-MM-DD).' % key)
        return v
    try:
        data = _parse_json(value)
    except (TypeError, ValueError):
        raise FounderConfigError('%s must be JSON.' % key)
    if not isinstance(data, dict):
        raise FounderConfigError('%s must be a JSON object.' % key)
    if kind == 'weights':
        out = {}
        for d in DIMENSIONS:
            w = data.get(d, default[d])
            try:
                w = float(w)
            except (TypeError, ValueError):
                raise FounderConfigError('Weight for %s must be a number.' % d)
            if w < 0 or w > 10:
                raise FounderConfigError('Weight for %s must be between 0 and 10.' % d)
            out[d] = w
        unknown = frozenset(data) - frozenset(DIMENSIONS)
        if unknown:
            raise FounderConfigError('Unknown rubric dimensions: %s.' % ', '.join(sorted(unknown)))
        if sum(out.values()) <= 0:
            raise FounderConfigError('At least one rubric weight must be above zero.')
        return out
    if kind == 'matrix':
        out = copy.deepcopy(default)
        unknown = frozenset(data) - frozenset(default)
        if unknown:
            raise FounderConfigError('Unknown action types: %s.' % ', '.join(sorted(unknown)))
        for action, row in data.items():
            if not isinstance(row, dict):
                raise FounderConfigError('Autonomy row %s must be an object.' % action)
            try:
                lvl = int(row.get('default', out[action]['default']))
                mx = int(row.get('max', out[action]['max']))
            except (TypeError, ValueError):
                raise FounderConfigError('Autonomy levels for %s must be whole numbers.' % action)
            if lvl not in AUTONOMY_LEVELS or mx not in AUTONOMY_LEVELS:
                raise FounderConfigError('Autonomy levels for %s must be 0-4.' % action)
            if lvl > mx:
                raise FounderConfigError('The default level for %s is above its maximum.' % action)
            if action == 'negotiate_terms' and (lvl or mx):
                raise FounderConfigError('Negotiating or accepting terms is never permitted.')
            out[action] = {'default': lvl, 'max': mx}
        return out
    if kind == 'targets':
        out = dict(default)
        unknown = frozenset(data) - frozenset(TARGET_KEYS)
        if unknown:
            raise FounderConfigError('Unknown targets: %s.' % ', '.join(sorted(unknown)))
        for k, v in data.items():
            try:
                f = float(v)
            except (TypeError, ValueError):
                raise FounderConfigError('Target %s must be a number.' % k)
            if f < 0 or (k != 'step_completion_per_week' and f > 100):
                raise FounderConfigError('Target %s is out of range.' % k)
            out[k] = f
        return out
    raise FounderConfigError('Unsupported setting type for %s.' % key)


def get(key: str):
    """The typed value for key, or its default. Never raises for a known key."""
    if key not in _SPEC:
        raise KeyError(key)
    raw = _raw(key)
    if raw in (None, ''):
        return copy.deepcopy(DEFAULTS[key])
    try:
        return _validate(key, raw)
    except FounderConfigError as exc:
        logger.error('founder setting %s holds a bad value (%r), using default: %s', key, raw, exc)
        return copy.deepcopy(DEFAULTS[key])


def set(key: str, value, admin_id: str):     # noqa: A001 (interface name fixed by FOUNDER_PLAN section 5)
    """Validate and save. Returns the clean value. Raises FounderConfigError."""
    clean = _validate(key, value)
    if key in ('fnd_autonomy_matrix', 'fnd_first_contact_cap'):
        matrix = clean if key == 'fnd_autonomy_matrix' else get('fnd_autonomy_matrix')
        cap = clean if key == 'fnd_first_contact_cap' else get('fnd_first_contact_cap')
        row = (matrix or {}).get('first_contact') or {}
        if int(row.get('max', 0) or 0) > int(cap) or int(row.get('default', 0) or 0) > int(cap):
            raise FounderConfigError('First contact cannot go above the first-contact cap (level %d).' % int(cap))
    if key == 'fnd_counsel_signoff_at' and not clean:
        from app.services import founder_feature
        if founder_feature.mode(founder_feature.INVESTOR) != 'off':
            raise FounderConfigError('Turn investor connection off before clearing the counsel sign-off date.')
    from app.models.audit_log import AuditLog
    from app.models.platform_settings import PlatformSetting
    stored = json.dumps(clean) if isinstance(clean, (dict, list)) else str(clean)
    AuditLog.log('founder_setting', user_id=admin_id, metadata={'key': key, 'value': stored[:500]})
    PlatformSetting.set(key, stored, updated_by=admin_id)    # commits
    return clean


def all_settings() -> dict:
    return {k: get(k) for k in _SPEC}


def counsel_signed_off() -> bool:
    return bool(get('fnd_counsel_signoff_at'))


def pricing() -> dict:
    """Prices and refund terms for checkout pages and the admin Pricing tab."""
    return {
        'price_cents': get('fnd_price_cents'),
        'addon_price_cents': get('fnd_addon_price_cents'),
        'investor_access_cents': get('fnd_investor_access_cents'),
        'refund_window_days': get('fnd_refund_window_days'),
        'fee_mode': get('fnd_fee_mode'),
        'currency': 'usd',
    }


def concierge_pricing() -> dict:
    """The consultation (concierge) fee settings. fee_cents 0 = not offered."""
    return {
        'fee_cents': get('fnd_concierge_fee_cents'),
        'active': bool(get('fnd_concierge_active')),
        'currency': get('fnd_concierge_currency'),
        'stripe_price_id': get('fnd_concierge_stripe_price_id'),
        'refund_window_days': get('fnd_refund_window_days'),
    }
