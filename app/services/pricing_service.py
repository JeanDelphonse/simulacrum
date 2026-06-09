"""FR-DISC-10: Single source of truth for simulation pricing."""
from flask import current_app


def get_current_price() -> dict:
    """Return a pricing dict reflecting any active discount.

    Keys always present:
      base_price_cents       int   — base price from platform_settings
      discounted_price_cents int   — price to charge (== base if no discount)
      discount_percentage    int   — 0 if no discount
      is_discounted          bool
      label                  str|None
      expires_at             str|None  — ISO 8601 UTC, None if no discount
    """
    from app.models.platform_settings import PlatformSetting
    from app.models.discount import SimulationDiscount

    try:
        base = int(
            PlatformSetting.get('simulation_price')
            or current_app.config['SIMULATION_PRICE_CENTS']
        )
    except Exception:
        base = int(current_app.config.get('SIMULATION_PRICE_CENTS', 69500))

    discount = SimulationDiscount.get_active()
    if discount:
        discounted = int(base * (1 - discount.discount_percentage / 100))
        return {
            'base_price_cents': base,
            'discounted_price_cents': discounted,
            'discount_percentage': discount.discount_percentage,
            'is_discounted': True,
            'label': discount.label,
            'expires_at': discount.end_at.isoformat(),
        }
    return {
        'base_price_cents': base,
        'discounted_price_cents': base,
        'discount_percentage': 0,
        'is_discounted': False,
        'label': None,
        'expires_at': None,
    }


def get_prospect_tier_config() -> dict:
    """Return the admin-configurable prospect tier settings.

    Keys: tier1_count, tier2_count, tier2_price_cents, tier3_count, tier3_price_cents.
    All values are read from PlatformSetting with safe integer defaults.
    """
    from app.models.platform_settings import PlatformSetting

    def _int(key, default):
        try:
            return int(PlatformSetting.get(key) or default)
        except Exception:
            return default

    return {
        'tier1_count':        _int('prospect_tier1_count', 5),
        'tier2_count':        _int('prospect_tier2_count', 10),
        'tier2_price_cents':  _int('prospect_tier2_price_cents', 500),
        'tier3_count':        _int('prospect_tier3_count', 15),
        'tier3_price_cents':  _int('prospect_tier3_price_cents', 1000),
    }


def format_price_usd(cents: int) -> str:
    """Format cents as a USD string, e.g. 1595 → '$15.95', 69500 → '$695'."""
    return f'${cents // 100:,}' if cents % 100 == 0 else f'${cents / 100:,.2f}'
