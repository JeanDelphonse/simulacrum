"""PRD-SIM-WORLD-001 — the Wealth Engine World read model.

The world is a *view*. Configuration is the model (PRD §6), and the model
already exists in three places this module reads and never duplicates:

    active      -> simulations.selected_agents      (SIM-PRD-AGENTSEL-001)
    configured  -> agent_context rows matching the agent's prompt_form keys
    value       -> calibration_runs, then income_streams, then nothing

The PRD (§7) offers to add `is_active` / `is_configured` / `projected_value`
columns to the agent-instance table if they do not already exist. They do
exist, spread across those three stores, so adding columns would manufacture
exactly the second source of truth §6 forbids. Nothing is written here; the
world writes through the shared Layer Actions path instead.

Numbers are never invented. A node carries a per-cycle value only when the
simulation actually produced one — a calibrated field or a projected income
stream — and says which. Everything else renders as an enabler, at zero.
"""

from __future__ import annotations

import json
import logging
import os
import time

from app.models.platform_settings import PlatformSetting

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Admin-configurable settings (standing convention: never hardcode a threshold)
# ---------------------------------------------------------------------------

# key -> (type, default, label, help). Editable through the existing
# PUT /api/admin/settings/<key> endpoint; no per-feature admin screen needed.
SETTINGS_SPEC = {
    'world_enabled': (
        'bool', True, 'Wealth Engine World enabled',
        'Master switch. When off the network tab renders only the legacy list view.'),
    'world_default_view': (
        'str', 'world', 'Default network-tab view',
        'world or list. The per-user toggle overrides this once they choose.'),
    'world_default_scope': (
        'str', 'selected', 'Default agent scope',
        'selected shows the agents Manage Agents lists as active for the simulation — '
        'the default, and what the world is for. configured narrows further to agents '
        'whose parameters are filled in; all shows the whole catalogue so an inactive '
        'agent can be switched on from the canvas.'),
    'world_allocation_pct': (
        'int', 45, 'Share of cycle income routed to wealth (%)',
        'Applied on a run cycle when an allocation (router) agent is active.'),
    'world_compound_pct': (
        'float', 1.0, 'Wealth compounding per cycle (%)',
        'Applied on a run cycle when an investing (compounding) agent is active.'),
    'world_auto_interval_ms': (
        'int', 2600, 'Auto-run interval (ms)',
        'Delay between cycles while Auto is engaged.'),
    'world_token_duration_ms': (
        'int', 850, 'Value token travel time (ms)',
        'How long a gold token takes to cross one live edge.'),
    'world_tokens_max': (
        'int', 5, 'Maximum tokens per edge',
        'Token count scales with the source agent\'s value, capped here so a large '
        'rate card does not flood the canvas.'),
    'world_token_value_step': (
        'int', 500, 'Dollars per value token',
        'One extra token per this much monthly value on the source agent.'),
    'world_suggest_connected_weight': (
        'float', 2.0, 'Orchestrator weight for connected agents',
        'Multiplier applied to an inactive agent that already touches an active one.'),
    'world_suggest_prereq_weight': (
        'float', 4.0, 'Orchestrator weight for missing prerequisites',
        'Multiplier applied to an inactive agent that an already-selected agent depends '
        'on. Above the connected weight so foundations are suggested first (PRD §9).'),
    'world_enabler_value': (
        'int', 120, 'Scoring value for an enabler agent',
        'Stand-in used only for ranking suggestions, never shown as income.'),
    'world_router_value': (
        'int', 300, 'Scoring value for a router agent',
        'Stand-in used only for ranking suggestions, never shown as income.'),
    'world_compound_value': (
        'int', 400, 'Scoring value for a compounding agent',
        'Stand-in used only for ranking suggestions, never shown as income.'),
    'world_root_multiplier': (
        'float', 1.15, 'Rate-card income multiplier (fallback)',
        'Used when the rate card has no calibrated hourly rate to compare against '
        'the reference median.'),
    'world_root_multiplier_min': (
        'float', 0.80, 'Rate-card multiplier floor',
        'Lower clamp on the measured rate-to-market ratio.'),
    'world_root_multiplier_max': (
        'float', 1.60, 'Rate-card multiplier ceiling',
        'Upper clamp on the measured rate-to-market ratio.'),
    'world_layer_focus_threshold': (
        'int', 26, 'Node count that triggers layer focus',
        'Above this many visible agents the canvas opens focused on one layer (PRD §11).'),
    'world_cache_ttl_seconds': (
        'int', 300, 'Read-model cache lifetime (seconds)',
        'The world is read-heavy and config changes are rare, so the model is memoized '
        'and invalidated on every write.'),
}

_TRUE = ('1', 'true', 'yes', 'on', 'True')


def setting(key):
    """Read one world setting, coerced to the type declared in SETTINGS_SPEC."""
    kind, default = SETTINGS_SPEC[key][0], SETTINGS_SPEC[key][1]
    raw = PlatformSetting.get(key)
    if raw is None or raw == '':
        return default
    if kind == 'bool':
        return str(raw) in _TRUE
    if kind == 'int':
        try:
            return int(raw)
        except (TypeError, ValueError):
            return default
    if kind == 'float':
        try:
            return float(raw)
        except (TypeError, ValueError):
            return default
    return str(raw)


def all_settings():
    """Every world setting with its current value and metadata, for an admin panel."""
    out = []
    for key, (kind, default, label, help_text) in SETTINGS_SPEC.items():
        out.append({
            'key': key, 'type': kind, 'default': default,
            'label': label, 'help': help_text, 'value': setting(key),
        })
    return out


# ---------------------------------------------------------------------------
# Zone theme kit (PRD §5.5)
# ---------------------------------------------------------------------------

_ZONES_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    'config', 'world_zones.json',
)


def _load_zones() -> dict:
    with open(_ZONES_PATH, 'r', encoding='utf-8') as fh:
        return json.load(fh)


_ZONE_KIT = _load_zones()
_ZONE_PACKS = _ZONE_KIT.get('zones', [])
_ZONE_ICONS = _ZONE_KIT.get('icons', {})
_LAYER_ICONS = _ZONE_KIT.get('layer_icons', {})


def resolve_zone(expertise_zone: str) -> dict:
    """Pick the theme pack whose match list appears in the simulation's zone."""
    needle = (expertise_zone or '').lower()
    for pack in _ZONE_PACKS:
        if any(token in needle for token in pack.get('match', [])):
            return pack
    default_key = _ZONE_KIT.get('default_zone')
    for pack in _ZONE_PACKS:
        if pack.get('key') == default_key:
            return pack
    return _ZONE_PACKS[0] if _ZONE_PACKS else {
        'key': 'default', 'name': 'General', 'accent': '#C9952A', 'labels': {},
    }


def zone_options() -> list:
    """Every theme pack, for the canvas zone selector.

    Labels travel with the pack so switching zones re-skins the canvas without a
    round trip — the packs are four small label maps, not a payload worth paging.
    """
    return [
        {'key': p['key'], 'name': p['name'], 'accent': p['accent'],
         'labels': p.get('labels', {})}
        for p in _ZONE_PACKS
    ]


# ---------------------------------------------------------------------------
# Node roles — how an agent behaves in the value flow
# ---------------------------------------------------------------------------

ROLE_FLOW = 'flow'              # produces income per cycle
ROLE_MULTIPLIER = 'multiplier'  # scales the whole engine's income
ROLE_ROUTER = 'router'          # moves income into wealth
ROLE_COMPOUND = 'compound'      # grows accumulated wealth
ROLE_ENABLER = 'enabler'        # makes other agents possible, earns nothing itself

_MULTIPLIER_AGENTS = frozenset(['rate_card'])
_ROUTER_AGENTS = frozenset(['income_allocation'])
_COMPOUND_AGENTS = frozenset([
    'fund_recommendations', 'dca_schedule', 'investment_policy', 'real_estate_strategy',
])

LAYER_NAMES = {
    1: 'Active Income', 2: 'Leveraged Income', 3: 'Productized Income',
    4: 'Automated Residual', 5: 'Wealth Deployment',
}
LAYER_COLORS = {
    1: '#0B4A46', 2: '#0F7B72', 3: '#139488', 4: '#13A89E', 5: '#C9952A',
}


def _role_for(action_type: str, value: float) -> str:
    if action_type in _MULTIPLIER_AGENTS:
        return ROLE_MULTIPLIER
    if action_type in _ROUTER_AGENTS:
        return ROLE_ROUTER
    if action_type in _COMPOUND_AGENTS:
        return ROLE_COMPOUND
    return ROLE_FLOW if value > 0 else ROLE_ENABLER


# ---------------------------------------------------------------------------
# Per-agent value, from the simulation's own outputs (PRD §6)
# ---------------------------------------------------------------------------

def _to_monthly(value: float, unit: str):
    """Normalise a calibrated figure to dollars per cycle, or None if it is not a flow."""
    if value is None:
        return None
    if unit == 'usd_year':
        return float(value) / 12.0
    if unit in ('usd', 'usd_month'):
        return float(value)
    # usd_hour is a rate, pct and ratio are not money at all.
    return None


def _calibrated_values(simulation_id: str) -> dict:
    """action_type -> value dict, from the calibration layer (SIM-PRD-CAL-001).

    The largest flow field per agent wins: an agent that reports both a deal size
    and an annualised total should show the figure that drives the engine.
    """
    out: dict = {}
    try:
        from app.services import calibration_service
        runs = calibration_service.runs_for_simulation(simulation_id)
    except Exception as exc:            # calibration is optional per deployment
        logger.warning('world: calibration lookup failed for %s: %s', simulation_id, exc)
        return out

    for run in runs:
        monthly = _to_monthly(float(run.cal_mid), run.unit)
        if monthly is None or monthly <= 0:
            continue
        current = out.get(run.agent_key)
        if current is None or monthly > current['value']:
            out[run.agent_key] = {
                'value': monthly,
                'source': 'calibration',
                'source_label': run.field_label,
                'tier': run.confidence_tier,
                'low': _to_monthly(float(run.cal_low), run.unit),
                'high': _to_monthly(float(run.cal_high), run.unit),
            }
    return out


def _root_multiplier(simulation_id: str) -> dict:
    """How far above market the user's own rate sits — measured, not assumed.

    A calibrated hourly rate carries the reference median it was compared against
    (`ref_p50`), so the rate card's effect on the engine is the real ratio between
    the two, clamped to the admin bounds. With no calibrated rate it falls back to
    the configured default.
    """
    fallback = float(setting('world_root_multiplier'))
    lo = float(setting('world_root_multiplier_min'))
    hi = float(setting('world_root_multiplier_max'))
    try:
        from app.services import calibration_service
        runs = calibration_service.runs_for_simulation(simulation_id)
    except Exception:
        return {'value': fallback, 'measured': False, 'basis': None}

    for run in runs:
        if run.agent_key not in _MULTIPLIER_AGENTS or run.unit != 'usd_hour':
            continue
        ref = float(run.ref_p50) if run.ref_p50 is not None else 0.0
        mid = float(run.cal_mid)
        if ref <= 0 or mid <= 0:
            continue
        ratio = max(lo, min(hi, mid / ref))
        return {
            'value': ratio,
            'measured': True,
            'basis': 'Your calibrated rate vs. the reference median for your cohort',
        }
    return {'value': fallback, 'measured': False, 'basis': None}


def _income_stream_values(sim, calibrated: dict, agents_by_layer: dict) -> dict:
    """Fill agents with no calibrated figure from the layer's projected income streams.

    A layer's projected midpoint is split evenly across that layer's revenue-role
    agents that are still unvalued — the simulation's own projection, attributed no
    more precisely than it actually is, which the node labels say plainly.

    Layer 5 is left out. Wealth deployment does not earn; it routes and compounds
    what the layers below produce, so pinning an income figure to an estate
    checklist would be a number the simulation never claimed.
    """
    out: dict = {}
    for layer in sim.layers:
        if layer.layer_number >= 5:
            continue
        total = 0.0
        for stream in layer.income_streams:
            low, high = stream.est_monthly_low, stream.est_monthly_high
            if low and high:
                total += (float(low) + float(high)) / 2.0
            elif low:
                total += float(low)
            elif high:
                total += float(high)
        if total <= 0:
            continue

        candidates = [
            a['action_type'] for a in agents_by_layer.get(layer.layer_number, [])
            if a['action_type'] not in calibrated
            and a['action_type'] not in _MULTIPLIER_AGENTS
            and a['action_type'] not in _ROUTER_AGENTS
            and a['action_type'] not in _COMPOUND_AGENTS
        ]
        if not candidates:
            continue
        share = total / len(candidates)
        for action_type in candidates:
            out[action_type] = {
                'value': share,
                'source': 'income_streams',
                'source_label': 'Projected for this layer, split across its agents',
                'tier': None, 'low': None, 'high': None,
            }
    return out


# ---------------------------------------------------------------------------
# The read model
# ---------------------------------------------------------------------------

_CACHE: dict = {}


def invalidate(simulation_id: str) -> None:
    """Drop the memoized world for one simulation. Called on every config write."""
    _CACHE.pop(simulation_id, None)


def build_world(sim, use_cache: bool = True) -> dict:
    """Nodes, edges, orchestrator suggestion, totals and zone for one simulation."""
    ttl = int(setting('world_cache_ttl_seconds'))
    if use_cache and ttl > 0:
        hit = _CACHE.get(sim.id)
        if hit and (time.time() - hit['at']) < ttl:
            return hit['model']

    model = _build(sim)
    if ttl > 0:
        _CACHE[sim.id] = {'at': time.time(), 'model': model}
    return model


def _build(sim) -> dict:
    from app.models.agent_action import AgentAction
    from app.models.agent_context import AgentContext
    from app.models.layer6 import Layer6Cycle, Layer6Outcome
    from app.services.agent_registry import (
        ACTION_PREREQUISITES, get_all_agents, resolve_alias,
    )
    from app.services.agent_selector import (
        LAYER_LOCK_CONDITIONS, ROOT_AGENT_ID, TRIGGERED_AGENT_IDS,
        _get_layer_unlock_status, effective_selection,
    )
    from app.extensions import db
    from sqlalchemy import func

    agents = get_all_agents()
    by_layer: dict = {}
    for agent in agents:
        by_layer.setdefault(agent['layer'], []).append(agent)

    # ── Active set — whatever Manage Agents calls active, by construction ────
    # Deliberately not "every agent when nothing is confirmed yet": that is what
    # the orchestrator's dispatch scope does, but the Manage Agents screen shows
    # the recommended default instead, and this view has to match the screen the
    # user manages the selection on.
    active_set = effective_selection(sim, agents_flat=agents)

    # ── Run status, for the "what has actually happened" layer of the node ───
    status_map: dict = {}
    for action in AgentAction.query.filter_by(simulation_id=sim.id).order_by(
        AgentAction.created_at.desc()
    ).all():
        status_map.setdefault(resolve_alias(action.action_type), action.status)
    completed = {at for at, st in status_map.items() if st == AgentAction.STATUS_COMPLETE}

    layer_unlocked = _get_layer_unlock_status(sim, completed)

    # ── Stored parameters, per layer (the Layer Actions store) ───────────────
    stored_by_layer = {n: AgentContext.get_for_layer(sim.id, n) for n in range(1, 6)}

    # ── Values ───────────────────────────────────────────────────────────────
    calibrated = _calibrated_values(sim.id)
    projected = _income_stream_values(sim, calibrated, by_layer)
    multiplier = _root_multiplier(sim.id)

    zone = resolve_zone(sim.expertise_zone)
    zone_labels = zone.get('labels', {})

    nodes = []
    for agent in agents:
        action_type = agent['action_type']
        layer = agent['layer']
        stored = stored_by_layer.get(layer, {})
        form = agent.get('prompt_form', [])

        params = []
        for field in form:
            params.append({
                'key': field['key'],
                'label': field.get('label', field['key']),
                'type': field.get('type', 'text'),
                'options': field.get('options', []),
                'required': bool(field.get('required')),
                'value': stored.get(field['key']) or '',
            })
        # "Configured" means every required field has a value; where an agent
        # declares none, any answered field counts. An agent with no form at all
        # has nothing to configure and so never shows as unconfigured.
        required_keys = [f['key'] for f in form if f.get('required')]
        if required_keys:
            configured = all(stored.get(k) for k in required_keys)
        elif form:
            configured = any(stored.get(f['key']) for f in form)
        else:
            configured = True

        val = calibrated.get(action_type) or projected.get(action_type)
        value = float(val['value']) if val else 0.0
        role = _role_for(action_type, value)
        # A multiplier / router / compound agent is not an income stream; whatever
        # figure it reported describes something else, so it does not join the sum.
        if role != ROLE_FLOW:
            value = 0.0

        prereqs = ACTION_PREREQUISITES.get(action_type, [])
        prereqs_met = all(p in completed for p in prereqs)
        prereqs_active = all(p in active_set for p in prereqs)

        nodes.append({
            'action_type': action_type,
            'label': zone_labels.get(action_type, agent['label']),
            'base_label': agent['label'],
            'description': agent.get('description', ''),
            'icon': _ZONE_ICONS.get(action_type, _LAYER_ICONS.get(str(layer), '◆')),
            'layer': layer,
            'active': action_type in active_set,
            'configured': bool(configured),
            'status': status_map.get(action_type, 'eligible' if prereqs_met else 'locked'),
            'role': role,
            'value': round(value, 2),
            'value_source': val['source'] if val else None,
            'value_source_label': val['source_label'] if val else None,
            'value_tier': val['tier'] if val else None,
            'value_low': round(val['low'], 2) if val and val.get('low') else None,
            'value_high': round(val['high'], 2) if val and val.get('high') else None,
            'multiplier': round(multiplier['value'], 3) if role == ROLE_MULTIPLIER else None,
            'multiplier_measured': multiplier['measured'] if role == ROLE_MULTIPLIER else None,
            'multiplier_basis': multiplier['basis'] if role == ROLE_MULTIPLIER else None,
            'prerequisites': prereqs,
            'prerequisites_met': prereqs_met,
            'prerequisites_active': prereqs_active,
            'layer_locked': not layer_unlocked.get(layer, True),
            'lock_condition': LAYER_LOCK_CONDITIONS.get(layer),
            'is_root': action_type == ROOT_AGENT_ID,
            'is_triggered': action_type in TRIGGERED_AGENT_IDS,
            'is_hub': bool(agent.get('hub_node')),
            'integrations': agent.get('integrations', []),
            'params': params,
            'param_count': len(params),
        })

    node_index = {n['action_type']: n for n in nodes}

    edges = []
    for action_type, prereqs in ACTION_PREREQUISITES.items():
        if action_type not in node_index:
            continue
        for prereq in prereqs:
            if prereq not in node_index:
                continue
            edges.append({
                'source': prereq,
                'target': action_type,
                'live': node_index[prereq]['active'] and node_index[action_type]['active'],
            })

    # ── Totals ───────────────────────────────────────────────────────────────
    monthly_income = sum(n['value'] for n in nodes if n['active'] and n['role'] == ROLE_FLOW)
    root = node_index.get(ROOT_AGENT_ID)
    if root and root['active']:
        monthly_income *= multiplier['value']

    confirmed = float(
        db.session.query(func.sum(Layer6Outcome.actual_income))
        .filter_by(simulation_id=sim.id).scalar() or 0
    )
    cycle_count = (
        db.session.query(func.max(Layer6Cycle.cycle_number))
        .filter_by(simulation_id=sim.id).scalar() or 0
    )

    visible = [n for n in nodes if n['active']]
    configured_count = sum(1 for n in nodes if n['configured'])
    return {
        'simulation_id': sim.id,
        'simulation_name': sim.name,
        'nodes': nodes,
        'edges': edges,
        'layers': [
            {'layer': n, 'name': LAYER_NAMES[n], 'color': LAYER_COLORS[n],
             'unlocked': layer_unlocked.get(n, True),
             'lock_condition': LAYER_LOCK_CONDITIONS.get(n)}
            for n in range(1, 6)
        ],
        'zone': {'key': zone['key'], 'name': zone['name'], 'accent': zone['accent'],
                 'expertise_zone': sim.expertise_zone or ''},
        'zone_options': zone_options(),
        'suggestion': suggest_next(nodes, edges),
        'totals': {
            'monthly_income': round(monthly_income, 2),
            'confirmed_income': round(confirmed, 2),
            'cycle_count': int(cycle_count),
            'active_count': len(visible),
            'configured_count': configured_count,
            'total_count': len(nodes),
        },
        'motion': {
            'allocation_pct': int(setting('world_allocation_pct')),
            'compound_pct': float(setting('world_compound_pct')),
            'auto_interval_ms': int(setting('world_auto_interval_ms')),
            'token_duration_ms': int(setting('world_token_duration_ms')),
            'tokens_max': int(setting('world_tokens_max')),
            'token_value_step': int(setting('world_token_value_step')),
        },
        'view': {
            'default_view': setting('world_default_view'),
            'default_scope': setting('world_default_scope'),
            'layer_focus_threshold': int(setting('world_layer_focus_threshold')),
        },
    }


# ---------------------------------------------------------------------------
# Orchestrator — the Layer 6 lens, as a scheduler (PRD §5.4, §9)
# ---------------------------------------------------------------------------

def suggest_next(nodes: list, edges: list) -> dict | None:
    """The highest-leverage inactive agent, and why.

    Prerequisites are weighted above raw value, so the world never suggests a
    Layer 3 product before the Layer 1 foundation it depends on. A locked layer
    is never suggested at all — the user cannot act on it yet.
    """
    index = {n['action_type']: n for n in nodes}
    active = {n['action_type'] for n in nodes if n['active']}

    enabler_v = float(setting('world_enabler_value'))
    router_v = float(setting('world_router_value'))
    compound_v = float(setting('world_compound_value'))
    w_connected = float(setting('world_suggest_connected_weight'))
    w_prereq = float(setting('world_suggest_prereq_weight'))

    # Inactive agents that something already-active depends on: the foundations.
    blocking = set()
    for node in nodes:
        if not node['active']:
            continue
        for prereq in node['prerequisites']:
            if prereq not in active and prereq in index:
                blocking.add(prereq)

    best, best_score, best_reason = None, -1.0, ''
    for node in nodes:
        action_type = node['action_type']
        if node['active'] or node['layer_locked']:
            continue

        if node['role'] == 'router':
            basis = router_v
        elif node['role'] == 'compound':
            basis = compound_v
        elif node['value'] > 0:
            basis = node['value']
        else:
            basis = enabler_v

        if action_type in blocking:
            score = basis * w_prereq
            reason = 'an agent you have already switched on depends on it'
        else:
            connected = any(
                (e['source'] == action_type and e['target'] in active)
                or (e['target'] == action_type and e['source'] in active)
                for e in edges
            )
            # Do not suggest opening a layer whose own prerequisites are missing.
            if not node['prerequisites_active'] and node['prerequisites']:
                continue
            score = basis * (w_connected if connected else 1.0)
            reason = ('it plugs straight into what is already running'
                      if connected else 'it opens the most value you are not yet capturing')

        if score > best_score:
            best, best_score, best_reason = node, score, reason

    if best is None:
        return None
    return {
        'action_type': best['action_type'],
        'label': best['label'],
        'layer': best['layer'],
        'reason': best_reason,
        'score': round(best_score, 2),
    }
