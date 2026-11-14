"""Founder Track admin, pricing and payment (FOUNDER_PLAN.md WP3).

Admin-only access to /admin/founder and its APIs, settings validation, the
autonomy-matrix rules (negotiate_terms stays 0; first contact never above the
cap), the counsel gate, the investor CSV import, Founder Track checkout
(Stripe faked), the webhook branch (idempotent) and the success-return
fallback, the add-on price, and that the existing checkout branches still work.
In-memory SQLite; requests is blocked.

    python test_founder_admin_billing.py
"""
import io
import json
import os
import sys
from datetime import date, datetime, timedelta

os.environ.setdefault('DATABASE_URL', 'sqlite:///:memory:')
os.environ.setdefault('SECRET_KEY', 'test-only')
ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

PASS, FAIL = [], []

NEW_PY = ['app/blueprints/founder/admin.py', 'app/blueprints/founder/billing.py']


def check(label, condition, detail=''):
    (PASS if condition else FAIL).append(label)
    line = '  {} {}{}'.format('ok  ' if condition else 'FAIL', label, ('  -- ' + str(detail)) if detail else '')
    enc = sys.stdout.encoding or 'ascii'
    print(line.encode(enc, 'replace').decode(enc))


class FakeSession(dict):
    """Stands in for a stripe.checkout.Session (dict access plus attributes)."""
    def __getattr__(self, k):
        try:
            return self[k]
        except KeyError:
            raise AttributeError(k)


def main():
    import requests
    requests.post = requests.get = lambda *a, **k: (_ for _ in ()).throw(AssertionError('network'))

    print('\n1. New files are Python 3.9 safe')
    missing = [f for f in NEW_PY
               if 'from __future__ import annotations' not in io.open(os.path.join(ROOT, f), encoding='utf-8').read()]
    check('every new .py file has "from __future__ import annotations"', not missing, missing)

    from app import create_app
    from app.extensions import db
    from utils.id_gen import generate_id
    app = create_app('testing')
    app.config.update(SQLALCHEMY_DATABASE_URI='sqlite:///:memory:', WTF_CSRF_ENABLED=False,
                      STRIPE_SECRET_KEY='sk_test_fake', STRIPE_WEBHOOK_SECRET='whsec_fake')

    # ── Stripe fakes ──
    import stripe
    created, retrieved = [], {}

    def fake_create(**kw):
        sid = 'cs_test_%d' % (len(created) + 1)
        created.append(kw)
        return FakeSession(id=sid, url='https://checkout.stripe.test/' + sid)

    stripe.checkout.Session.create = staticmethod(fake_create)
    stripe.checkout.Session.retrieve = staticmethod(lambda sid, **kw: retrieved[sid])

    from app.blueprints.payments import routes as pay_routes
    next_event = {}
    pay_routes.construct_webhook_event = lambda payload, sig: next_event['e']

    with app.app_context():
        db.create_all()
        from flask import g
        from app.models.audit_log import AuditLog
        from app.models.founder import FounderVenture, InvestorProfile, InvestorTarget
        from app.models.mail_crm import UserFeatureOverride
        from app.models.simulation import Simulation
        from app.models.user import User
        from app.services import founder_config, founder_feature, founder_ventures, stripe_service
        now = datetime.utcnow()

        def user(email, admin=False):
            u = User(id=generate_id(), email=email, full_name=email.split('@')[0].title(), password_hash='x',
                     is_admin=admin, email_verified=True, onboarding_completed_at=now)
            db.session.add(u)
            db.session.commit()
            return u

        admin = user('admin@x.com', admin=True)
        founder = user('founder@x.com')
        member = user('member@x.com')       # has a completed simulation -> add-on price
        other = user('other@x.com')
        A_ID, F_ID, M_ID, O_ID = admin.id, founder.id, member.id, other.id

        client = app.test_client()
        for verb in ('get', 'post', 'put', 'delete', 'open'):
            orig = getattr(client, verb)
            setattr(client, verb, (lambda o: lambda *a, **k: (g.pop('_login_user', None), o(*a, **k))[1])(orig))

        def login(uid):
            with client.session_transaction() as s:
                if uid:
                    s['_user_id'], s['_fresh'] = uid, True
                else:
                    s.pop('_user_id', None)

        def api(method, path, body=None, uid=A_ID):
            login(uid)
            r = getattr(client, method)(path, json=body) if body is not None else getattr(client, method)(path)
            return r.status_code, (r.get_json(silent=True) or {})

        def put(settings, uid=A_ID):
            return api('put', '/api/admin/founder/settings', {'settings': settings}, uid)

        def audits(action):
            return AuditLog.query.filter_by(action=action).count()

        print('\n2. Admin only')
        login(None)
        r = client.get('/admin/founder')
        check('anonymous is sent to sign in', r.status_code in (302, 401) and r.status_code != 200, r.status_code)
        login(F_ID)
        check('a member gets 403 at /admin/founder', client.get('/admin/founder').status_code == 403)
        for method, path in (('get', '/api/admin/founder/settings'), ('put', '/api/admin/founder/settings'),
                             ('post', '/api/admin/founder/mode'), ('post', '/api/admin/founder/users'),
                             ('post', '/api/admin/founder/counsel'), ('get', '/api/admin/founder/investors'),
                             ('post', '/api/admin/founder/investors/import'),
                             ('delete', '/api/admin/founder/investors/XXXXXXXXX')):
            code, _ = api(method, path, {}, uid=F_ID)
            check('member gets 403 from %s %s' % (method.upper(), path), code == 403, code)
        check('the track is off by default', founder_feature.mode() == 'off')
        login(A_ID)
        r = client.get('/admin/founder')
        html = r.get_data(as_text=True)
        check('an admin gets the page while the track is off', r.status_code == 200, r.status_code)
        check('the page has every tab', all('id="tab-%s"' % t in html for t in
                                            ('overview', 'rubric', 'readiness', 'autonomy', 'caps', 'targets',
                                             'pricing', 'preview', 'investors', 'names', 'counsel')))
        check('the counsel date is not pre-filled', 'id="counselDate" max=' in html
              and 'id="counselDate" max="%s" value=' % date.today().isoformat() not in html)
        check('investor connection shows locked until counsel sign-off', 'Locked until counsel sign-off' in html)
        check('the subnav marks Founder Track active',
              'class="subnav-link active" aria-current="true">Founder Track<' in html)
        check('?tab=pricing opens the pricing tab',
              'id="tab-pricing" aria-controls="panel-pricing"\n            aria-selected="true"'
              in client.get('/admin/founder?tab=pricing').get_data(as_text=True))

        print('\n3. Settings validation')
        bad = (
            ({'fnd_price_cents': 'abc'}, 'a non-number price'),
            ({'fnd_price_cents': -5}, 'a negative price'),
            ({'fnd_fee_mode': 'recurring'}, 'recurring fee mode'),
            ({'fnd_preview_level': 'everything'}, 'an unknown preview level'),
            ({'fnd_rubric_weights': {d: 0 for d in founder_config.DIMENSIONS}}, 'all-zero rubric weights'),
            ({'fnd_rubric_weights': {'vibes': 1}}, 'an unknown rubric dimension'),
            ({'fnd_targets': {'eval_completion': 140}}, 'a target above 100%'),
            ({'fnd_gate_rung_applications': 9}, 'a gate rung above 5'),
            ({'fnd_counsel_signoff_at': '2026-01-01'}, 'the counsel date through the settings endpoint'),
            ({'fnd_nonsense': 1}, 'an unknown key'),
            ({}, 'an empty change'),
            ({'fnd_price_cents': 49}, 'a price below the card minimum'),
            ({'fnd_price_cents': 0}, 'a zero price (no free unlock exists)'),
            ({'fnd_addon_price_cents': 10}, 'an add-on price below the card minimum'),
            ({'fnd_addon_price_cents': 59900}, 'an add-on price above the Founder Track price'),
            ({'fnd_price_cents': 19900}, 'a Founder Track price below the add-on price'),
            ({'fnd_investor_access_cents': 25}, 'an investor access price between $0 and the card minimum'),
        )
        before = audits('founder_setting')
        for change, label in bad:
            code, body = put(change)
            check('refuses %s (400 with a message)' % label, code == 400 and body.get('message'), (code, body))
        check('refused changes write nothing', audits('founder_setting') == before
              and founder_config.get('fnd_price_cents') == 49900)
        code, body = put({'fnd_price_cents': 39900, 'fnd_addon_price_cents': '19900', 'fnd_refund_window_days': 30,
                          'fnd_preview_level': 'top3', 'fnd_fee_mode': 'one_time',
                          'fnd_rubric_weights': {'team': 3}, 'fnd_targets': {'paid_poc_rate': 25}})
        check('valid settings save', code == 200, body)
        check('... and read back typed', founder_config.get('fnd_price_cents') == 39900
              and founder_config.get('fnd_addon_price_cents') == 19900
              and founder_config.get('fnd_preview_level') == 'top3'
              and founder_config.get('fnd_rubric_weights')['team'] == 3.0
              and founder_config.get('fnd_rubric_weights')['market'] == 1.0
              and founder_config.get('fnd_targets')['paid_poc_rate'] == 25)
        check('every saved key is audit-logged', audits('founder_setting') == before + 7,
              audits('founder_setting') - before)
        code, body = put({'fnd_investor_access_cents': 0})
        check('investor access may be $0', code == 200, body)
        put({'fnd_investor_access_cents': 4900})
        code, body = put({'fnd_price_cents': 50, 'fnd_addon_price_cents': 50})
        check('prices at the card minimum save', code == 200, body)
        put({'fnd_price_cents': 39900, 'fnd_addon_price_cents': 19900})
        code, _ = put({'fnd_gate_rung_applications': 3, 'fnd_gate_rung_intros': 2})
        check('the introductions gate cannot sit below the applications gate', code == 400)
        code, _ = put({'fnd_gate_rung_applications': 3, 'fnd_gate_rung_intros': 4})
        check('consistent gates save', code == 200 and founder_config.get('fnd_gate_rung_applications') == 3)

        print('\n4. Autonomy matrix and the first-contact cap')
        m = founder_config.get('fnd_autonomy_matrix')
        code, body = put({'fnd_autonomy_matrix': {'negotiate_terms': {'default': 1, 'max': 1}}})
        check('negotiate_terms cannot be raised above 0', code == 400, body)
        code, _ = put({'fnd_autonomy_matrix': {'negotiate_terms': {'default': 0, 'max': 2}}})
        check('... not even its maximum', code == 400)
        check('negotiate_terms is still 0/0', founder_config.get('fnd_autonomy_matrix')['negotiate_terms']
              == {'default': 0, 'max': 0})
        check('the default first-contact cap is 3', founder_config.get('fnd_first_contact_cap') == 3)
        code, body = put({'fnd_autonomy_matrix': {'first_contact': {'default': 2, 'max': 4}}})
        check('first contact cannot be raised above the cap', code == 400 and 'cap' in body.get('message', ''), body)
        code, _ = put({'fnd_autonomy_matrix': {'first_contact': {'default': 4, 'max': 4}}, 'fnd_first_contact_cap': 3})
        check('... nor its default level', code == 400)
        code, _ = put({'fnd_first_contact_cap': 1})
        check('the cap cannot drop below the current first-contact levels', code == 400)
        code, _ = put({'fnd_autonomy_matrix': {'first_contact': {'default': 2, 'max': 4}}, 'fnd_first_contact_cap': 4})
        check('raising the cap and the row together saves', code == 200
              and founder_config.get('fnd_autonomy_matrix')['first_contact'] == {'default': 2, 'max': 4})
        code, _ = put({'fnd_autonomy_matrix': {'research': {'default': 4, 'max': 3}}})
        check('a default above its maximum is refused', code == 400)
        code, _ = put({'fnd_autonomy_matrix': {'research': {'default': 5, 'max': 5}}})
        check('levels above 4 are refused', code == 400)
        code, _ = put({'fnd_autonomy_matrix': {'teleport': {'default': 1, 'max': 1}}})
        check('unknown action types are refused', code == 400)
        code, _ = put({'fnd_autonomy_matrix': {'first_contact': {'default': 1, 'max': 2}}, 'fnd_first_contact_cap': 2})
        check('lowering both saves', code == 200 and founder_config.get('fnd_first_contact_cap') == 2)
        check('other rows keep their values', founder_config.get('fnd_autonomy_matrix')['research'] == m['research'])

        print('\n5. Switches, allowlist and the counsel gate')
        code, body = api('post', '/api/admin/founder/mode', {'key': 'founder_investor', 'mode': 'on'})
        check('founder_investor refuses to turn on without counsel sign-off', code == 400, body)
        code, _ = api('post', '/api/admin/founder/mode', {'key': 'founder_investor', 'mode': 'allowlist'})
        check('... or to allowlist', code == 400 and founder_feature.mode('founder_investor') == 'off')
        code, _ = api('post', '/api/admin/founder/mode', {'key': 'founder_track', 'mode': 'sometimes'})
        check('an unknown mode is refused', code == 400)
        code, _ = api('post', '/api/admin/founder/mode', {'key': 'nope', 'mode': 'on'})
        check('an unknown switch is refused', code == 400)
        code, body = api('post', '/api/admin/founder/mode', {'key': 'founder_track', 'mode': 'allowlist'})
        check('founder_track goes to allowlist', code == 200 and founder_feature.mode() == 'allowlist', body)
        check('the mode change is audit-logged', audits('founder_mode') >= 1)
        code, body = api('post', '/api/admin/founder/users', {'key': 'founder_track', 'emails': ['ghost@x.com'],
                                                              'state': 'allowed'})
        check('an unknown email is refused by name', code == 400 and 'ghost@x.com' in body.get('message', ''))
        code, body = api('post', '/api/admin/founder/users', {'key': 'founder_track', 'state': 'allowed',
                                                              'emails': ['Founder@x.com', 'member@x.com']})
        check('emails are allowlisted (case-insensitive)', code == 200 and body.get('updated') == 2, body)
        check('the override rows exist', UserFeatureOverride.query.filter_by(feature_key='founder_track',
                                                                             state='allowed').count() == 2)
        check('the access change is audit-logged', audits('founder_access') == 1)
        code, _ = api('post', '/api/admin/founder/users', {'emails': ['other@x.com'], 'state': 'superuser'})
        check('an unknown state is refused', code == 400)
        check('an allowlisted member can use the track', founder_feature.is_enabled(db.session.get(User, F_ID)))
        check('a member not on the list cannot', not founder_feature.is_enabled(db.session.get(User, O_ID)))

        code, _ = api('post', '/api/admin/founder/counsel', {'action': 'record', 'date': '2026-11-01'})
        check('counsel sign-off needs confirm', code == 400 and not founder_config.counsel_signed_off())
        future = (date.today() + timedelta(days=3)).isoformat()
        code, _ = api('post', '/api/admin/founder/counsel', {'action': 'record', 'date': future, 'confirm': True})
        check('a future sign-off date is refused', code == 400)
        code, _ = api('post', '/api/admin/founder/counsel', {'action': 'record', 'date': 'soon', 'confirm': True})
        check('a malformed date is refused', code == 400)
        code, _ = api('post', '/api/admin/founder/counsel', {'action': 'record', 'date': '2026-11-01',
                                                             'confirm': True})
        check('a sign-off without a counsel reference is refused', code == 400
              and not founder_config.counsel_signed_off())
        code, body = api('post', '/api/admin/founder/counsel', {'action': 'record', 'date': '2026-11-01',
                                                                'reference': 'Lee & Hart LLP memo 11/01',
                                                                'confirm': True})
        check('the sign-off date is recorded', code == 200 and founder_config.get('fnd_counsel_signoff_at')
              == '2026-11-01', body)
        rec = AuditLog.query.filter_by(action='founder_counsel_record').first()
        check('... with the counsel reference in the audit log', rec is not None
              and rec.extra.get('reference') == 'Lee & Hart LLP memo 11/01' and rec.user_id == A_ID)
        html = (login(A_ID), client.get('/admin/founder?tab=counsel').get_data(as_text=True))[1]
        check('the page shows the sign-off date and no lock', '2026-11-01' in html
              and 'Locked until counsel sign-off' not in html)
        code, _ = api('post', '/api/admin/founder/mode', {'key': 'founder_investor', 'mode': 'allowlist'})
        check('founder_investor can turn on after sign-off', code == 200
              and founder_feature.mode('founder_investor') == 'allowlist')
        code, body = api('post', '/api/admin/founder/counsel', {'action': 'clear', 'confirm': True})
        check('clearing is refused while investor connection is on', code == 400
              and founder_config.counsel_signed_off(), body)
        api('post', '/api/admin/founder/mode', {'key': 'founder_investor', 'mode': 'off'})
        code, _ = api('post', '/api/admin/founder/counsel', {'action': 'clear', 'confirm': True})
        check('with the switch off, the sign-off can be cleared', code == 200
              and not founder_config.counsel_signed_off())

        print('\n6. Investor directory CSV import')
        csv_text = ('name,firm,kind,stages,check_min,check_max,sectors,geographies,apply_url,accepts_applications,'
                    'auto_submit_allowed,notes\n'
                    'Ada Ventures,Ada,investor,pre-seed;seed,$100k,1.5m,fintech;health,UK;EU,https://ada.vc/apply,yes,no,'
                    'Leads rounds\n'
                    'Grant Fund,,grant,idea,,,,US,,1,0,\n'
                    ',NoName,investor,,,,,,,,,\n'
                    'Bad Kind,X,hedgefund,,,,,,,,,\n'
                    'Bad URL,X,investor,,,,,,javascript:alert(1),,,\n'
                    'Bad Range,X,investor,,500k,100k,,,,,,\n')
        login(A_ID)
        r = client.post('/api/admin/founder/investors/import',
                        data={'file': (io.BytesIO(csv_text.encode('utf-8')), 'inv.csv')},
                        content_type='multipart/form-data')
        body = r.get_json() or {}
        check('the import adds the valid rows', r.status_code == 200 and body.get('created') == 2, body)
        check('... and reports each bad row by number', sorted(e['row'] for e in body.get('errors', []))
              == [4, 5, 6, 7], body.get('errors'))
        ada = InvestorProfile.query.filter_by(name='Ada Ventures').first()
        check('lists, amounts and flags are parsed', ada is not None and ada.stages == ['pre-seed', 'seed']
              and ada.check_min == 100000 and ada.check_max == 1500000 and ada.sectors == ['fintech', 'health']
              and ada.geographies == ['UK', 'EU'] and ada.accepts_applications and not ada.auto_submit_allowed
              and ada.source == 'admin' and ada.added_by == A_ID)
        check('auto-submit stays off unless the CSV says so',
              InvestorProfile.query.filter_by(name='Grant Fund').first().auto_submit_allowed is False)
        code, body = api('post', '/api/admin/founder/investors/import',
                         {'csv': 'name,firm,notes\nada ventures,ADA,Updated note\n'})
        check('a matching name + firm updates instead of duplicating', code == 200 and body.get('updated') == 1
              and body.get('created') == 0 and InvestorProfile.query.filter_by(source='admin').count() == 2, body)
        db.session.expire_all()
        check('... with the new values', db.session.get(InvestorProfile, ada.id).notes == 'Updated note')
        code, body = api('post', '/api/admin/founder/investors/import', {'csv': 'name,favourite_colour\nA,blue\n'})
        check('unknown columns are refused', code == 400 and 'favourite_colour' in body.get('message', ''))
        code, _ = api('post', '/api/admin/founder/investors/import', {'csv': 'firm\nX\n'})
        check('a file without a name column is refused', code == 400)
        code, _ = api('post', '/api/admin/founder/investors/import', {'csv': ''})
        check('an empty file is refused', code == 400)
        check('imports are audit-logged', audits('founder_investor_import') == 2)
        login(A_ID)
        r = client.post('/api/admin/founder/investors/import',
                        data={'file': (io.BytesIO(b'name\nEvil Capital\n'), 'x.csv')},
                        content_type='multipart/form-data', headers={'Origin': 'https://evil.example'})
        check('a cross-site upload is refused', r.status_code == 403
              and InvestorProfile.query.filter_by(name='Evil Capital').first() is None, r.status_code)
        code_x = client.put('/api/admin/founder/settings', json={'settings': {'fnd_next_alternates': 4}},
                            headers={'Referer': 'https://evil.example/page'}).status_code
        check('a cross-site settings change is refused', code_x == 403 and founder_config.get('fnd_next_alternates')
              != 4, code_x)
        r = client.post('/api/admin/founder/investors/import', json={'csv': 'name\nSame Origin Fund\n'},
                        headers={'Origin': 'http://localhost'})
        check('a same-origin call still works', r.status_code == 200, r.status_code)
        InvestorProfile.query.filter_by(name='Same Origin Fund').delete()
        db.session.commit()
        code, body = api('get', '/api/admin/founder/investors')
        check('the list returns the directory', code == 200 and len(body.get('investors', [])) == 2)
        html = (login(A_ID), client.get('/admin/founder?tab=investors').get_data(as_text=True))[1]
        check('the Investors tab lists them', 'ada ventures' in html.lower() and 'Grant Fund' in html)
        fnd_row = InvestorProfile(name='Founder Pick', kind='investor', source='founder', added_by=F_ID)
        db.session.add(fnd_row)
        db.session.commit()
        code, _ = api('delete', '/api/admin/founder/investors/%s' % fnd_row.id)
        check('a founder-added investor is not deleted here', code == 400
              and db.session.get(InvestorProfile, fnd_row.id) is not None)
        tgt = InvestorTarget(venture_id='VENTUREX1', investor_profile_id=ada.id, name='Ada Ventures', kind='investor')
        db.session.add(tgt)
        db.session.commit()
        code, _ = api('delete', '/api/admin/founder/investors/%s' % ada.id)
        db.session.expire_all()
        check('an admin row is deleted', code == 200 and db.session.get(InvestorProfile, ada.id) is None)
        check('... founders\' targets keep their copy, unlinked',
              db.session.get(InvestorTarget, tgt.id).investor_profile_id is None)
        check('the delete is audit-logged', audits('founder_investor_delete') == 1)
        code, _ = api('delete', '/api/admin/founder/investors/NOPE00000')
        check('deleting an unknown id is 404', code == 404)

        print('\n6b. Name checks')
        from app.models.founder import StartupPage
        sp = StartupPage(venture_id='VENTUREX2', slug='applle-robotics', name='Applle Robotics')
        db.session.add(sp)
        db.session.commit()
        html = (login(A_ID), client.get('/admin/founder?tab=names').get_data(as_text=True))[1]
        check('the Name checks tab lists pending names', 'Applle Robotics' in html and '/startup/applle-robotics' in html)
        code, _ = api('post', '/api/admin/founder/name-checks/%s' % sp.id, {'state': 'approved'})
        check('an unknown name-check state is refused', code == 400)
        code, _ = api('post', '/api/admin/founder/name-checks/%s' % sp.id, {'state': 'held'}, uid=F_ID)
        check('a member cannot change a name check', code == 403)
        code, body = api('post', '/api/admin/founder/name-checks/%s' % sp.id, {'state': 'held'})
        check('an admin holds a name', code == 200 and db.session.get(StartupPage, sp.id).name_check == 'held', body)
        code, body = api('post', '/api/admin/founder/name-checks/%s' % sp.id, {'state': 'clear'})
        db.session.expire_all()
        check('... and clears it', code == 200 and db.session.get(StartupPage, sp.id).name_check == 'clear')
        check('name checks are audit-logged', audits('founder_name_check') == 2)
        html = (login(A_ID), client.get('/admin/founder?tab=names').get_data(as_text=True))[1]
        check('a cleared name leaves the list', 'Applle Robotics' not in html)
        code, _ = api('post', '/api/admin/founder/name-checks/NOPE00000', {'state': 'clear'})
        check('an unknown page is 404', code == 404)

        print('\n7. Checkout page and session')
        put({'fnd_price_cents': 49900, 'fnd_addon_price_cents': 29900, 'fnd_refund_window_days': 14})
        v = founder_ventures.create_venture(db.session.get(User, F_ID), 'Acme Robotics', 'Robots for docks', 'seed')
        V_ID = v.id
        login(F_ID)
        r = client.get('/founder/v/%s/checkout' % V_ID)
        html = r.get_data(as_text=True)
        check('the owner sees the checkout page', r.status_code == 200, r.status_code)
        check('... with the standalone price and refund window', '$499' in html and '14 days' in html)
        check('... what unlocks, and the report disclaimer', 'What unlocks' in html and 'full readiness report' in html)
        from app.services import legal_text
        check('... and the legal text', legal_text.get('founder_report_disclaimer')[:40] in html)
        check('a non-member gets 403', (login(M_ID), client.get('/founder/v/%s/checkout' % V_ID))[1].status_code == 403)
        check('a member not on the allowlist gets 404',
              (login(O_ID), client.get('/founder/v/%s/checkout' % V_ID))[1].status_code == 404)
        check('an unknown venture is 404', (login(F_ID), client.get('/founder/v/NOPE00000/checkout'))[1].status_code
              == 404)

        login(F_ID)
        r = client.post('/founder/v/%s/checkout' % V_ID)
        check('POST redirects to Stripe Checkout', r.status_code == 303
              and r.headers.get('Location', '').startswith('https://checkout.stripe.test/'), r.status_code)
        kw = created[-1] if created else {}
        li = (kw.get('line_items') or [{}])[0].get('price_data', {})
        check('the session charges the configured price, one time', li.get('unit_amount') == 49900
              and kw.get('mode') == 'payment' and li.get('currency') == 'usd', li)
        check('the metadata names product, venture and user', kw.get('metadata', {}).get('product') == 'founder_track'
              and kw['metadata'].get('venture_id') == V_ID and kw['metadata'].get('user_id') == F_ID, kw.get('metadata'))
        check('success returns to the verify route with the session id',
              '/founder/v/%s/checkout/success?session_id={CHECKOUT_SESSION_ID}' % V_ID in kw.get('success_url', ''))
        check('starting checkout is audit-logged', audits('founder_checkout_started') == 1)
        n_created = len(created)
        retrieved['cs_test_%d' % n_created] = FakeSession(
            id='cs_test_%d' % n_created, status='open', payment_status='unpaid', amount_total=49900,
            url='https://checkout.stripe.test/cs_test_%d' % n_created,
            metadata={'product': 'founder_track', 'venture_id': V_ID, 'user_id': F_ID})
        login(F_ID)
        r = client.post('/founder/v/%s/checkout' % V_ID)
        check('a second POST reuses the open session instead of creating another', r.status_code == 303
              and r.headers.get('Location') == 'https://checkout.stripe.test/cs_test_%d' % n_created
              and len(created) == n_created, (r.status_code, len(created)))
        check('the venture is not paid yet', not founder_ventures.is_paid(db.session.get(FounderVenture, V_ID)))
        put({'fnd_price_cents': 45000})
        login(F_ID)
        client.post('/founder/v/%s/checkout' % V_ID)
        check('a new admin price is used at once', created[-1]['line_items'][0]['price_data']['unit_amount'] == 45000)
        put({'fnd_price_cents': 49900})

        print('\n8. Add-on price only when eligible')
        check('a member with nothing is not eligible', not stripe_service.founder_addon_eligible(F_ID))
        try:
            stripe_service.create_founder_checkout(db.session.get(FounderVenture, V_ID), db.session.get(User, F_ID),
                                                   'founder_addon')
            refused = False
        except ValueError:
            refused = True
        check('create_founder_checkout refuses the add-on for an ineligible user', refused)
        try:
            stripe_service.create_founder_checkout(db.session.get(FounderVenture, V_ID), db.session.get(User, F_ID), 'gift')
            refused = False
        except ValueError:
            refused = True
        check('... and an unknown product', refused)
        db.session.add(Simulation(id=generate_id(), user_id=M_ID, name='Ops path', status='pending'))
        db.session.commit()
        check('a pending simulation does not count', not stripe_service.founder_addon_eligible(M_ID))
        db.session.add(Simulation(id=generate_id(), user_id=M_ID, name='Ops path 2', status='complete'))
        db.session.commit()
        check('a completed simulation makes a member eligible', stripe_service.founder_addon_eligible(M_ID))
        from app.models.bio_page import BioPage
        db.session.add(BioPage(id=generate_id(), user_id=O_ID, slug='other-person', status='published'))
        db.session.commit()
        check('a published bio page makes a member eligible', stripe_service.founder_addon_eligible(O_ID))
        mv = founder_ventures.create_venture(db.session.get(User, M_ID), 'Dockside', '', 'idea')
        MV_ID = mv.id
        login(M_ID)
        html = client.get('/founder/v/%s/checkout' % MV_ID).get_data(as_text=True)
        check('an eligible member sees the add-on price', '$299' in html and 'Member price' in html
              and 'usually $499)' in html)
        put({'fnd_price_cents': 49950})
        html = (login(M_ID), client.get('/founder/v/%s/checkout' % MV_ID).get_data(as_text=True))[1]
        check('... and the full price is shown to the cent', 'usually $499.50)' in html)
        put({'fnd_price_cents': 49900})
        login(M_ID)
        r = client.post('/founder/v/%s/checkout' % MV_ID)
        kw = created[-1]
        check('... and is charged it as founder_addon', r.status_code == 303
              and kw['line_items'][0]['price_data']['unit_amount'] == 29900
              and kw['metadata']['product'] == 'founder_addon', kw.get('metadata'))

        print('\n9. Webhook marks the venture paid, once')

        def hook(session_obj, etype='checkout.session.completed'):
            next_event['e'] = {'type': etype, 'data': {'object': session_obj}}
            login(None)
            return client.post('/api/payments/webhook', data=b'{}', headers={'Stripe-Signature': 'sig'})

        base = {'id': 'cs_hook_1', 'payment_status': 'paid', 'amount_total': 29900, 'payment_intent': 'pi_123',
                'metadata': {'product': 'founder_addon', 'venture_id': MV_ID, 'user_id': O_ID,
                             'amount_cents': '29900'}}
        r = hook(base)
        db.session.expire_all()
        check('a session whose payer is not the owner does not mark paid', r.status_code == 200
              and db.session.get(FounderVenture, MV_ID).paid_at is None)
        unpaid = dict(base, payment_status='unpaid', metadata=dict(base['metadata'], user_id=M_ID))
        hook(unpaid)
        db.session.expire_all()
        check('an unpaid session does not mark paid', db.session.get(FounderVenture, MV_ID).paid_at is None)
        good = dict(base, metadata=dict(base['metadata'], user_id=M_ID))
        r = hook(good)
        db.session.expire_all()
        mv = db.session.get(FounderVenture, MV_ID)
        check('a paid session marks the venture paid', r.status_code == 200 and mv.paid_at is not None, r.status_code)
        check('... with the payment intent, amount and add-on track', mv.payment_intent_id == 'pi_123'
              and mv.amount_cents == 29900 and mv.track == 'addon')
        first_paid = mv.paid_at
        r = hook(dict(good, payment_intent='pi_other', amount_total=1))
        db.session.expire_all()
        mv = db.session.get(FounderVenture, MV_ID)
        check('a replayed webhook changes nothing', r.status_code == 200 and mv.paid_at == first_paid
              and mv.payment_intent_id == 'pi_123' and mv.amount_cents == 29900)
        check('the payment is audit-logged once', AuditLog.query.filter_by(action='founder_paid',
                                                                           resource_id=MV_ID).count() == 1)
        dup = AuditLog.query.filter_by(action='founder_duplicate_payment', resource_id=MV_ID).all()
        check('a second paid session with another payment intent is flagged for refund', len(dup) == 1
              and dup[0].extra.get('payment_intent') == 'pi_other'
              and dup[0].extra.get('kept_payment_intent') == 'pi_123', [d.extra for d in dup])
        hook(dict(good, payment_intent='pi_other', amount_total=1))
        hook(good)
        check('... once, and a replay of the original is not a duplicate',
              AuditLog.query.filter_by(action='founder_duplicate_payment', resource_id=MV_ID).count() == 1)
        login(M_ID)
        r = client.get('/founder/v/%s/checkout' % MV_ID)
        check('a paid owner is sent from checkout to the report', r.status_code == 302
              and r.headers.get('Location', '').endswith('/founder/v/%s/report' % MV_ID), r.headers.get('Location'))
        r = client.post('/founder/v/%s/checkout' % MV_ID)
        check('... and cannot start a second checkout', r.status_code == 302 and len(created) == 3, len(created))

        print('\n10. Success return verifies the session (webhook fallback)')
        login(F_ID)
        r = client.get('/founder/v/%s/checkout/success?session_id=cs_missing' % V_ID)
        check('an unknown session shows the confirming notice, not paid', r.status_code == 200
              and 'confirming your payment' in r.get_data(as_text=True)
              and db.session.get(FounderVenture, V_ID).paid_at is None)
        retrieved['cs_wrong'] = FakeSession(id='cs_wrong', payment_status='paid', amount_total=49900,
                                            payment_intent='pi_w', metadata={'product': 'founder_track',
                                                                             'venture_id': MV_ID, 'user_id': M_ID})
        client.get('/founder/v/%s/checkout/success?session_id=cs_wrong' % V_ID)
        db.session.expire_all()
        check('a session for another venture is ignored', db.session.get(FounderVenture, V_ID).paid_at is None)
        retrieved['cs_ok'] = FakeSession(id='cs_ok', payment_status='paid', amount_total=49900,
                                         payment_intent={'id': 'pi_ok'},
                                         metadata={'product': 'founder_track', 'venture_id': V_ID, 'user_id': F_ID})
        r = client.get('/founder/v/%s/checkout/success?session_id=cs_ok' % V_ID)
        db.session.expire_all()
        v = db.session.get(FounderVenture, V_ID)
        check('a verified session marks paid and redirects to the report', r.status_code == 302
              and v.paid_at is not None and v.payment_intent_id == 'pi_ok' and v.amount_cents == 49900
              and v.track == 'standalone', (r.status_code, v.paid_at))
        hook(dict(retrieved['cs_ok']))
        check('the late webhook for the same session is a no-op',
              AuditLog.query.filter_by(action='founder_paid', resource_id=V_ID).count() == 1)
        check('an admin overview counts paid ventures and revenue',
              '$798.00' in (login(A_ID), client.get('/admin/founder').get_data(as_text=True))[1])

        print('\n10b. A full refund locks the venture again')
        r = hook({'id': 'ch_1', 'payment_intent': 'pi_123', 'refunded': False, 'amount_refunded': 5000},
                 etype='charge.refunded')
        db.session.expire_all()
        check('a partial refund keeps access', r.status_code == 200
              and db.session.get(FounderVenture, MV_ID).paid_at is not None
              and audits('founder_partial_refund') == 1)
        r = hook({'id': 'ch_1', 'payment_intent': 'pi_123', 'refunded': True, 'amount_refunded': 29900},
                 etype='charge.refunded')
        db.session.expire_all()
        mv = db.session.get(FounderVenture, MV_ID)
        check('a full refund clears paid_at', r.status_code == 200 and mv.paid_at is None
              and not founder_ventures.is_paid(mv) and mv.payment_intent_id == 'pi_123')
        check('... and is audit-logged', audits('founder_refunded') == 1)
        hook({'id': 'ch_1', 'payment_intent': 'pi_123', 'refunded': True}, etype='charge.refunded')
        check('a replayed refund changes nothing', audits('founder_refunded') == 1)
        hook({'id': 'ch_2', 'payment_intent': 'pi_other', 'refunded': True}, etype='charge.refunded')
        db.session.expire_all()
        check('refunding the duplicate payment leaves the other venture paid',
              db.session.get(FounderVenture, V_ID).paid_at is not None)
        html = (login(A_ID), client.get('/admin/founder').get_data(as_text=True))[1]
        check('the overview no longer counts the refunded payment', '$499.00' in html and '$798.00' not in html)
        login(M_ID)
        r = client.get('/founder/v/%s/checkout' % MV_ID)
        check('the refunded owner sees checkout again', r.status_code == 200)


        print('\n11. Existing checkout branches are unaffected')
        sim = Simulation(id=generate_id(), user_id=O_ID, name='Tiered', status='complete')
        db.session.add(sim)
        db.session.commit()
        r = hook({'id': 'cs_v', 'payment_status': 'paid', 'metadata': {'product': 'voice_training', 'user_id': O_ID}})
        db.session.expire_all()
        check('voice_training still unlocks voice', r.status_code == 200
              and db.session.get(User, O_ID).voice_training_paid_at is not None)
        r = hook({'id': 'cs_p', 'payment_status': 'paid', 'metadata': {
            'product': 'prospect_tier_upgrade', 'simulation_id': sim.id, 'upgrade_to_tier': '2', 'delta_cents': '1500'}})
        db.session.expire_all()
        s2 = db.session.get(Simulation, sim.id)
        check('prospect_tier_upgrade still upgrades', r.status_code == 200 and s2.prospect_tier == 2
              and s2.prospect_tier_paid_cents == 1500)
        paid_before = FounderVenture.query.filter(FounderVenture.paid_at.isnot(None)).count()
        r = hook({'id': 'cs_u', 'payment_status': 'paid', 'metadata': {'product': 'mystery', 'venture_id': V_ID}})
        check('an unknown product is still ignored with 200', r.status_code == 200
              and FounderVenture.query.filter(FounderVenture.paid_at.isnot(None)).count() == paid_before)
        r = hook({'id': 'pi_x', 'metadata': {}}, etype='payment_intent.succeeded')
        check('other event types still return 200', r.status_code == 200)

        print('\n12. Track off: founder billing routes 404, admin page stays')
        api('post', '/api/admin/founder/mode', {'key': 'founder_track', 'mode': 'off'})
        login(F_ID)
        check('checkout 404s with the track off', client.get('/founder/v/%s/checkout' % V_ID).status_code == 404)
        check('success 404s with the track off',
              client.get('/founder/v/%s/checkout/success' % V_ID).status_code == 404)
        login(A_ID)
        check('the admin page still renders', client.get('/admin/founder').status_code == 200)

    print('\n%d passed, %d failed' % (len(PASS), len(FAIL)))
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
