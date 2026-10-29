"""EXEC_PLAN P3 — inspectable plans, tool registry, autonomy (L1: nothing executes).

    python test_agent_plans.py
"""
import json
import os
import sys

os.environ.setdefault('DATABASE_URL', 'sqlite:///:memory:')
os.environ.setdefault('SECRET_KEY', 'test-only')

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import create_app                                    # noqa: E402
from app.extensions import db                                 # noqa: E402
from utils.id_gen import generate_id                          # noqa: E402

PASS, FAIL = [], []


def check(label, condition, detail=''):
    (PASS if condition else FAIL).append(label)
    mark = 'ok  ' if condition else 'FAIL'
    line = '  {} {}{}'.format(mark, label, ('  -- ' + str(detail)) if detail else '')
    print(line.encode(sys.stdout.encoding or 'ascii', 'replace')
              .decode(sys.stdout.encoding or 'ascii'))


def main():
    import requests
    network = []
    requests.post = requests.get = lambda *a, **k: network.append(a[0] if a else k) or (_ for _ in ()).throw(
        AssertionError('network'))

    app = create_app('testing')
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'

    with app.app_context():
        db.create_all()
        from datetime import datetime
        from flask import g
        from app.models.user import User
        from app.models.simulation import Simulation
        from app.models.agent_action import AgentAction
        from app.models.artifact import ArtifactVersion
        from app.models.agent_plan import ToolRegistry, AgentPlan, PlanStep, ActionApproval
        from app.models.outreach_email import EmailLog
        from app.models.action_step import ActionStep
        from app.services import agent_plans
        from app.services.agent_registry import get_all_agents

        owner = User(id=generate_id(), email='o@x.com', full_name='Owner', password_hash='x',
                     email_verified=True, onboarding_completed_at=datetime.utcnow())
        other = User(id=generate_id(), email='x@x.com', full_name='Other', password_hash='x',
                     email_verified=True, onboarding_completed_at=datetime.utcnow())
        db.session.add_all([owner, other])
        db.session.flush()
        sim = Simulation(id=generate_id(), user_id=owner.id, name='S', expertise_zone='Ops', status='complete')
        db.session.add(sim)
        db.session.flush()

        def artifact(agent, layer=1):
            a = AgentAction(id=generate_id(), simulation_id=sim.id, layer_number=layer, action_type=agent,
                            status='complete', artifact='# A', created_by=owner.id, completed_at=datetime.utcnow())
            db.session.add(a)
            db.session.flush()
            db.session.add(ArtifactVersion(id=generate_id(), action_id=a.id, simulation_id=sim.id,
                                           layer_number=layer, action_type=agent, version_number=1,
                                           content='# A', is_current=True))
            db.session.commit()
            return a

        cold = artifact('cold_email_campaign')
        booking = artifact('booking_page')
        neg = artifact('negotiation_script')
        dca = artifact('dca_schedule', 5)
        ids = dict(cold=cold.id, booking=booking.id, neg=neg.id, dca=dca.id, owner=owner.id, other=other.id)

        print('\n1. Tool registry')
        tools = agent_plans.ensure_tools()
        check('registry seeded with every tool', set(tools) == set(agent_plans.TOOL_SEED), len(tools))
        check('money movement, LinkedIn and Kajabi ship disabled',
              not tools['alpaca_order'].enabled and not tools['linkedin_post'].enabled
              and not tools['kajabi_publish'].enabled)
        tools['stripe_payment_link'].cost_cents = 25
        db.session.commit()
        agent_plans.ensure_tools()
        check('re-seeding keeps admin edits', ToolRegistry.query.filter_by(
            tool_key='stripe_payment_link').first().cost_cents == 25)

        print('\n2. Autonomy')
        check('L5 agents are locked at L0', agent_plans.allowed_levels('dca_schedule') == ['L0']
              and all(agent_plans.allowed_levels(a['action_type']) == ['L0']
                      for a in get_all_agents() if a['layer'] == 5))
        check('L4 is never allowed for any agent',
              all('L4' not in agent_plans.allowed_levels(a['action_type']) for a in get_all_agents()))
        check('defaults come from the generated config',
              agent_plans.default_level('cold_email_campaign') == 'L1'
              and agent_plans.default_level('booking_page') == 'L2'
              and agent_plans.default_level('negotiation_script') == 'L3')
        try:
            agent_plans.set_level(ids['owner'], 'dca_schedule', 'L2')
            check('raising an L5 agent is refused', False)
        except ValueError:
            check('raising an L5 agent is refused', True)
        try:
            agent_plans.set_level(ids['owner'], 'cold_email_campaign', 'L4')
            check('L4 is refused', False)
        except ValueError:
            check('L4 is refused', True)
        agent_plans.set_level(ids['owner'], 'booking_page', 'L3')
        check('a user can change an agent\'s level', agent_plans.effective_level(ids['owner'], 'booking_page') == 'L3')
        check('levels are per user', agent_plans.effective_level(ids['other'], 'booking_page') == 'L2')

        client = app.test_client()

        def login(uid):
            with client.session_transaction() as sess:
                sess['_user_id'] = uid
                sess['_fresh'] = True
            g.pop('_login_user', None)

        login(ids['owner'])
        print('\n3. Plans')
        res = client.get('/api/artifacts/%s/plan' % ids['cold'])
        p = res.get_json() or {}
        check('plan created on first view', res.status_code == 200 and p.get('steps'), res.status_code)
        check('plan follows the agent\'s method template',
              [s['method'] for s in p['steps']] ==
              ['research', 'personalize', 'send', 'wait', 'follow_up', 'stop_on_reply'], [s['method'] for s in p['steps']])
        send = next(s for s in p['steps'] if s['method'] == 'send')
        check('the send step names the email tool and is hard-gated',
              send['tool_key'] == 'send_email' and send['required_reason'] == 'hard_gate'
              and send['approval_status'] == 'pending' and send['reversibility'] == 'irreversible', send)
        check('manual steps need no approval',
              next(s for s in p['steps'] if s['method'] == 'wait')['approval_status'] == 'not_required')
        check('an L1 plan does not execute', p.get('executes') is False)
        check('same plan on the next view (no duplicates)',
              client.get('/api/artifacts/%s/plan' % ids['cold']).get_json()['id'] == p['id']
              and AgentPlan.query.filter_by(action_id=ids['cold']).count() == 1)

        b = client.get('/api/artifacts/%s/plan' % ids['booking']).get_json()
        # P4: plan-level approval (L3) isn't built, so an EXECUTABLE step always
        # takes its own approval; other reversible steps are covered by the plan.
        check('L3: executable steps still need their own approval; others are covered by the plan',
              b['autonomy_level'] == 'L3'
              and all(s['approval_status'] == ('pending' if s['executable'] else 'not_required')
                      for s in b['steps'] if s['tool_key']), b['steps'])
        d = client.get('/api/artifacts/%s/plan' % ids['dca']).get_json()
        check('dca_schedule plan is plan-only and generate-only',
              d['autonomy_level'] == 'L0' and [s['method'] for s in d['steps']] == ['plan', 'review'])

        print('\n4. Decisions record, never execute')
        res = client.post('/api/plan-steps/%s/decision' % send['id'], json={'decision': 'approve'})
        q = res.get_json() or {}
        check('approving a step records the decision',
              res.status_code == 200 and next(s for s in q['steps'] if s['id'] == send['id'])['approval_status'] == 'approved')
        check('…as an approval record with its reason',
              ActionApproval.query.filter_by(target_id=send['id']).first().required_reason == 'hard_gate')
        check('nothing was sent, scheduled or called',
              EmailLog.query.count() == 0 and ActionStep.query.count() == 0 and not network)
        pend = [s for s in q['steps'] if s['approval_status'] == 'pending']
        for s in pend:
            client.post('/api/plan-steps/%s/decision' % s['id'], json={'decision': 'approve'})
        check('plan becomes approved when every step is decided',
              AgentPlan.query.get(p['id']).status == 'approved')
        client.post('/api/plan-steps/%s/decision' % send['id'], json={'decision': 'reject'})
        check('a rejected step rejects the plan', AgentPlan.query.get(p['id']).status == 'rejected')
        wait = next(s for s in p['steps'] if s['method'] == 'wait')
        check('steps without an approval refuse decisions',
              client.post('/api/plan-steps/%s/decision' % wait['id'], json={'decision': 'approve'}).status_code == 400)
        check('invalid decisions are refused',
              client.post('/api/plan-steps/%s/decision' % send['id'], json={'decision': 'run'}).status_code == 400)

        r = client.post('/api/artifacts/%s/plan/regenerate' % ids['cold']).get_json()
        check('rebuilding supersedes the old plan',
              r['id'] != p['id'] and AgentPlan.query.get(p['id']).status == 'superseded')
        check('decisions on a superseded plan are refused',
              client.post('/api/plan-steps/%s/decision' % send['id'], json={'decision': 'approve'}).status_code == 400)

        login(ids['other'])
        check('other users cannot see the plan', client.get('/api/artifacts/%s/plan' % ids['cold']).status_code == 403)
        new_send = next(s for s in r['steps'] if s['method'] == 'send')
        check('…or decide its steps',
              client.post('/api/plan-steps/%s/decision' % new_send['id'], json={'decision': 'approve'}).status_code == 403)

        print('\n5. Pages')
        login(ids['owner'])
        page = client.get('/settings/agent-autonomy')
        html = page.get_data(as_text=True)
        check('autonomy settings page renders', page.status_code == 200 and 'Agent autonomy' in html
              and 'nothing executes' in html)
        check('L5 agent select is locked', 'data-agent="dca_schedule" disabled' in html)
        res = client.put('/api/agent-autonomy/negotiation_script', json={'level': 'L1'})
        check('autonomy API saves a level', res.status_code == 200 and agent_plans.effective_level(
            ids['owner'], 'negotiation_script') == 'L1')
        check('autonomy API refuses L4', client.put('/api/agent-autonomy/negotiation_script',
                                                    json={'level': 'L4'}).status_code == 400)
        art = client.get('/simulations/%s/artifacts/%s' % (sim.id, ids['cold'])).get_data(as_text=True)
        check('artifact page has the Plan button', 'aabPlanBtn' in art)

        print('\n6. Cycle delete clears plans')
        from app.services.cycle_admin_service import delete_cycles
        res = delete_cycles(sim.id, include_orphans=True)
        check('cycle delete succeeds', res.get('ok'), res.get('message'))
        check('plans, steps and decisions removed with their artifacts',
              AgentPlan.query.count() == 0 and PlanStep.query.count() == 0 and ActionApproval.query.count() == 0)

    print('\n%d passed, %d failed' % (len(PASS), len(FAIL)))
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
