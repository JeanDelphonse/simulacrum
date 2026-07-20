"""
Agent Registry — loads config/agents.json and exposes structured interfaces.

Single source of truth per SIM-PRD-AGENTMAP-001 FR-MAP-01.
All 49 agents' properties are defined in agents.json; no agent behaviour
is hardcoded here.  This module makes them available to the rest of the
application and provides backward-compatibility aliases for old action_type
strings already stored in the database.
"""
from __future__ import annotations
import json
import os

_AGENTS_JSON_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    'config', 'agents.json',
)


def _load() -> dict:
    with open(_AGENTS_JSON_PATH, 'r', encoding='utf-8') as fh:
        return json.load(fh)


_data = _load()
_agents_flat: list[dict] = _data['agents']
_aliases: dict[str, str] = _data.get('aliases', {})

# Reverse alias map: canonical action_type → list of old names
_reverse_aliases: dict[str, list[str]] = {}
for _old, _canonical in _aliases.items():
    _reverse_aliases.setdefault(_canonical, []).append(_old)


# ---------------------------------------------------------------------------
# Public constants
# ---------------------------------------------------------------------------

# Layer-keyed agent dict: {layer_int: {action_type: {label, description, prompt_form, disclaimer?}}}
# Matches the shape of AGENT_ACTION_TYPES in app/services/claude.py.
AGENT_ACTION_TYPES: dict[int, dict[str, dict]] = {}
for _agent in _agents_flat:
    _layer = _agent['layer']
    AGENT_ACTION_TYPES.setdefault(_layer, {})
    _meta: dict = {
        'label': _agent['label'],
        'description': _agent['description'],
        'prompt_form': _agent.get('prompt_form', []),
    }
    if _agent.get('disclaimer'):
        _meta['disclaimer'] = True
    AGENT_ACTION_TYPES[_layer][_agent['action_type']] = _meta
    # NOTE: legacy aliases are intentionally NOT registered as separate keys
    # here — doing so inflated per-layer agent lists and progress-ring
    # denominators (e.g. layer 1 showed 14 instead of 11).  Legacy action_type
    # strings are resolved to their canonical name via resolve_alias() at every
    # lookup site instead.

# Prerequisite DAG: {action_type: [prerequisite_action_types]}
ACTION_PREREQUISITES: dict[str, list[str]] = {}
for _agent in _agents_flat:
    _prereqs = [p['action_type'] for p in _agent.get('prerequisites', [])]
    if _prereqs:
        ACTION_PREREQUISITES[_agent['action_type']] = _prereqs
        for _old_name in _reverse_aliases.get(_agent['action_type'], []):
            ACTION_PREREQUISITES.setdefault(_old_name, _prereqs)

# Cold start chain — ordered by cold_start_order
COLD_START_SEQUENCE: list[str] = [
    a['action_type']
    for a in sorted(
        (a for a in _agents_flat if a.get('cold_start_chain')),
        key=lambda a: a.get('cold_start_order', 999),
    )
]

# Cold start Bayesian priors — include aliases for backward compat
COLD_START_PRIORS: dict[str, float] = {
    a['action_type']: float(a['cold_start_prior'])
    for a in _agents_flat
    if 'cold_start_prior' in a
}
for _canonical, _prior in list(COLD_START_PRIORS.items()):
    for _old_name in _reverse_aliases.get(_canonical, []):
        COLD_START_PRIORS.setdefault(_old_name, _prior)


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def resolve_alias(action_type: str) -> str:
    """Return canonical action_type, resolving legacy aliases."""
    return _aliases.get(action_type, action_type)


def get_agent_meta(action_type: str) -> dict | None:
    """Full agent dict from agents.json, or None if not found (resolves aliases)."""
    canonical = resolve_alias(action_type)
    for a in _agents_flat:
        if a['action_type'] == canonical:
            return a
    return None


def get_all_agents() -> list[dict]:
    return list(_agents_flat)


def get_agents_by_layer(layer: int) -> list[dict]:
    return [a for a in _agents_flat if a['layer'] == layer]


def get_alias_map() -> dict[str, str]:
    return dict(_aliases)


def get_integrations(action_type: str) -> list[str]:
    meta = get_agent_meta(action_type)
    return meta.get('integrations', []) if meta else []


def is_hub_node(action_type: str) -> bool:
    meta = get_agent_meta(action_type)
    return bool(meta and meta.get('hub_node'))


def get_cold_start_prior(action_type: str) -> float:
    return COLD_START_PRIORS.get(action_type, COLD_START_PRIORS.get(resolve_alias(action_type), 0.1))
