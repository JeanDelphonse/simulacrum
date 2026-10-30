"""What the public marketing site renders from (SITE_UPDATE_PLAN step 0).

Two shared contexts so the site can never drift from the product:

  pricing_context()  the simulation price, read at render time from the same
                     source the checkout charges (pricing_service.get_current_price,
                     which checkout's price_for_user wraps). Templates must use
                     these values — never a literal dollar amount.
  site_layers()      the five income layers: names from one constant here,
                     agent counts from config/agents.json.
"""
from __future__ import annotations

import logging
from collections import Counter

logger = logging.getLogger(__name__)

# Exactly five income layers. The orchestrator ("Layer 6" in the app) is not an
# income layer and is deliberately not listed.
LAYERS = (
    {'n': 1, 'code': 'L1', 'name': 'Active Income', 'tag': 'Trade time for money — strategically',
     'lens': 'Optimal Stopping', 'badge': '#0B4A46', 'badge_text': '#fff', 'bg': 'rgba(11,74,70,.16)'},
    {'n': 2, 'code': 'L2', 'name': 'Leveraged Income', 'tag': 'Earn beyond the hour',
     'lens': 'Explore vs. Exploit', 'badge': '#0F7B72', 'badge_text': '#fff', 'bg': 'rgba(15,123,114,.10)'},
    {'n': 3, 'code': 'L3', 'name': 'Productized Income', 'tag': 'Expertise that sells without you in the room',
     'lens': 'Caching', 'badge': '#139488', 'badge_text': '#fff', 'bg': 'rgba(19,148,136,.10)'},
    {'n': 4, 'code': 'L4', 'name': 'Automated Residual Income', 'tag': 'Systems that run while you sleep',
     'lens': 'Scheduling', 'badge': '#13A89E', 'badge_text': '#fff', 'bg': 'rgba(19,168,158,.10)'},
    {'n': 5, 'code': 'L5', 'name': 'Wealth Deployment', 'tag': 'Turn income into compounding capital',
     'lens': 'Overfitting & Relaxation', 'badge': '#C9952A', 'badge_text': '#241a05', 'bg': 'rgba(201,149,42,.12)'},
)
LAYER_COUNT = len(LAYERS)


def site_layers() -> dict:
    """{'layers': [...], 'count': 5, 'agent_total': 49}, each layer with its
    agent count from agents.json."""
    from app.services import agent_registry
    counts = Counter(a.get('layer') for a in agent_registry._agents_flat)
    layers = [dict(layer, agents=counts.get(layer['n'], 0)) for layer in LAYERS]
    return {'layers': layers, 'count': LAYER_COUNT,
            'agent_total': sum(layer['agents'] for layer in layers)}


def pricing_context() -> dict:
    """original_price / current_price / discount_pct / is_discounted for display.
    Display only: the charge is decided by pricing_service at checkout."""
    from app.services.pricing_service import format_price_usd, get_current_price
    try:
        p = get_current_price()
    except Exception as exc:
        logger.warning('site pricing unavailable: %s', exc)
        return {'available': False, 'original_price': None, 'current_price': None,
                'discount_pct': 0, 'is_discounted': False, 'label': None, 'expires_at': None}
    return {
        'available': True,
        'original_price': format_price_usd(p['base_price_cents']),
        'current_price': format_price_usd(p['discounted_price_cents']),
        'discount_pct': p['discount_percentage'],
        'is_discounted': bool(p['is_discounted']),
        'label': p.get('label'),
        'expires_at': p.get('expires_at'),
    }


# What each integration unlocks, in marketing words. Keyed by
# config/integrations.json keys: an integration removed there disappears from
# the site. `rolling_out` marks what isn't live yet; keep it honest.
_UNLOCKS = (
    ('internal_email', 'Outreach', 'Email',
     'Sends outreach under your name on the channels you switch on — or from your own domain once you connect SendGrid.', None),
    ('apollo', 'Outreach', 'Apollo',
     'Finds real decision-makers to contact. Runs on SimiSI\'s Apollo seats — no account needed.', None),
    ('convertkit', 'Outreach', 'ConvertKit',
     'Publishes your newsletters and lead magnets to your own list.', None),
    ('cal_com', 'Booking', 'Cal.com',
     'Sets up your booking page so prospects can book calls with you.', None),
    ('stripe', 'Publish & get paid', 'Stripe',
     'Creates payment links on your own Stripe account, with your approval.', None),
    ('kajabi', 'Publish & get paid', 'Kajabi',
     'Publishes courses and memberships to your Kajabi account.', 'rolling out'),
    ('pandadoc', 'Proposals', 'PandaDoc',
     'Turns your rate card and scope into proposals and agreements.', None),
    ('linkedin', 'Profile', 'LinkedIn',
     'Imports your profile to build your bio page. Posting for you is rolling out.', None),
    ('alpaca', 'Wealth', 'Alpaca',
     'Compares your allocation plan with your brokerage — advisory only; SimiSI never places trades.', 'rolling out'),
)


def integration_unlocks() -> list:
    try:
        from app.services import integration_registry
        known = {i['key'] for i in integration_registry.get_all()}
    except Exception:
        known = None
    return [{'key': k, 'group': g, 'name': n, 'unlocks': u, 'rolling_out': bool(r)}
            for k, g, n, u, r in _UNLOCKS if known is None or k in known]
