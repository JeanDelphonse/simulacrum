from app.models.platform_settings import PlatformSetting
import logging

logger = logging.getLogger(__name__)


def is_fintech_enabled() -> bool:
    """Check if live fintech API integration is enabled via admin toggle."""
    value = PlatformSetting.get('fintech_toggle', 'off')
    return value.lower() == 'on'


def get_wealth_recommendations(income_profile: dict) -> dict:
    """
    Return Layer 5 wealth deployment data.
    When fintech toggle is on, fetches real fund/portfolio data.
    When off, returns static guidance.
    """
    if not is_fintech_enabled():
        return _static_wealth_guidance(income_profile)

    try:
        return _live_fintech_recommendations(income_profile)
    except Exception as e:
        logger.error(f'Fintech API error, falling back to static guidance: {e}')
        return _static_wealth_guidance(income_profile)


def _static_wealth_guidance(income_profile: dict) -> dict:
    return {
        'mode': 'static',
        'asset_classes': [
            {'name': 'Index Funds / ETFs', 'allocation': '60%', 'rationale': 'Low-cost broad market exposure for long-term compounding'},
            {'name': 'Real Estate Investment Trusts (REITs)', 'allocation': '20%', 'rationale': 'Passive real estate exposure without direct ownership'},
            {'name': 'High-Yield Savings / Cash Equivalents', 'allocation': '10%', 'rationale': 'Emergency fund and short-term liquidity'},
            {'name': 'Angel Investing / Private Equity', 'allocation': '10%', 'rationale': 'High-risk/high-reward allocation for accredited investors'},
        ],
        'platforms': ['Fidelity', 'Vanguard', 'Schwab', 'M1 Finance'],
        'note': 'These are general recommendations only. Consult a licensed financial advisor.',
    }


def _live_fintech_recommendations(income_profile: dict) -> dict:
    # Placeholder for Plaid/Alpaca integration
    # When toggle is on, this would make real API calls
    logger.info('Live fintech integration called — not yet fully implemented')
    return _static_wealth_guidance(income_profile)
