"""No code path can place a brokerage order (COMPLIANCE_PLAN B4, hard rule 6).

Alpaca is read-only: it may read the account; nothing may create, configure
or receive an order. Network calls are recorded, never made.

    python test_no_brokerage_orders.py
"""
import os
import re
import sys
from types import SimpleNamespace

os.environ.setdefault('DATABASE_URL', 'sqlite:///:memory:')
os.environ.setdefault('SECRET_KEY', 'test-only')
ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

PASS, FAIL = [], []


def check(label, condition, detail=''):
    (PASS if condition else FAIL).append(label)
    line = '  {} {}{}'.format('ok  ' if condition else 'FAIL', label, ('  -- ' + str(detail)) if detail else '')
    enc = sys.stdout.encoding or 'ascii'
    print(line.encode(enc, 'replace').decode(enc))


def main():
    print('\nSource')
    hits = []
    for base in ('app', 'utils', 'config'):
        for root, _d, files in os.walk(os.path.join(ROOT, base)):
            if '__pycache__' in root:
                continue
            for f in files:
                if f.endswith(('.py', '.js', '.json', '.html')):
                    txt = open(os.path.join(root, f), encoding='utf-8', errors='ignore').read()
                    for pat in (r'/v2/orders', r'submit_order', r'place_order', r'alpaca_order', r'/orders\b',
                                r'fintech-toggle', r'webhooks/alpaca'):
                        if re.search(pat, txt):
                            hits.append('%s: %s' % (os.path.relpath(os.path.join(root, f), ROOT), pat))
    check('no order endpoint, order tool, trading toggle or trade webhook in the code', not hits, hits[:6])
    check('the fintech switch module is gone', not os.path.exists(os.path.join(ROOT, 'app', 'services', 'fintech.py')))

    import requests
    calls = []

    class _Resp:
        status_code = 200
        ok = True
        text = '{}'

        def json(self):
            return {'id': 'acct', 'status': 'ACTIVE', 'account_number': 'PA1'}

    def _rec(method):
        def f(url, *a, **k):
            calls.append((method, url))
            return _Resp()
        return f
    for m in ('get', 'post', 'put', 'patch', 'delete'):
        setattr(requests, m, _rec(m.upper()))
    requests.request = lambda method, url, *a, **k: (calls.append((method.upper(), url)), _Resp())[1]

    from app import create_app
    from app.extensions import db
    from utils.id_gen import generate_id
    from cryptography.fernet import Fernet
    from datetime import datetime
    app = create_app('testing')
    app.config.update(SQLALCHEMY_DATABASE_URI='sqlite:///:memory:', WTF_CSRF_ENABLED=False,
                      ENCRYPTION_KEY=Fernet.generate_key().decode())
    with app.app_context():
        from flask import g
        from app import models as _models  # noqa: F401
        db.create_all()
        from app.models.user import User
        from app.models.simulation import Simulation
        from app.services import agent_plans, plan_executor, claude, legal_text
        u = User(id=generate_id(), email='i@t.local', full_name='Ivy', password_hash='x', email_verified=True,
                 onboarding_completed_at=datetime.utcnow(), is_admin=True)
        db.session.add(u)
        sim = Simulation(id=generate_id(), user_id=u.id, name='S', expertise_zone='Ops', status='complete')
        db.session.add(sim)
        db.session.commit()

        print('\nTools')
        tools = getattr(agent_plans, 'TOOLS', None) or getattr(agent_plans, '_TOOLS', None) or {}
        check('no brokerage tool is registered', not any('alpaca' in str(k) or 'order' in str(k) for k in tools), list(tools)[:12])
        check('only payment links and booking pages can execute',
              set(plan_executor.EXECUTABLE_TOOLS) == {'stripe_payment_link', 'calcom_booking_link'})

        client = app.test_client()
        with client.session_transaction() as s:
            s['_user_id'] = u.id
            s['_fresh'] = True
        g.pop('_login_user', None)

        print('\nEndpoints')
        check('the automated-trading toggle endpoint is gone',
              client.post('/api/integrations/alpaca/fintech-toggle', json={'enabled': True}).status_code == 404)
        check('the trade webhook is gone', client.post('/webhooks/alpaca/%s' % u.id, json={'event': 'fill'}).status_code == 404)
        calls.clear()
        client.post('/api/integrations/alpaca/validate', json={'api_key': 'k', 'api_secret': 's'})
        g.pop('_login_user', None)
        client.post('/api/integrations/alpaca/connect', json={'api_key': 'k', 'api_secret': 's', 'is_paper': True})
        g.pop('_login_user', None)
        check('validating and connecting Alpaca only reads the account', calls and all('/orders' not in url for _m, url in calls)
              and all(m == 'GET' for m, _u in calls), calls)

        settings = client.get('/settings/integrations').get_data(as_text=True)
        g.pop('_login_user', None)
        check('Settings offers no automated trading', 'automated trading' not in settings.lower()
              and 'place trades in my' not in settings and 'Target ETF symbols' not in settings)
        admin = client.get('/admin').get_data(as_text=True)
        g.pop('_login_user', None)
        check('Admin has no fintech switch', 'Fintech API Integration' not in admin)

        print('\nA full Layer 5 run')

        class Fake:
            def __init__(self):
                self.messages = self
                self.calls = []

            def create(self, **kw):
                self.calls.append(kw)
                return SimpleNamespace(content=[SimpleNamespace(text='A common approach is to automate a fixed '
                                                                     'contribution. Questions for an adviser: …')],
                                       usage=SimpleNamespace(input_tokens=1, output_tokens=1), stop_reason='end_turn')
        fake = Fake()
        real_client, real_log = claude._client, claude._log_interaction
        claude._client, claude._log_interaction = (lambda: fake), (lambda *a, **k: None)
        calls.clear()
        try:
            for agent in ('dca_schedule', 'income_allocation', 'fund_recommendations'):
                claude._execute_agent_action_body(action_type=agent, layer_number=5, expertise_zone='Ops',
                                                  parsed_text='', user_inputs={'contribution_amount': '500'},
                                                  user_id=u.id, simulation_id=sim.id, model='m')
            check('no Layer 5 agent calls a brokerage', not [c for c in calls if 'alpaca' in c[1] or '/orders' in c[1]], calls)

            print('\nThe simulation\'s own Layer 5')
            fake2 = Fake()
            fake2.create = lambda **kw: (fake2.calls.append(kw), SimpleNamespace(
                content=[SimpleNamespace(text='{"layer_name": "x", "income_streams": []}')],
                usage=SimpleNamespace(input_tokens=1, output_tokens=1), stop_reason='end_turn'))[1]
            claude._client = lambda: fake2
            claude.generate_simulation_layer(layer_number=5, expertise_zone='Ops', focus_hint='', parsed_text='',
                                             user_id=u.id, simulation_id=sim.id, fintech_enabled=True)
            p = fake2.calls[0]['messages'][0]['content']
            check('the layer prompt is education only, even with the old switch passed in',
                  'Include specific fund tickers' not in p and 'not personalised advice' in p)
            replies = iter(['{"layer_name": "x", "income_streams": [{"name": "Buy VTI"}]}',
                            '{"layer_name": "x", "income_streams": [{"name": "Use Vanguard"}]}'])
            fake2.create = lambda **kw: SimpleNamespace(content=[SimpleNamespace(text=next(replies))],
                                                        usage=SimpleNamespace(input_tokens=1, output_tokens=1),
                                                        stop_reason='end_turn')
            out = claude.generate_simulation_layer(layer_number=5, expertise_zone='Ops', focus_hint='', parsed_text='',
                                                   user_id=u.id, simulation_id=sim.id)
            check('…and named products in it are regenerated, then blanked',
                  'VTI' not in str(out) and 'Vanguard' not in str(out) and '[a provider]' in str(out))
        finally:
            claude._client, claude._log_interaction = real_client, real_log
        check('legal text for the referral is in place', bool(legal_text.get('l5_referral_adviser')))

    print('\n%d passed, %d failed' % (len(PASS), len(FAIL)))
    if FAIL:
        for label in FAIL:
            print('  FAILED: ' + label)
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
