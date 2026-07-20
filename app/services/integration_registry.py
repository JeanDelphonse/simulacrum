"""
Integration Registry — SIM-PRD-CONNECT-001.

Loads config/integrations.json and classifies the nine integrations into three
tiers that drive whether and how each surfaces to the user:

  brokered  — invisible platform infrastructure (internal_email, apollo, pandadoc).
              Always available; never gated, never presented as configuration.
  oauth     — one-tap, surfaced just-in-time when an agent needs it
              (stripe, cal_com, linkedin).
  advanced  — hidden behind a disclosure, never surfaced proactively
              (convertkit, kajabi, alpaca).

Static tier metadata lives here / in the JSON.  Per-user connections continue to
live in the UserIntegration table (`runtime_provider` is the UserIntegration
provider key; brokered integrations have no per-user row).
"""
from __future__ import annotations
import json
import os

TIER_BROKERED = 'brokered'
TIER_OAUTH = 'oauth'
TIER_ADVANCED = 'advanced'

_REGISTRY_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    'config', 'integrations.json',
)


def _load() -> list[dict]:
    with open(_REGISTRY_PATH, 'r', encoding='utf-8') as fh:
        return json.load(fh)['integrations']


_INTEGRATIONS: list[dict] = _load()
_BY_KEY: dict[str, dict] = {i['key']: i for i in _INTEGRATIONS}


# ---------------------------------------------------------------------------
# Lookups
# ---------------------------------------------------------------------------

def get_all() -> list[dict]:
    return list(_INTEGRATIONS)


def get(key: str) -> dict | None:
    return _BY_KEY.get(key)


def by_tier(tier: str) -> list[dict]:
    return [i for i in _INTEGRATIONS if i['tier'] == tier]


def classify(key: str) -> str | None:
    """Return the tier of an integration key, or None if unknown."""
    meta = _BY_KEY.get(key)
    return meta['tier'] if meta else None


def runtime_provider(key: str) -> str | None:
    """UserIntegration.provider key for this integration (None for brokered)."""
    meta = _BY_KEY.get(key)
    return meta.get('runtime_provider') if meta else None


def display_name(key: str) -> str:
    meta = _BY_KEY.get(key)
    return meta.get('display_name', key) if meta else key


def contextual_prompt(key: str) -> str | None:
    meta = _BY_KEY.get(key)
    return meta.get('contextual_prompt') if meta else None


def min_layer(key: str) -> int:
    meta = _BY_KEY.get(key)
    return int(meta.get('min_layer', 1)) if meta else 1


# ---------------------------------------------------------------------------
# Agent → integration requirements
# ---------------------------------------------------------------------------

def required_integration_keys(action_type: str) -> list[str]:
    """Registry keys an agent declares it needs (from agents.json)."""
    from app.services.agent_registry import get_integrations
    # agents.json integration strings already use the registry canonical keys
    # (stripe, cal_com, linkedin, internal_email, ...).
    return [k for k in get_integrations(action_type) if k in _BY_KEY]


def required_oauth_keys(action_type: str) -> list[str]:
    """The oauth-tier integrations an agent needs (the only tier that gates activation)."""
    return [k for k in required_integration_keys(action_type)
            if classify(k) == TIER_OAUTH]


# ---------------------------------------------------------------------------
# Per-user connection state
# ---------------------------------------------------------------------------

def has_connected(user_id: str, key: str) -> bool:
    """True if the user has this integration available.

    Brokered integrations are always available (platform infrastructure).
    oauth/advanced require a healthy, unexpired UserIntegration row.
    """
    tier = classify(key)
    if tier == TIER_BROKERED:
        return True
    provider = runtime_provider(key)
    if not provider:
        return False
    from app.models.integration import UserIntegration
    rec = UserIntegration.query.filter_by(user_id=user_id, provider=provider).first()
    return bool(rec and rec.is_connected and not rec.is_expired)


def missing_oauth_keys(action_type: str, user_id: str) -> list[str]:
    """oauth integrations an agent needs that the user has NOT connected."""
    return [k for k in required_oauth_keys(action_type)
            if not has_connected(user_id, k)]


def pending_connection_for(action_type: str, user_id: str) -> str | None:
    """The first unconnected oauth integration an agent needs, or None.

    Drives an artifact's `pending_connection` / 'connect to activate' state.
    """
    missing = missing_oauth_keys(action_type, user_id)
    return missing[0] if missing else None


def registry_key_for_provider(provider: str) -> str | None:
    """Reverse map a UserIntegration.provider key to its registry key."""
    for meta in _INTEGRATIONS:
        if meta.get('runtime_provider') == provider:
            return meta['key']
    return provider if provider in _BY_KEY else None
