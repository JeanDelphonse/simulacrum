"""Founder agent catalog (FOUNDER_PLAN.md section 4).

Reads config/founder_agents.json only. The 49 wealth agents in
config/agents.json (agent_registry) are a separate catalog and are never mixed
with these: every founder key starts with fnd_.
"""
from __future__ import annotations

import copy
import json
import os

_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                     'config', 'founder_agents.json')

LAYER_NAMES = {1: 'Validate', 2: 'Build', 3: 'Sell', 4: 'Operate', 5: 'Capitalize'}

PARAM_KEYS = ('autonomy_level', 'scope', 'budget_cap', 'action_cap', 'stopping_rule', 'voice', 'success_metric')

EXTERNAL_KINDS = ('none', 'customer', 'investor')

DIMENSIONS = ('problem', 'customer', 'market', 'solution', 'gtm', 'business_model', 'operations', 'team',
              'fundraising')

_DOC = None


def _load() -> dict:
    global _DOC
    if _DOC is None:
        with open(_PATH, encoding='utf-8') as f:
            _DOC = json.load(f)
    return _DOC


def reload() -> None:
    """Re-read the catalog (tests, or after editing the JSON)."""
    global _DOC
    _DOC = None
    # Update in place so modules that imported these names see the new catalog.
    _BY_KEY.clear()
    _BY_KEY.update({a['action_type']: a for a in _load()['agents']})
    DIMENSION_AGENTS.clear()
    DIMENSION_AGENTS.update(_dimension_agents())


def all_agents() -> list:
    """Every founder agent, in catalog order (copies, safe to mutate)."""
    return copy.deepcopy(_load()['agents'])


def get(key: str):
    a = _BY_KEY.get(key)
    return copy.deepcopy(a) if a else None


def by_layer(n: int) -> list:
    return [copy.deepcopy(a) for a in _load()['agents'] if a['layer'] == int(n)]


def keys() -> list:
    return [a['action_type'] for a in _load()['agents']]


def rules() -> list:
    """The shared honesty and no-advice rules every founder prompt carries."""
    return list(_load().get('rules', []))


def default_params(key: str) -> dict:
    a = _BY_KEY.get(key)
    return copy.deepcopy(a['default_params']) if a else {}


def _dimension_agents() -> dict:
    out = {d: [] for d in DIMENSIONS}
    for a in _load()['agents']:
        for d in a.get('dimensions', []):
            out.setdefault(d, []).append(a['action_type'])
    return out


_BY_KEY = {a['action_type']: a for a in _load()['agents']}
# Rubric dimension -> candidate agents, in catalog order (lower layers first).
DIMENSION_AGENTS = _dimension_agents()
