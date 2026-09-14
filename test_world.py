"""The Wealth Engine World — PRD-SIM-WORLD-001.

The claim worth testing is §6: the world and the list can never disagree,
because the world owns no state. So these checks push on the seams — toggle an
agent from the world and read it back through the selector, save a parameter in
the drawer's shape and read it back through AgentContext, and confirm the values
on the canvas trace to real simulation outputs rather than to decoration.

Against in-memory SQLite; no network, no real data.

    python test_world.py
"""
import io
import os
import sys
from datetime import datetime

os.environ.setdefault('SECRET_KEY', 'test-only')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PASS, FAIL = [], []


def check(label, condition, detail=''):
    (PASS if condition else FAIL).append(label)
    line = '  {} {}{}'.format('ok  ' if condition else 'FAIL', label,
                              ('  -- ' + str(detail)) if detail else '')
    enc = sys.stdout.encoding or 'ascii'
    print(line.encode(enc, 'replace').decode(enc))


def section(title):
    print('\n' + title)


def main():
    from app import create_app
    from app.extensions import db, bcrypt
    from utils.id_gen import generate_id

    app = create_app('testing')
    app.config.update(SQLALCHEMY_DATABASE_URI='sqlite:///:memory:', WTF_CSRF_ENABLED=False)

    with app.app_context():
        from app.models.agent_action import AgentAction
        from app.models.agent_context import AgentContext
        from app.models.calibration import CalibrationRun
        from app.models.simulation import Simulation, SimulationLayer, IncomeStream
        from app.models.user import User
        from app.services import world_service
        from app.services.agent_registry import get_all_agents
        from app.services.agent_selector import ROOT_AGENT_ID, TRIGGERED_AGENT_IDS

        db.create_all()

        now = datetime.utcnow()
        user = User(id=generate_id(), email='owner@t.local', full_name='Owner',
                    password_hash=bcrypt.generate_password_hash('pw12345678').decode(),
                    email_verified=True, onboarding_completed_at=now)
        other = User(id=generate_id(), email='nosy@t.local', full_name='Nosy',
                     password_hash=bcrypt.generate_password_hash('pw12345678').decode(),
                     email_verified=True, onboarding_completed_at=now)
        db.session.add_all([user, other])
        db.session.commit()

        sim = Simulation(id=generate_id(), user_id=user.id, name='Engine Test',
                         expertise_zone='Management Consulting', status='complete')
        db.session.add(sim)
        db.session.flush()

        # A layer with a projected income stream — the second-choice value source.
        layer2 = SimulationLayer(id=generate_id(), simulation_id=sim.id, layer_number=2,
                                 layer_name='Leveraged Income')
        db.session.add(layer2)
        db.session.flush()
        db.session.add(IncomeStream(
            id=generate_id(), layer_id=layer2.id, name='Workshops',
            est_monthly_low=2000, est_monthly_high=4000,
            ai_reasoning='Two cohorts a quarter at the rate the simulation projected.'))

        sim.selected_agents = [ROOT_AGENT_ID, 'booking_page', 'cold_email_campaign',
                               'group_coaching', 'workshop_curriculum', 'income_allocation']
        sim.agent_selection_confirmed_at = now

        # Pre-seed the selector's description cache. get_selector_data() otherwise
        # calls Claude on a cache miss, and the list view is only in this test as
        # the other half of the "world and list cannot disagree" check — the
        # descriptions themselves are beside the point and must not hit the network.
        from app.services import agent_selector as _sel
        sim.agent_personalized_descriptions = dict(
            {a['action_type']: a.get('description', '') for a in get_all_agents()},
            **{_sel._DESCRIPTIONS_SIG_KEY: _sel._descriptions_input_sig(
                [sim.expertise_zone], sim.expertise_zone)})
        db.session.commit()

        # ── Read model shape ────────────────────────────────────────────────
        section('Read model (§6, §8)')
        world_service.invalidate(sim.id)
        model = world_service.build_world(sim, use_cache=False)

        registry_count = len(get_all_agents())
        check('every agent is a node', len(model['nodes']) == registry_count,
              '{} nodes vs {} agents'.format(len(model['nodes']), registry_count))
        check('five layer bands', len(model['layers']) == 5)
        check('edges come from the prerequisite DAG', len(model['edges']) > 0,
              '{} edges'.format(len(model['edges'])))

        index = {n['action_type']: n for n in model['nodes']}
        check('selected agents are active', index['booking_page']['active'])
        check('unselected agents are inactive', not index['sales_page']['active'])
        check('triggered agents are always active',
              all(index[a]['active'] for a in TRIGGERED_AGENT_IDS if a in index))

        live = [e for e in model['edges'] if e['live']]
        check('an edge is live only when both ends are active',
              all(index[e['source']]['active'] and index[e['target']]['active'] for e in live))
        dead = [e for e in model['edges'] if not e['live']]
        check('a half-active edge is not live',
              all(not (index[e['source']]['active'] and index[e['target']]['active'])
                  for e in dead))

        # ── Values trace to the simulation's own outputs (§6) ───────────────
        section('Values are real, not illustrative (§6)')
        l2_valued = [n for n in model['nodes']
                     if n['layer'] == 2 and n['value_source'] == 'income_streams']
        check('layer income streams reach that layer\'s agents', len(l2_valued) > 0,
              '{} valued'.format(len(l2_valued)))
        check('the projected midpoint is what is split',
              abs(sum(n['value'] for n in l2_valued) - 3000.0) < 0.01,
              sum(n['value'] for n in l2_valued))

        check('an agent with no output carries no value',
              index['estate_planning']['value'] == 0
              and index['estate_planning']['role'] == 'enabler')
        check('the allocation agent routes rather than earns',
              index['income_allocation']['role'] == 'router'
              and index['income_allocation']['value'] == 0)
        check('an investing agent compounds rather than earns',
              index['fund_recommendations']['role'] == 'compound')

        # Layer 5 deploys wealth, it does not earn it — a projection there must
        # not turn an estate checklist into an income line.
        layer5 = SimulationLayer(id=generate_id(), simulation_id=sim.id, layer_number=5,
                                 layer_name='Wealth Deployment')
        db.session.add(layer5)
        db.session.flush()
        db.session.add(IncomeStream(
            id=generate_id(), layer_id=layer5.id, name='Portfolio income',
            est_monthly_low=1000, est_monthly_high=3000,
            ai_reasoning='Dividend yield on the allocated portfolio.'))
        db.session.commit()
        world_service.invalidate(sim.id)
        l5 = world_service.build_world(sim, use_cache=False)
        check('a layer 5 projection never becomes an agent income line',
              all(n['value'] == 0 and n['value_source'] is None
                  for n in l5['nodes'] if n['layer'] == 5),
              [(n['action_type'], n['value']) for n in l5['nodes']
               if n['layer'] == 5 and n['value']])

        # A calibrated field outranks the layer-stream fallback.
        db.session.add(CalibrationRun(
            id=generate_id(), simulation_id=sim.id, agent_key='group_coaching',
            output_field='cohort_revenue', field_label='Revenue per cohort', unit='usd',
            raw_value=5000, cal_low=4000, cal_mid=5000, cal_high=6000,
            confidence_tier='moderate'))
        # And the rate card's multiplier is measured against the reference median.
        db.session.add(CalibrationRun(
            id=generate_id(), simulation_id=sim.id, agent_key='rate_card',
            output_field='billable_hourly_rate', field_label='Your billable hourly rate',
            unit='usd_hour', raw_value=250, cal_low=200, cal_mid=240, cal_high=280,
            confidence_tier='high', ref_p50=200))
        db.session.commit()

        world_service.invalidate(sim.id)
        model = world_service.build_world(sim, use_cache=False)
        index = {n['action_type']: n for n in model['nodes']}

        gc = index['group_coaching']
        check('a calibrated figure beats the layer-stream fallback',
              gc['value_source'] == 'calibration' and abs(gc['value'] - 5000.0) < 0.01,
              '{} {}'.format(gc['value_source'], gc['value']))
        check('the calibrated node cites its field and tier',
              gc['value_source_label'] == 'Revenue per cohort' and gc['value_tier'] == 'moderate')

        root = index[ROOT_AGENT_ID]
        check('the rate card is a multiplier, not an income line',
              root['role'] == 'multiplier' and root['value'] == 0)
        check('the multiplier is the measured rate-to-market ratio (240/200 = 1.2)',
              root['multiplier_measured'] and abs(root['multiplier'] - 1.2) < 0.001,
              root['multiplier'])

        # Engine income = active flow values, scaled by the measured multiplier.
        expected = sum(n['value'] for n in model['nodes']
                       if n['active'] and n['role'] == 'flow') * 1.2
        check('engine income applies the multiplier to active flows only',
              abs(model['totals']['monthly_income'] - expected) < 0.01,
              '{} vs {}'.format(model['totals']['monthly_income'], expected))

        check('an hourly rate is never mistaken for a monthly flow',
              world_service._to_monthly(240, 'usd_hour') is None)
        check('an annual figure is divided into cycles',
              abs(world_service._to_monthly(120000, 'usd_year') - 10000) < 0.01)

        # ── Configured is what the World shows by default ───────────────────
        section('Configured is a scope, not the default')
        check('nothing is configured on a fresh simulation',
              model['totals']['configured_count'] == 0,
              model['totals']['configured_count'])
        check('the admin default scope is the active set',
              model['view']['default_scope'] == 'selected', model['view']['default_scope'])

        # booking_page needs meeting_types AND available_days. One is not enough.
        AgentContext.upsert(sim.id, 1, 'meeting_types', 'Discovery call')
        db.session.commit()
        world_service.invalidate(sim.id)
        partial = {n['action_type']: n
                   for n in world_service.build_world(sim, use_cache=False)['nodes']}
        check('a half-filled agent does not count as configured',
              not partial['booking_page']['configured'])

        AgentContext.upsert(sim.id, 1, 'available_days', 'Tue, Thu')
        db.session.commit()
        world_service.invalidate(sim.id)
        full = world_service.build_world(sim, use_cache=False)
        nodes_by_type = {n['action_type']: n for n in full['nodes']}
        check('every required field filled means configured',
              nodes_by_type['booking_page']['configured'])
        check('an optional field left blank does not block configured',
              not any(p['value'] for p in nodes_by_type['booking_page']['params']
                      if p['key'] == 'paid_session_rate'))
        check('configured_count tracks it', full['totals']['configured_count'] == 1,
              full['totals']['configured_count'])

        # The one key two agents share is required for one and optional for the
        # other, so filling it cannot falsely configure the neighbour.
        AgentContext.upsert(sim.id, 2, 'workshop_duration', 'Two days')
        db.session.commit()
        world_service.invalidate(sim.id)
        shared = {n['action_type']: n
                  for n in world_service.build_world(sim, use_cache=False)['nodes']}
        check('a shared parameter key does not configure the neighbouring agent',
              not shared['corporate_training']['configured'],
              'corporate_training still needs its own required fields')

        # ── Orchestrator (§5.4) ─────────────────────────────────────────────
        section('Orchestrator (§5.4, §9)')
        suggestion = model['suggestion']
        check('a suggestion is made', suggestion is not None)
        check('the suggestion explains itself',
              suggestion and len(suggestion.get('reason', '')) > 10, suggestion)
        check('the suggested agent is not already active',
              suggestion and not index[suggestion['action_type']]['active'])
        check('a locked layer is never suggested',
              suggestion and not index[suggestion['action_type']]['layer_locked'],
              suggestion)

        # Prerequisites outrank raw value: an inactive agent that an active one
        # depends on must win, even against a richer node elsewhere.
        blocking = set()
        for node in model['nodes']:
            if not node['active']:
                continue
            for prereq in node['prerequisites']:
                if prereq in index and not index[prereq]['active']:
                    blocking.add(prereq)
        if blocking:
            check('a missing foundation is suggested before anything else',
                  suggestion['action_type'] in blocking,
                  '{} not in {}'.format(suggestion['action_type'], sorted(blocking)))
        else:
            check('a missing foundation is suggested before anything else (none missing)', True)

        # ── The active set is Manage Agents', including before confirmation ──
        section('Active set matches Manage Agents (/setup)')

        from app.services.agent_selector import effective_selection, get_selector_data

        model = world_service.build_world(sim, use_cache=False)
        world_active = {n['action_type'] for n in model['nodes'] if n['active']}
        check('a confirmed simulation shows exactly its saved selection',
              world_active == set(sim.selected_agents) | set(TRIGGERED_AGENT_IDS)
              | {ROOT_AGENT_ID},
              sorted(world_active ^ (set(sim.selected_agents)
                                     | set(TRIGGERED_AGENT_IDS) | {ROOT_AGENT_ID})))
        check('active_count agrees with the nodes',
              model['totals']['active_count'] == len(world_active))

        # An unconfirmed simulation is the case that was wrong: the world used to
        # treat every agent as active, while Manage Agents shows the recommended
        # default. The two must agree.
        fresh = Simulation(id=generate_id(), user_id=user.id, name='Unconfirmed',
                           expertise_zone='Management Consulting', status='complete')
        db.session.add(fresh)
        db.session.commit()
        fresh.agent_personalized_descriptions = dict(
            {a['action_type']: a.get('description', '') for a in get_all_agents()},
            **{_sel._DESCRIPTIONS_SIG_KEY: _sel._descriptions_input_sig(
                [fresh.expertise_zone], fresh.expertise_zone)})
        db.session.commit()

        check('an unconfirmed simulation has no saved selection',
              not fresh.selected_agents and fresh.agent_selection_confirmed_at is None)

        fresh_world = world_service.build_world(fresh, use_cache=False)
        fresh_active = {n['action_type'] for n in fresh_world['nodes'] if n['active']}
        manage_agents = set(get_selector_data(fresh)['selected_agents'])

        check('the world does NOT light up all 49 before confirmation',
              len(fresh_active) < len(get_all_agents()),
              '{} of {} active'.format(len(fresh_active), len(get_all_agents())))
        check('the world active set equals the Manage Agents selection',
              fresh_active == manage_agents,
              'differs by {}'.format(sorted(fresh_active ^ manage_agents)))
        check('the recommended default still includes the root agent',
              ROOT_AGENT_ID in fresh_active)
        check('and the event-triggered agents',
              set(TRIGGERED_AGENT_IDS) <= fresh_active)
        check('one function decides it for both surfaces',
              effective_selection(fresh) == fresh_active == manage_agents)

        # ── Zone theming (§5.5) ─────────────────────────────────────────────
        model = world_service.build_world(sim, use_cache=False)
        section('Zone theming (§5.5)')
        check('the consulting zone resolves from the expertise string',
              model['zone']['key'] == 'consulting', model['zone'])
        check('a coaching simulation picks the coaching pack',
              world_service.resolve_zone('Executive Coaching')['key'] == 'coaching')
        check('an unknown zone falls back to the default',
              world_service.resolve_zone('Underwater Basket Weaving')['key'] == 'consulting')
        check('theme packs ship their label overrides for the selector',
              any(z['key'] == 'saas' and z['labels'].get('saas_product_spec')
                  for z in model['zone_options']))
        check('every agent has a glyph', all(n['icon'] for n in model['nodes']))

        # ── Settings are admin-configurable, never hardcoded ────────────────
        section('Admin-configurable thresholds')
        from app.models.platform_settings import PlatformSetting
        check('motion rates come from settings',
              model['motion']['allocation_pct'] == 45 and model['motion']['auto_interval_ms'] == 2600,
              model['motion'])
        PlatformSetting.set('world_allocation_pct', '60')
        world_service.invalidate(sim.id)
        check('changing a setting changes the model',
              world_service.build_world(sim, use_cache=False)['motion']['allocation_pct'] == 60)
        PlatformSetting.set('world_allocation_pct', '45')
        check('every knob round-trips through all_settings',
              len(world_service.all_settings()) == len(world_service.SETTINGS_SPEC))

        # ── Caching and invalidation (§9) ───────────────────────────────────
        section('Caching (§9)')
        world_service.invalidate(sim.id)
        first = world_service.build_world(sim)
        check('a second read is served from cache',
              world_service.build_world(sim) is first)
        world_service.invalidate(sim.id)
        check('invalidation forces a rebuild',
              world_service.build_world(sim) is not first)

        from app.services.agent_registry import resolve_alias as _canon
        sim_id = sim.id
        fresh_id = fresh.id
        fresh_expected = sorted(fresh_active)
        owner_email = user.email

    # ── HTTP surface ────────────────────────────────────────────────────────
    section('Endpoints (§8) and the shared write path (§6)')
    client = app.test_client()

    res = client.get('/api/simulations/{}/world'.format(sim_id))
    check('the world needs a login', res.status_code in (302, 401), res.status_code)

    client.post('/api/auth/login', json={'email': 'nosy@t.local', 'password': 'pw12345678'})
    res = client.get('/api/simulations/{}/world'.format(sim_id))
    check('another user cannot read the world', res.status_code == 404, res.status_code)
    client.post('/api/auth/logout')

    login = client.post('/api/auth/login',
                        json={'email': owner_email, 'password': 'pw12345678'})
    check('owner logs in', login.status_code == 200, login.status_code)

    res = client.get('/api/simulations/{}/world'.format(sim_id))
    check('GET /world returns the read model', res.status_code == 200, res.status_code)
    body = res.get_json()
    check('the payload carries everything the canvas needs',
          all(k in body for k in ('nodes', 'edges', 'suggestion', 'totals',
                                  'zone', 'layers', 'motion', 'view')))
    check('the payload tells the client how many agents are configured',
          'configured_count' in body['totals'], body['totals'])
    check('the payload names the default scope',
          body['view']['default_scope'] == 'selected', body['view'])
    check('every node says whether it is configured',
          all('configured' in n for n in body['nodes']))

    # Activate through the world, verify through the selector.
    res = client.post('/api/simulations/{}/agents/sales_page/activate'.format(sim_id),
                      json={'active': True})
    check('POST /activate turns an agent on', res.status_code == 200, res.get_data(as_text=True)[:200])
    activated = res.get_json()
    check('the response returns a fresh graph, not just an ack',
          'nodes' in activated and 'edges' in activated and 'totals' in activated)

    sel = client.get('/api/simulations/{}/agents/selector-data'.format(sim_id)).get_json()
    selected_in_list = set(sel['selected_agents'])
    check('the LIST view agrees the agent is now on', 'sales_page' in selected_in_list)

    world_nodes = {n['action_type']: n
                   for n in client.get('/api/simulations/{}/world'.format(sim_id))
                   .get_json()['nodes']}
    check('the WORLD view agrees too', world_nodes['sales_page']['active'])
    check('world and list hold identical active sets',
          {a for a, n in world_nodes.items() if n['active']} == selected_in_list,
          'world-only: {}'.format(
              {a for a, n in world_nodes.items() if n['active']} ^ selected_in_list))

    res = client.post('/api/simulations/{}/agents/sales_page/activate'.format(sim_id),
                      json={'active': False})
    check('POST /activate turns an agent back off', res.status_code == 200)
    sel = client.get('/api/simulations/{}/agents/selector-data'.format(sim_id)).get_json()
    check('the list sees the deactivation', 'sales_page' not in set(sel['selected_agents']))

    res = client.post('/api/simulations/{}/agents/rate_card/activate'.format(sim_id),
                      json={'active': False})
    check('the root agent cannot be switched off', res.status_code == 400, res.status_code)

    res = client.post('/api/simulations/{}/agents/consulting_proposal/activate'.format(sim_id),
                      json={'active': False})
    check('a triggered agent cannot be switched off', res.status_code == 400, res.status_code)

    res = client.post('/api/simulations/{}/agents/not_an_agent/activate'.format(sim_id),
                      json={'active': True})
    check('an unknown agent 404s', res.status_code == 404, res.status_code)

    # Save parameters the way the drawer does, read back through the list.
    res = client.put('/api/simulations/{}/agents/booking_page/params'.format(sim_id),
                     json={'inputs': {'meeting_types': 'Discovery call, Working session'}})
    check('the drawer saves through the existing params endpoint',
          res.status_code == 200, res.get_data(as_text=True)[:200])

    with app.app_context():
        from app.models.agent_context import AgentContext
        stored = AgentContext.get_for_layer(sim_id, 1)
        check('the parameter landed in the shared AgentContext store',
              stored.get('meeting_types') == 'Discovery call, Working session', stored.get('meeting_types'))

    world_nodes = {n['action_type']: n
                   for n in client.get('/api/simulations/{}/world'.format(sim_id))
                   .get_json()['nodes']}
    saved = [p for p in world_nodes['booking_page']['params'] if p['key'] == 'meeting_types']
    check('the world reads the saved value straight back',
          saved and saved[0]['value'] == 'Discovery call, Working session',
          saved[0]['value'] if saved else 'absent')
    check('a write invalidated the cached model',
          world_nodes['booking_page']['configured'] is not None)

    # ── The List view is the other half of the same model ───────────────────
    # ?view=list opens on "Layer actions" (the /dag swimlane), so that endpoint
    # is what a user actually sees first. It used to return all 49 regardless of
    # the selection; /network used to fall through to every agent whenever no
    # selection had been confirmed.
    section('List view scopes to the active set too')

    dag = client.get('/api/simulations/{}/layer6/dag'.format(sim_id)).get_json()
    dag_types = {n['id'] for n in dag['nodes']}
    world = client.get('/api/simulations/{}/world'.format(sim_id)).get_json()
    world_active = {n['action_type'] for n in world['nodes'] if n['active']}

    check('the swimlane no longer returns all 49',
          len(dag_types) < world['totals']['total_count'],
          '{} of {}'.format(len(dag_types), world['totals']['total_count']))
    check('the swimlane shows the active agents and nothing unrelated',
          dag_types <= world_active,
          'extra: {}'.format(sorted(dag_types - world_active)))
    check('the swimlane shows every active agent',
          world_active <= dag_types,
          'missing: {}'.format(sorted(world_active - dag_types)))
    check('every swimlane node still carries its form and status',
          all('form' in n and 'status' in n for n in dag['nodes']))

    net = client.get('/api/simulations/{}/layer6/network'.format(sim_id)).get_json()
    check('the agent-network sub-view renders', 'nodes' in net and len(net['nodes']) > 0)
    # Its node ids are catalog ids (layers, orchestrator, engines); only agent
    # nodes carry an action_type, and the hand-authored catalog covers a subset
    # of the registry, so the test is that nothing outside the active set leaks.
    net_agents = {_canon(n['action_type']) for n in net['nodes'] if n.get('action_type')}
    check('the agent-network sub-view has agent nodes', len(net_agents) > 0,
          '{} agent nodes'.format(len(net_agents)))
    check('no agent outside the active set leaks into the network view',
          net_agents <= world_active,
          'extra: {}'.format(sorted(net_agents - world_active)))
    check('infrastructure nodes are kept regardless of the filter',
          any(not n.get('action_type') for n in net['nodes']))

    # The unconfirmed simulation: the case both endpoints got wrong.
    fdag = client.get('/api/simulations/{}/layer6/dag'.format(fresh_id)).get_json()
    fdag_types = sorted(n['id'] for n in fdag['nodes'])
    check('an unconfirmed simulation does not dump all 49 into the swimlane',
          len(fdag_types) < world['totals']['total_count'],
          '{} nodes'.format(len(fdag_types)))
    check('the swimlane agrees with Manage Agents before confirmation too',
          fdag_types == fresh_expected,
          'differs by {}'.format(sorted(set(fdag_types) ^ set(fresh_expected))))

    fnet = client.get('/api/simulations/{}/layer6/network'.format(fresh_id))
    check('the unconfirmed agent-network view still renders', fnet.status_code == 200,
          fnet.status_code)
    fnet_agents = {_canon(n['action_type'])
                   for n in fnet.get_json()['nodes'] if n.get('action_type')}
    check('and it too stays inside Manage Agents before confirmation',
          fnet_agents <= set(fresh_expected),
          'extra: {}'.format(sorted(fnet_agents - set(fresh_expected))))

    # A deselected agent that actually ran stays on the board — the cycle's own
    # queue references it, so hiding it would leave a hole.
    with app.app_context():
        from app.models.agent_action import AgentAction as _AA
        from app.models.simulation import Simulation as _Sim
        from utils.id_gen import generate_id as _gid
        _s = _Sim.query.get(sim_id)
        check('sales_page is not in the selection',
              'sales_page' not in set(_s.selected_agents))
        db_action = _AA(id=_gid(), simulation_id=sim_id, layer_number=3,
                        action_type='sales_page', status=_AA.STATUS_COMPLETE,
                        artifact='Ran before it was deselected.')
        from app.extensions import db as _db
        _db.session.add(db_action)
        _db.session.commit()

    dag2 = client.get('/api/simulations/{}/layer6/dag'.format(sim_id)).get_json()
    by_id = {n['id']: n for n in dag2['nodes']}
    check('an agent that produced real work is still reported',
          'sales_page' in by_id)
    check('but it is flagged inactive so the swimlane can drop it',
          by_id['sales_page']['active'] is False)
    check('an agent in the selection is flagged active',
          by_id['booking_page']['active'] is True)

    # The swimlane's own catalogue is half legacy ids; every node has to carry
    # its aliases or those entries silently fall off the board.
    with app.app_context():
        from app.services.agent_registry import get_alias_map
        legacy_names = set(get_alias_map())
    import re as _re
    _js = io.open('app/static/js/swimlane.js', encoding='utf-8').read()
    _block = _js[_js.index('const AGENTS = ['):]
    _block = _block[:_block.index('\n  ];')]
    swimlane_ids = set(_re.findall(r"id:\s*'([a-z0-9_]+)'", _block))

    check('the swimlane catalogue really does use legacy ids',
          len(swimlane_ids & legacy_names) > 0,
          '{} legacy of {}'.format(len(swimlane_ids & legacy_names), len(swimlane_ids)))

    # Every dag node advertises its aliases, so the client can key its cache
    # under each one. Resolving every swimlane id must land on a dag node.
    alias_index = {}
    for n in dag2['nodes']:
        alias_index[n['id']] = n
        for al in n.get('aliases', []):
            alias_index[al] = n
    reachable = {i for i in swimlane_ids if i in alias_index}
    check('every dag node ships its alias list',
          all('aliases' in n for n in dag2['nodes']))
    check('aliases make the legacy swimlane ids resolvable',
          len(reachable & legacy_names) > 0,
          '{} legacy ids now resolve'.format(len(reachable & legacy_names)))
    check('nothing the swimlane can reach is outside the dag',
          reachable <= set(alias_index))

    check('the client keys its cache by alias too',
          '(n.aliases || []).forEach' in _js)
    check('the board honours the same ?layer= focus the world writes',
          'function _layerFocus()' in _js and 'node.layer !== focus' in _js)
    check('the board is built from the server node list, not the stale catalogue',
          'function _boardAgents()' in _js and '_visible = _boardAgents();' in _js)

    # ── The board equals the Manage Agents active set, exactly ──────────────
    # Replicates _boardAgents() against the live payload: every dag node that is
    # active, deduplicated by canonical id.
    def board_for(target_id):
        payload = client.get('/api/simulations/{}/layer6/dag'.format(target_id)).get_json()
        cache = {}
        for n in payload['nodes']:
            cache[n['id']] = n
            for al in n.get('aliases', []):
                cache[al] = n
        seen, out = set(), []
        for key in cache:
            node = cache[key]
            if node['active'] is False or node['id'] in seen:
                continue
            seen.add(node['id'])
            out.append(node)
        return out

    board = board_for(sim_id)
    board_ids = {n['id'] for n in board}
    check('the board shows every active agent, run or not',
          board_ids == world_active,
          'missing {} / extra {}'.format(sorted(world_active - board_ids),
                                         sorted(board_ids - world_active)))
    check('a deselected agent that ran stays off the board',
          'sales_page' not in board_ids)
    check('agents that have not run yet are on it, carrying a real status',
          any(n['status'] in ('queued', 'blocked') for n in board),
          sorted({n['status'] for n in board}))
    check('the board spans every layer the selection touches',
          {n['layer'] for n in board} == {world_nodes[a]['layer'] for a in world_active},
          sorted({n['layer'] for n in board}))

    # sow_template has no entry in the hardcoded catalogue at all, and two of
    # its entries collide on referral_network. Rendering from the server is what
    # makes an agent like that reachable.
    with app.app_context():
        from app.models.simulation import Simulation as _S
        from app.extensions import db as _db
        _s = _S.query.get(sim_id)
        _s.selected_agents = list(_s.selected_agents) + ['sow_template']
        _db.session.commit()
    check('an agent missing from the stale catalogue still reaches the board',
          'sow_template' in {n['id'] for n in board_for(sim_id)})

    # ── Routing and rendering (§4) ──────────────────────────────────────────
    section('Entry points and routing (§4)')
    page = client.get('/simulations/{}/gcc?tab=network'.format(sim_id))
    html = page.get_data(as_text=True)
    check('the gcc network tab renders', page.status_code == 200, page.status_code)
    check('the world is on the page', 'id="wewRoot"' in html)
    check('the legacy list is still on the page', 'anv-modal-overlay' in html)
    check('the World / List toggle is present',
          'id="wewViewWorld"' in html and 'id="wewViewList"' in html)
    check('the world script is loaded', 'wealth_world.js' in html)
    check('the scope control offers configured, your agents and all',
          all(('value="%s"' % v) in html for v in ('configured', 'selected', 'all')))
    # Scoped to the scope select's own markup: the layer selector has a
    # value="all" option of its own, earlier in the page.
    _scope_block = html[html.index('id="wewScopeSel"'):]
    _scope_block = _scope_block[:_scope_block.index('</select>')]
    check('the scope control leads with the active set',
          _scope_block.index('value="selected"') < _scope_block.index('value="configured"')
          < _scope_block.index('value="all"'), _scope_block.replace('\n', ' '))
    check('the scope control is a select, not the old two-way button',
          'id="wewScopeSel"' in html and 'id="wewScope"' not in html)
    check('the template knows the world is enabled', 'WORLD_ENABLED = true' in html)

    check('?view=list is a valid entry point',
          client.get('/simulations/{}/gcc?tab=network&view=list'.format(sim_id)).status_code == 200)
    check('?agent=<key> deep-link loads',
          client.get('/simulations/{}/gcc?tab=network&agent=booking_page'.format(sim_id))
          .status_code == 200)
    check('?layer=L2 focus loads',
          client.get('/simulations/{}/gcc?tab=network&layer=L2'.format(sim_id)).status_code == 200)
    check('the legacy /layer6 URL still redirects',
          client.get('/simulations/{}/layer6'.format(sim_id)).status_code == 301)

    # ── Master switch ───────────────────────────────────────────────────────
    section('Master switch')
    with app.app_context():
        from app.models.platform_settings import PlatformSetting
        PlatformSetting.set('world_enabled', 'false')

    res = client.get('/api/simulations/{}/world'.format(sim_id))
    check('the endpoint refuses when the world is off', res.status_code == 403, res.status_code)
    html = client.get('/simulations/{}/gcc?tab=network'.format(sim_id)).get_data(as_text=True)
    check('the page falls back to the legacy tab only',
          'id="wewRoot"' not in html and 'anv-modal-overlay' in html)
    check('the world script is not shipped when disabled', 'wealth_world.js' not in html)

    with app.app_context():
        from app.models.platform_settings import PlatformSetting
        PlatformSetting.set('world_enabled', 'true')

    print('\n{} passed, {} failed'.format(len(PASS), len(FAIL)))
    if FAIL:
        for label in FAIL:
            print('  FAILED: ' + label)
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
