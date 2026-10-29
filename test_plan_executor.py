"""EXEC_PLAN P4 — L2 execution of approved REVERSIBLE plan steps.

Stripe and Cal.com are faked; requests is blocked so a stray call fails.

    python test_plan_executor.py
"""
import os
import sys
from datetime import datetime
from types import SimpleNamespace

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
    requests.post = requests.get = requests.delete = (
        lambda *a, **k: network.append(a[0] if a else k) or (_ for _ in ()).throw(AssertionError('network')))

    app = create_app('testing')
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
    from cryptography.fernet import Fernet
    app.config['ENCRYPTION_KEY'] = Fernet.generate_key().decode()

    with app.app_context():
        db.create_all()
        from flask import g
        from app.models.user import User
        from app.models.simulation import Simulation
        from app.models.agent_action import AgentAction
        from app.models.artifact import ArtifactVersion
        from app.models.agent_plan import ExecutionRun, PlanStep, AgentPlan
        from app.models.integration import UserIntegration
        from app.models.layer6 import ActionItem
        from app.models.observability import GuardrailRule
        from app.models.platform_settings import PlatformSetting
        from app.models.audit_log import AuditLog
        from app.models.artifact_exec import ArtifactAction
        from app.services import agent_plans, guardrail_service as gr
        from app.services import stripe_connect_service as sc
        from app.services import cal_service
        from app.services.token_crypto import encrypt_token
        import utils.action_items as ai

        ai._send_escalation_email = lambda item: None      # no email from tests

        stripe_calls, undo_calls, cal_calls = [], [], []

        def fake_link(user_id, name, cents, **kw):
            stripe_calls.append((name, cents, kw.get('action_id')))
            return {'payment_link_url': 'https://buy.stripe.com/test_%d' % len(stripe_calls),
                    'payment_link_id': 'plink_%d' % len(stripe_calls), 'product_id': 'prod', 'price_id': 'price'}

        sc.create_stripe_payment_link = fake_link
        sc._get_integration = lambda uid: (None, SimpleNamespace(PaymentLink=SimpleNamespace(
            modify=lambda lid, **kw: undo_calls.append((lid, kw)))))

        class FakeCal:
            def __init__(self, token): pass
            def create_event_type(self, title, length, price=0, **kw):
                cal_calls.append((title, length, price)); return {'id': 77, 'slug': 'discovery-call'}
            def get_me(self): return {'user': {'username': 'dana'}}
            def get_booking_page_url(self, u, slug=None): return 'https://cal.com/%s/%s' % (u, slug)
            def delete_event_type(self, eid): cal_calls.append(('delete', eid))
        cal_service.CalClient = FakeCal

        owner = User(id=generate_id(), email='o@x.com', full_name='Owner', password_hash='x',
                     email_verified=True, onboarding_completed_at=datetime.utcnow())
        other = User(id=generate_id(), email='x@x.com', full_name='X', password_hash='x',
                     email_verified=True, onboarding_completed_at=datetime.utcnow())
        db.session.add_all([owner, other])
        db.session.flush()
        sim = Simulation(id=generate_id(), user_id=owner.id, name='S', expertise_zone='Ops', status='complete')
        db.session.add(sim)
        db.session.flush()
        for provider in ('stripe', 'cal'):
            rec = UserIntegration(id=generate_id(), user_id=owner.id, provider=provider)
            rec.access_token_enc = encrypt_token('tok-' + provider)
            db.session.add(rec)

        def artifact(agent, content):
            a = AgentAction(id=generate_id(), simulation_id=sim.id, layer_number=1, action_type=agent,
                            status='complete', artifact=content, created_by=owner.id, completed_at=datetime.utcnow())
            db.session.add(a)
            db.session.flush()
            db.session.add(ArtifactVersion(id=generate_id(), action_id=a.id, simulation_id=sim.id, layer_number=1,
                                           action_type=agent, version_number=1, content=content, is_current=True))
            db.session.commit()
            return a

        rc = artifact('rate_card', '# Rate Card\n\nTier 1 Diagnostic — $15,000\n')
        bk = artifact('booking_page', '# Booking page')
        ce = artifact('cold_email_campaign', '{"prospects": []}')
        ids = dict(rc=rc.id, bk=bk.id, ce=ce.id, owner=owner.id, other=other.id, sim=sim.id)

        client = app.test_client()
        # The executor resets the DB session after the external call (as it must in
        # production); drop Flask-Login's per-context user cache before every request.
        _post, _get = client.post, client.get
        client.post = lambda *a, **k: (g.pop('_login_user', None), _post(*a, **k))[1]
        client.get = lambda *a, **k: (g.pop('_login_user', None), _get(*a, **k))[1]

        def login(uid):
            with client.session_transaction() as sess:
                sess['_user_id'] = uid
                sess['_fresh'] = True
            g.pop('_login_user', None)

        def step_of(plan, method):
            return next(s for s in plan['steps'] if s['method'] == method)

        login(ids['owner'])
        print('\n1. Plan and escalation')
        p = client.get('/api/artifacts/%s/plan' % ids['rc']).get_json()
        link = step_of(p, 'create_payment_link')
        check('rate card (L2) can execute its payment-link step', p['executes'] and link['executable'])
        check('the step is pending approval', link['approval_status'] == 'pending')
        check('defaults are prefilled from the artifact (first price: $15,000)',
              link['default_inputs']['amount_usd'] == 15000.0, link['default_inputs'])
        esc_item = ActionItem.query.filter_by(source_artifact_id=link['id']).first()
        check('the pending step surfaces in Escalations', esc_item is not None
              and esc_item.item_type == 'agent_approval_required' and esc_item.resolved_at is None)
        esc_id = esc_item.id

        url = '/api/plan-steps/%s/execute' % link['id']
        good = {'product_name': 'Tier 1 Diagnostic', 'amount_usd': '15000'}
        res = client.post(url, json={'inputs': good})
        check('cannot run before approval', res.status_code == 400 and not stripe_calls, res.get_json())

        print('\n2. Approved step runs, once, on the user\'s account')
        client.post('/api/plan-steps/%s/decision' % link['id'], json={'decision': 'approve'})
        res = client.post(url, json={'inputs': {'product_name': 'X', 'amount_usd': '0'}})
        check('invalid inputs are refused before anything runs', res.status_code == 400 and not stripe_calls)
        res = client.post(url, json={'inputs': good})
        run = res.get_json() or {}
        check('the approved step runs', res.status_code == 200 and run.get('status') == 'succeeded', run)
        check('Stripe got exactly the confirmed inputs',
              stripe_calls == [('Tier 1 Diagnostic', 1500000, ids['rc'])], stripe_calls)
        check('the link comes back to the plan', run['response']['url'].startswith('https://buy.stripe.com/'))
        check('execution recorded', ExecutionRun.query.filter_by(step_id=link['id'], status='succeeded').count() == 1)
        check('audit log and artifact action log written',
              AuditLog.query.filter_by(action='plan_step_executed').count() == 1
              and ArtifactAction.query.filter_by(artifact_id=ids['rc'], verb='execute').count() == 1)
        db.session.expire_all()
        check('the escalation is resolved', ActionItem.query.get(esc_id).resolved_at is not None)
        res = client.post(url, json={'inputs': good})
        check('running again returns the same run (no second link)',
              res.get_json()['id'] == run['id'] and len(stripe_calls) == 1)

        res = client.post('/api/execution-runs/%s/undo' % run['id'])
        check('undo deactivates the payment link',
              res.status_code == 200 and res.get_json()['status'] == 'undone'
              and undo_calls == [('plink_1', {'active': False})], undo_calls)
        check('undo cannot be repeated', client.post('/api/execution-runs/%s/undo' % run['id']).status_code == 400)

        print('\n3. Refusals')
        p2 = client.post('/api/artifacts/%s/plan/regenerate' % ids['rc']).get_json()
        link2 = step_of(p2, 'create_payment_link')
        client.post('/api/plan-steps/%s/decision' % link2['id'], json={'decision': 'approve'})
        PlatformSetting.set('exec_max_runs_per_agent_per_day', '1')
        db.session.commit()
        res = client.post('/api/plan-steps/%s/execute' % link2['id'], json={'inputs': good})
        check('the per-agent daily limit is enforced', res.status_code == 400 and 'limit' in res.get_json()['message']
              and len(stripe_calls) == 1, res.get_json())
        PlatformSetting.set('exec_max_runs_per_agent_per_day', '5')
        db.session.commit()

        agent_plans.set_level(ids['owner'], 'rate_card', 'L1')
        res = client.post('/api/plan-steps/%s/execute' % link2['id'], json={'inputs': good})
        check('lowering autonomy to L1 stops execution', res.status_code == 400 and 'L2' in res.get_json()['message'])
        agent_plans.set_level(ids['owner'], 'rate_card', 'L2')

        rule = GuardrailRule(id=generate_id(), **{k: v for k, v in gr.validate_rule(dict(
            name='No payment links', rule_type='tool_allowlist', scope='global', scope_ref=None,
            action='block', severity='high',
            definition={'mode': 'deny', 'tools': ['stripe_payment_link']})).items() if k != 'definition'},
            definition_json={'mode': 'deny', 'tools': ['stripe_payment_link']})
        db.session.add(rule)
        db.session.commit()
        res = client.post('/api/plan-steps/%s/execute' % link2['id'], json={'inputs': good})
        r = res.get_json() or {}
        check('a blocking guardrail stops the call at the tool boundary',
              r.get('status') == 'blocked' and 'no-payment-links' in (r.get('guardrail_result') or '')
              and len(stripe_calls) == 1, r)
        db.session.delete(GuardrailRule.query.get(rule.id))
        db.session.commit()

        pc = client.get('/api/artifacts/%s/plan' % ids['ce']).get_json()
        send = step_of(pc, 'send')
        client.post('/api/plan-steps/%s/decision' % send['id'], json={'decision': 'approve'})
        res = client.post('/api/plan-steps/%s/execute' % send['id'], json={'inputs': {}})
        check('an approved SEND step still cannot execute (hard gate, P5)',
              res.status_code == 400 and not send['executable'])

        login(ids['other'])
        check('other users cannot execute', client.post('/api/plan-steps/%s/execute' % link2['id'],
                                                        json={'inputs': good}).status_code == 403)
        check('…or undo', client.post('/api/execution-runs/%s/undo' % run['id']).status_code == 403)
        login(ids['owner'])

        print('\n4. Cal.com booking link')
        pb = client.get('/api/artifacts/%s/plan' % ids['bk']).get_json()
        pub = step_of(pb, 'publish')
        client.post('/api/plan-steps/%s/decision' % pub['id'], json={'decision': 'approve'})
        res = client.post('/api/plan-steps/%s/execute' % pub['id'], json={'inputs': {
            'title': 'Discovery call', 'length_minutes': '30', 'price_usd': '0'}})
        r = res.get_json() or {}
        check('booking step creates the event type on the user\'s Cal.com',
              r.get('status') == 'succeeded' and r['response']['url'] == 'https://cal.com/dana/discovery-call'
              and cal_calls[0] == ('Discovery call', 30, 0), r)
        client.post('/api/execution-runs/%s/undo' % r['id'])
        check('undo deletes the event type', cal_calls[-1] == ('delete', 77))

        UserIntegration.query.filter_by(user_id=ids['owner'], provider='stripe').delete()
        db.session.commit()
        p3 = client.post('/api/artifacts/%s/plan/regenerate' % ids['rc']).get_json()
        l3 = step_of(p3, 'create_payment_link')
        client.post('/api/plan-steps/%s/decision' % l3['id'], json={'decision': 'approve'})
        res = client.post('/api/plan-steps/%s/execute' % l3['id'], json={'inputs': good})
        r = res.get_json() or {}
        check('a disconnected integration is reported, not attempted',
              r.get('status') == 'failed' and 'Connect Stripe' in (r.get('error') or ''), r)

        print('\n5. Evidence survives cycle delete')
        from app.services.cycle_admin_service import delete_cycles
        before = ExecutionRun.query.count()
        delete_cycles(ids['sim'], include_orphans=True)
        check('plans are cleared but execution records are kept',
              AgentPlan.query.count() == 0 and PlanStep.query.count() == 0
              and ExecutionRun.query.count() == before and before > 0)
        check('no stray network calls', not network, network)

    print('\n%d passed, %d failed' % (len(PASS), len(FAIL)))
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
