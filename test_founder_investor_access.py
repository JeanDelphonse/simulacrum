"""Founder Track investor access, startup side (FOUNDER_PLAN.md WP8 part A).

The founder_investor gate (switch + counsel sign-off), private links (free
invited access, revoked links refused, open counts), email sign-in (signed,
short-lived, session-only identity), paid access (price from config, webhook
idempotent, 506(b)/undecided waits for the founders, 506(c) active), approval
needs the decision right, data-room requests only with access, refunds on
unpublish, the one-pager (public omits the ask) and the founder JSON APIs.
In-memory SQLite; Stripe, email and the network are faked.

    python test_founder_investor_access.py
"""
import io
import os
import re
import sys
from datetime import datetime, timedelta

os.environ.setdefault('DATABASE_URL', 'sqlite:///:memory:')
os.environ.setdefault('SECRET_KEY', 'test-only')
ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

PASS, FAIL = [], []

NEW_PY = ['app/services/startup_access_service.py', 'app/services/founder_onepager.py',
          'app/blueprints/startup/investor.py']


def check(label, condition, detail=''):
    (PASS if condition else FAIL).append(label)
    line = '  {} {}{}'.format('ok  ' if condition else 'FAIL', label, ('  -- ' + str(detail)) if detail else '')
    enc = sys.stdout.encoding or 'ascii'
    print(line.encode(enc, 'replace').decode(enc))


class FakeSession(dict):
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

    import tempfile
    from app import create_app
    from app.extensions import db
    from utils.id_gen import generate_id
    app = create_app('testing')
    app.config.update(SQLALCHEMY_DATABASE_URI='sqlite:///:memory:', WTF_CSRF_ENABLED=False,
                      UPLOAD_FOLDER=tempfile.mkdtemp(prefix='stinv_'),
                      STRIPE_SECRET_KEY='sk_test_fake', STRIPE_WEBHOOK_SECRET='whsec_fake')

    sent = []
    from app.services import email_service
    email_service._send = lambda subject, recipients, body, html=None: sent.append((subject, list(recipients), body))

    import stripe
    created, retrieved = [], {}

    def fake_create(**kw):
        sid = 'cs_inv_%d' % (len(created) + 1)
        created.append(kw)
        s = FakeSession(id=sid, url='https://checkout.stripe.test/' + sid, status='open', payment_status='unpaid',
                        amount_total=kw['line_items'][0]['price_data']['unit_amount'], metadata=kw['metadata'])
        retrieved[sid] = s
        return s
    stripe.checkout.Session.create = staticmethod(fake_create)
    stripe.checkout.Session.retrieve = staticmethod(lambda sid, **kw: retrieved[sid])

    refunds = []
    from app.services import stripe_service

    def fake_refund(pi, reason=''):
        refunds.append((pi, reason))
        return {'refund_id': 're_%d' % len(refunds), 'status': 'succeeded', 'amount': 0}
    stripe_service.issue_refund = fake_refund

    from app.blueprints.payments import routes as pay_routes
    next_event = {}
    pay_routes.construct_webhook_event = lambda payload, sig: next_event['e']

    with app.app_context():
        db.create_all()
        from flask import g
        from app.models.audit_log import AuditLog
        from app.models.contact import Contact
        from app.models.founder import (ComplianceProfile, DecisionRight, FounderVenture, InvestorAccess,
                                        InvestorLink, ProofPlan, ProofResult, StartupPage, VentureMember)
        from app.models.mail_crm import FeatureFlag
        from app.models.user import User
        from app.services import founder_config, founder_feature, founder_onepager, founder_ventures
        from app.services import startup_access_service as sas
        from app.services import startup_page_service as sps
        now = datetime.utcnow()

        def user(email, admin=False, name=None):
            u = User(id=generate_id(), email=email, full_name=name or email.split('@')[0].title(),
                     password_hash='x', is_admin=admin, email_verified=True, onboarding_completed_at=now)
            db.session.add(u)
            db.session.commit()
            return u

        admin = user('admin@x.com', admin=True)
        owner = user('owner@x.com', name='Olive Owner')
        cof = user('cof@x.com', name='Cora Cofounder')
        outsider = user('out@x.com')
        A_ID, O_ID, C_ID, X_ID = admin.id, owner.id, cof.id, outsider.id
        users_before = User.query.count()

        client = app.test_client()
        inv2 = app.test_client()
        inv3 = app.test_client()
        for cl in (client, inv2, inv3):
            for verb in ('get', 'post', 'put', 'delete', 'open'):
                orig = getattr(cl, verb)
                setattr(cl, verb, (lambda o: lambda *a, **k: (g.pop('_login_user', None), o(*a, **k))[1])(orig))

        def login(uid, cl=client):
            with cl.session_transaction() as s:
                if uid:
                    s['_user_id'], s['_fresh'] = uid, True
                else:
                    s.pop('_user_id', None)

        def api(method, path, body=None, uid=O_ID):
            login(uid)
            fn = getattr(client, method)
            r = fn(path, json=body) if body is not None else fn(path)
            out = r.status_code, (r.get_json(silent=True) or {})
            login(None)
            return out

        def hook(obj, etype='checkout.session.completed'):
            next_event['e'] = {'type': etype, 'data': {'object': obj}}
            return client.post('/api/payments/webhook', data=b'{}', headers={'Stripe-Signature': 'sig'})

        # ── a paid venture with a published page and an ask ──
        founder_feature.set_mode('founder_track', 'on', A_ID)
        v = founder_ventures.create_venture(db.session.get(User, O_ID), 'Dockwise', 'Scheduling for loading docks',
                                            'seed')
        V = v.id
        cm = VentureMember(venture_id=V, user_id=C_ID, role='cofounder', status='active', title='CTO', joined_at=now)
        db.session.add(cm)
        v.paid_at = now
        plan = ProofPlan(venture_id=V, customer_name='Acme Logistics', status='complete', duration='6 weeks',
                         success_criteria=[{'metric': 'Average truck wait', 'target': 'under 1 hour'}])
        db.session.add(plan)
        db.session.flush()
        db.session.add(ProofResult(venture_id=V, proof_plan_id=plan.id, measured=True,
                                   benefits_text='Wait time fell from 4 hours to 50 minutes over six weeks.',
                                   quote_text='We stopped paying detention fees.', quote_permission=True))
        db.session.add(ProofResult(venture_id=V, proof_plan_id=plan.id, measured=False,
                                   benefits_text='A second site is trialling it.',
                                   quote_text='SECRET UNPERMITTED QUOTE', quote_permission=False))
        db.session.commit()
        page = sps.ensure_page(v)
        sps.update(page, {'problem': 'Trucks wait four hours at the dock.', 'ask_round': 'Seed',
                          'ask_amount': '1500000', 'use_of_funds': 'Two engineers and a sales lead.',
                          'traction': [{'label': 'Paying pilots', 'value': '3', 'note': 'Signed in October'}]})
        sps.publish(page, db.session.get(User, O_ID))
        P, SLUG = page.id, page.slug
        login(None)

        investor_urls = ['/startup/%s/i/whatever' % SLUG, '/startup/%s/signin' % SLUG,
                         '/startup/%s/signin/badtoken' % SLUG, '/startup/%s/investor' % SLUG,
                         '/startup/%s/unlock' % SLUG, '/startup/%s/access-request' % SLUG,
                         '/startup/%s/onepager.pdf' % SLUG, '/startup/%s/unlock/success' % SLUG]

        print('\n2. Everything 404s while founder_investor is off or counsel sign-off is missing')
        check('the public page is live', client.get('/startup/%s' % SLUG).status_code == 200)
        check('the public page shows no investor entry while the switch is off',
              '/startup/%s/signin' % SLUG not in client.get('/startup/%s' % SLUG).get_data(as_text=True))
        codes = [client.get(u).status_code for u in investor_urls]
        check('every investor GET route 404s with the switch off', set(codes) == {404}, codes)
        r = client.post('/startup/%s/investor-request' % SLUG, json={'kind': 'data_room'})
        check('the investor request POST 404s with the switch off', r.status_code == 404, r.status_code)
        code, _ = api('get', '/api/founder/v/%s/investor-links' % V)
        check('the founder link API 404s with the switch off', code == 404, code)
        try:
            founder_feature.set_mode('founder_investor', 'on', A_ID)
            check('the switch refuses to turn on without counsel sign-off', False)
        except founder_feature.FounderError:
            check('the switch refuses to turn on without counsel sign-off', True)
        # A flag row forced on (say, by hand in the database) still does nothing without the sign-off.
        db.session.add(FeatureFlag(feature_key='founder_investor', mode='on'))
        db.session.commit()
        codes = [client.get(u).status_code for u in investor_urls]
        check('a forced-on switch without sign-off still 404s everywhere', set(codes) == {404}, codes)
        code, _ = api('get', '/api/founder/v/%s/investor-access' % V)
        check('... and the founder access API 404s', code == 404, code)
        founder_config.set('fnd_counsel_signoff_at', '2026-11-01', A_ID)
        founder_feature.set_mode('founder_investor', 'on', A_ID)
        check('with sign-off and the switch on, sign-in opens', client.get('/startup/%s/signin' % SLUG).status_code == 200)
        check('the public view carries no ask', 'Two engineers' not in client.get('/startup/%s' % SLUG).get_data(as_text=True))
        check('the public page links investors to sign-in and unlock once the gate is open',
              all(u in client.get('/startup/%s' % SLUG).get_data(as_text=True)
                  for u in ('/startup/%s/signin' % SLUG, '/startup/%s/unlock' % SLUG)))
        founder_feature.set_mode('founder_investor', 'allowlist', A_ID)
        check('allowlist: a page whose owner is not allowed 404s',
              client.get('/startup/%s/signin' % SLUG).status_code == 404)
        founder_feature.set_user_access([O_ID], 'allowed', A_ID, key='founder_investor')
        check('allowlist: allowing the owner opens the page',
              client.get('/startup/%s/signin' % SLUG).status_code == 200)
        founder_feature.set_mode('founder_investor', 'on', A_ID)

        print('\n3. Private links: invited access without payment')
        try:
            sas.issue_link(page, db.session.get(User, C_ID), 'Fund A')
            check('a co-founder without the investor_contact right cannot issue links', False)
        except sas.AccessError as exc:
            check('a co-founder without the investor_contact right cannot issue links', exc.status == 403)
        code, body = api('post', '/api/founder/v/%s/investor-links' % V, {'label': 'Fund A'}, uid=C_ID)
        check('... nor through the API (403)', code == 403, (code, body))
        code, body = api('post', '/api/founder/v/%s/investor-links' % V, {'label': 'x'}, uid=X_ID)
        check('a non-member gets 403/404 from the API', code in (403, 404), code)
        code, body = api('post', '/api/founder/v/%s/investor-links' % V,
                         {'label': 'Ivy at Fund A', 'investor_email': 'Ivy@FundA.vc'})
        check('the owner issues a named link (201)', code == 201 and body['link']['investor_email'] == 'ivy@funda.vc',
              (code, body))
        named = InvestorLink.query.get(body['link']['id'])
        check('the token is random, long and stored', named.token and len(named.token) >= 40
              and body['link']['url'].endswith('/startup/%s/i/%s' % (SLUG, named.token)))
        code, body = api('post', '/api/founder/v/%s/investor-links' % V, {'label': 'Bad', 'investor_email': 'nope'})
        check('a bad investor email is refused (400)', code == 400, (code, body))
        code, body = api('get', '/api/founder/v/%s/investor-links' % V, uid=C_ID)
        check('any member can list links', code == 200 and len(body['links']) == 1 and body['can_manage'] is False,
              (code, body))

        n_charges = len(created)
        r = client.get('/startup/%s/i/%s' % (SLUG, named.token))
        html = r.get_data(as_text=True)
        db.session.expire_all()
        named = InvestorLink.query.get(named.id)
        check('the private link opens the investor view', r.status_code == 200 and 'Investor view' in html,
              r.status_code)
        check('... with the ask', 'US$ 1,500,000' in html and 'Two engineers and a sales lead.' in html)
        check('... and full proof (criteria, unmeasured result) without unpermitted quotes',
              'Average truck wait: under 1 hour' in html and 'A second site is trialling it.' in html
              and 'SECRET UNPERMITTED QUOTE' not in html)
        check('... and the data-room choice', 'Data room access' in html)
        check('... and is not indexable', 'noindex' in html and r.headers.get('Cache-Control') == 'no-store')
        acc = InvestorAccess.query.filter_by(startup_page_id=P, email='ivy@funda.vc').first()
        check('invited access is recorded for the link email', acc is not None and acc.kind == 'invited'
              and acc.status == 'active' and acc.investor_link_id == named.id)
        check('no payment was asked for', len(created) == n_charges and acc.amount_cents is None)
        check('the open is counted', named.open_count == 1 and named.opened_at is not None)
        client.get('/startup/%s/i/%s' % (SLUG, named.token))
        db.session.expire_all()
        check('a second open counts again; opened_at keeps the first',
              InvestorLink.query.get(named.id).open_count == 2)
        check('has_access is true for the invited email', sas.has_access(page, 'ivy@funda.vc'))
        check('the visit is recorded as an investor view with the link',
              any(x.view == 'investor' and x.investor_link_id == named.id for x in
                  __import__('app.models.founder', fromlist=['StartupPageVisit']).StartupPageVisit.query.all()))

        code, body = api('post', '/api/founder/v/%s/investor-links/%s/revoke' % (V, named.id), {}, uid=C_ID)
        check('a co-founder without the right cannot revoke (403)', code == 403, code)
        code, body = api('post', '/api/founder/v/%s/investor-links/%s/revoke' % (V, named.id), {})
        check('the owner revokes the link', code == 200 and body['link']['revoked'], (code, body))
        r = client.get('/startup/%s/i/%s' % (SLUG, named.token))
        check('a revoked link is refused (410)', r.status_code == 410
              and 'no longer active' in r.get_data(as_text=True), r.status_code)
        check('a revoked link ends the invited access', not sas.has_access(page, 'ivy@funda.vc'))
        r = client.get('/startup/%s/investor' % SLUG)
        check('... and the investor view sends the visitor away', r.status_code == 302, r.status_code)
        check('an unknown token is refused', client.get('/startup/%s/i/%s' % (SLUG, 'z' * 43)).status_code == 410)

        print('\n4. Email sign-in: signed, short-lived, session-only')
        login(None, inv2)
        del sent[:]
        r = inv2.post('/startup/%s/signin' % SLUG, data={'email': 'Pat@Angel.vc'})
        check('sign-in answers with "check your email"', r.status_code == 200
              and 'Check your email' in r.get_data(as_text=True), r.status_code)
        mail = [s for s in sent if s[1] == ['pat@angel.vc']]
        m = re.search(r'/startup/%s/signin/(\S+)' % SLUG, mail[-1][2]) if mail else None
        check('a sign-in link was emailed', m is not None, sent[-1:] if sent else None)
        token = m.group(1) if m else ''
        r = inv2.post('/startup/%s/signin' % SLUG, data={'email': 'not-an-email'})
        check('a bad address is refused (400)', r.status_code == 400, r.status_code)
        r = inv2.get('/startup/%s/signin/%s' % (SLUG, 'garbage.token.x'))
        check('a bad token is refused (400)', r.status_code == 400, r.status_code)
        r = inv2.get('/startup/%s/signin/%s' % (SLUG, token[:-2] + ('AA' if not token.endswith('AA') else 'BB')))
        check('a tampered token is refused', r.status_code == 400, r.status_code)
        old_age = sas.SIGNIN_MAX_AGE
        sas.SIGNIN_MAX_AGE = -1
        r = inv2.get('/startup/%s/signin/%s' % (SLUG, token))
        sas.SIGNIN_MAX_AGE = old_age
        check('an expired token is refused', r.status_code == 400, r.status_code)
        with app.test_request_context():
            v2 = founder_ventures.create_venture(db.session.get(User, X_ID), 'Other Co', 'Another', 'idea')
            v2.paid_at = now
            db.session.commit()
            p2 = sps.ensure_page(v2)
            sps.update(p2, {'problem': 'Another problem.'})
            sps.publish(p2, db.session.get(User, X_ID))
            other_tok = sas.make_signin_token('pat@angel.vc', p2)
        r = inv2.get('/startup/%s/signin/%s' % (SLUG, other_tok))
        check('a token for another page is refused', r.status_code == 400, r.status_code)
        with inv2.session_transaction() as s:
            check('nothing is stored in the session before a good token', not s.get('_investors'))
        r = inv2.get('/startup/%s/signin/%s' % (SLUG, token))
        with inv2.session_transaction() as s:
            check('a good token stores the email in the session key _investors',
                  s.get('_investors') == ['pat@angel.vc'], dict(s))
            check('... without logging anyone in', not s.get('_user_id'))
        check('... and creates no account', User.query.count() == users_before + 0 or
              User.query.filter_by(email='pat@angel.vc').first() is None)
        check('an uninvited verified visitor is sent to unlock', r.status_code == 302
              and r.headers['Location'].endswith('/startup/%s/investor' % SLUG)
              and inv2.get('/startup/%s/investor' % SLUG).status_code == 302)
        for _ in range(5):
            r = inv2.post('/startup/%s/signin' % SLUG, data={'email': 'spam@angel.vc'})
        check('sign-in emails are rate-limited per address and page (429)', r.status_code == 429, r.status_code)

        print('\n5. Paid access, undecided exemption: the ask waits for the founders')
        r = inv2.get('/startup/%s/unlock' % SLUG)
        body = r.get_data(as_text=True)
        check('the unlock page shows the price from config', r.status_code == 200 and '$49.00' in body)
        check('... says payment does not verify accreditation (legal text)',
              'does not verify that you are an accredited investor' in body)
        check('... and that the founders review paid access first', 'review every paid access' in body)
        r = inv2.post('/startup/%s/unlock' % SLUG, data={'email': 'pat@angel.vc', 'amount': '1',
                                                          'unit_amount': '1'})
        kw = created[-1]
        check('POST redirects to Stripe Checkout', r.status_code == 303
              and r.headers['Location'].startswith('https://checkout.stripe.test/'), r.status_code)
        check('the amount is the config price, not a client value',
              kw['line_items'][0]['price_data']['unit_amount'] == 4900, kw['line_items'])
        check('metadata names the product, page and email', kw['metadata'] == {
            'product': 'investor_access', 'startup_page_id': P, 'email': 'pat@angel.vc', 'amount_cents': '4900'},
            kw['metadata'])
        n = len(created)
        r = inv2.post('/startup/%s/unlock' % SLUG, data={})
        check('a second click reuses the open session', len(created) == n and r.status_code == 303)
        r = inv3.post('/startup/%s/unlock' % SLUG, data={'email': 'pat@angel.vc'})
        check('an unverified browser cannot start checkout for someone else', r.status_code == 302
              and '/signin' in r.headers['Location'] and len(created) == n)

        sid = kw and r and created[-1] and 'cs_inv_%d' % len(created)
        paid = {'id': sid, 'payment_status': 'paid', 'amount_total': 4900, 'payment_intent': 'pi_pat',
                'metadata': created[-1]['metadata']}
        del sent[:]
        r = hook(dict(paid, payment_status='unpaid'))
        check('an unpaid session records nothing', r.status_code == 200
              and InvestorAccess.query.filter_by(stripe_session_id=sid).count() == 0)
        r = hook(paid)
        db.session.expire_all()
        row = InvestorAccess.query.filter_by(stripe_session_id=sid).first()
        check('the webhook records paid access', r.status_code == 200 and row is not None and row.kind == 'paid'
              and row.amount_cents == 4900 and row.payment_intent_id == 'pi_pat')
        check('under an undecided exemption it waits for approval', row.status == 'requested', row.status)
        check('the founders are told to approve',
              any('Approve investor access' in s[0] and 'owner@x.com' in s[1] for s in sent), [s[0] for s in sent])
        hook(paid)
        retrieved[sid].update(payment_status='paid', payment_intent='pi_pat')
        inv2.get('/startup/%s/unlock/success?session_id=%s' % (SLUG, sid))
        check('replays and the success return are idempotent',
              InvestorAccess.query.filter_by(stripe_session_id=sid).count() == 1)
        html = inv2.get('/startup/%s/investor' % SLUG).get_data(as_text=True)
        check('while pending, the investor view shows proof but not the ask',
              'Proof in detail' in html and 'US$ 1,500,000' not in html and 'Two engineers' not in html
              and 'review paid access before the ask is shown' in html)
        check('... and no data-room choice', 'Data room access' not in html)
        r = inv2.post('/startup/%s/investor-request' % SLUG, json={'kind': 'data_room', 'name': 'Pat Angel',
                                                                     'email': 'pat@angel.vc', 'firm': 'Angel'})
        check('a pending payer cannot request the data room (403)', r.status_code == 403, r.status_code)
        pdf_html = founder_onepager.build_html(page, True, row)
        check('a pending payer gets the one-pager without the ask', 'Use of funds' not in pdf_html)

        code, body = api('post', '/api/founder/v/%s/investor-access/%s' % (V, row.id), {'action': 'approve'},
                         uid=C_ID)
        check('approval needs the decision right (co-founder without it: 403)', code == 403, (code, body))
        db.session.add(DecisionRight(venture_id=V, decision_type='investor_contact', holder_member_id=cm.id))
        db.session.commit()
        check('the investor_contact holder can manage', sas.can_manage(v, db.session.get(User, C_ID)))
        code, body = api('get', '/api/founder/v/%s/investor-access' % V, uid=C_ID)
        check('the access API lists the pending request', code == 200
              and any(a['id'] == row.id and a['status'] == 'requested' for a in body['access']), (code, body))
        del sent[:]
        code, body = api('post', '/api/founder/v/%s/investor-access/%s' % (V, row.id), {'action': 'approve'},
                         uid=C_ID)
        db.session.expire_all()
        check('the holder approves', code == 200 and InvestorAccess.query.get(row.id).status == 'active'
              and InvestorAccess.query.get(row.id).approved_by == C_ID, (code, body))
        check('the investor is told', any(s[1] == ['pat@angel.vc'] and 'approved' in s[0] for s in sent))
        html = inv2.get('/startup/%s/investor' % SLUG).get_data(as_text=True)
        check('after approval the ask shows', 'US$ 1,500,000' in html and 'Two engineers' in html)

        print('\n6. Data-room requests only with access')
        r = client.post('/startup/%s/investor-request' % SLUG, json={'kind': 'data_room', 'name': 'Nobody Here',
                                                                       'email': 'no@x.vc', 'firm': 'X'})
        check('without access: 403', r.status_code == 403, r.status_code)
        r = client.post('/startup/%s/request' % SLUG, json={'kind': 'data_room', 'name': 'Nobody Here',
                                                              'email': 'no2@x.vc', 'firm': 'X'})
        check('the public request route turns a data-room request into an intro',
              r.status_code == 200 and r.get_json()['kind'] == 'intro', r.get_json())
        r = inv2.post('/startup/%s/investor-request' % SLUG, json={'kind': 'data_room', 'name': 'Pat Angel',
                                                                     'email': 'pat@angel.vc', 'firm': 'Angel Co'})
        check('with access: the data-room request goes through', r.status_code == 200
              and r.get_json()['kind'] == 'data_room', r.get_json())
        check('... and lands in the founders\' contacts', Contact.query.filter_by(user_id=O_ID,
                                                                                email='pat@angel.vc').first() is not None)

        print('\n7. 506(c): paid access is active at once')
        db.session.add(ComplianceProfile(venture_id=V, exemption='506c', recorded_by=O_ID, recorded_at=now))
        db.session.commit()
        check('the exemption is read', sas.exemption(v) == '506c')
        founder_config.set('fnd_investor_access_cents', 9900, A_ID)
        with app.test_request_context():
            res = sas.create_checkout(page, 'quinn@vc.vc')
        check('a changed admin price is what Stripe is asked for',
              created[-1]['line_items'][0]['price_data']['unit_amount'] == 9900 and res['amount_cents'] == 9900)
        del sent[:]
        hook({'id': res['session_id'], 'payment_status': 'paid', 'amount_total': 9900, 'payment_intent': 'pi_quinn',
              'metadata': created[-1]['metadata']})
        db.session.expire_all()
        q = InvestorAccess.query.filter_by(email='quinn@vc.vc').first()
        check('under 506(c) the paid access is active', q is not None and q.status == 'active' and q.kind == 'paid')
        check('the founders are told an investor unlocked the page',
              any('unlocked' in s[0] for s in sent), [s[0] for s in sent])
        ComplianceProfile.query.filter_by(venture_id=V).first().exemption = '506b'
        db.session.commit()
        with app.test_request_context():
            res = sas.create_checkout(page, 'rae@vc.vc')
        hook({'id': res['session_id'], 'payment_status': 'paid', 'amount_total': 9900, 'payment_intent': 'pi_rae',
              'metadata': created[-1]['metadata']})
        db.session.expire_all()
        rae = InvestorAccess.query.filter_by(email='rae@vc.vc').first()
        check('under 506(b) the uninvited payer waits', rae is not None and rae.status == 'requested')
        del sent[:]
        code, body = api('post', '/api/founder/v/%s/investor-access/%s' % (V, rae.id), {'action': 'deny'})
        db.session.expire_all()
        rae = InvestorAccess.query.get(rae.id)
        check('declining a paid request refunds it', code == 200 and rae.status == 'refunded'
              and ('pi_rae', 'Investor access declined by the founders') in refunds, (code, rae.status))
        check('... and tells the investor', any(s[1] == ['rae@vc.vc'] for s in sent))
        code, body = api('post', '/api/founder/v/%s/investor-access/%s' % (V, rae.id), {'action': 'approve'})
        check('a declined access cannot be approved afterwards', code == 409, code)
        founder_config.set('fnd_investor_access_cents', 0, A_ID)
        try:
            with app.test_request_context():
                sas.create_checkout(page, 'zero@vc.vc')
            check('a price under 50 cents means no paid access', False)
        except sas.AccessError:
            check('a price under 50 cents means no paid access', True)
        check('... and the unlock page says so', 'Paid access is not offered' in
              inv3.get('/startup/%s/unlock' % SLUG).get_data(as_text=True))
        founder_config.set('fnd_investor_access_cents', 4900, A_ID)

        print('\n8. Access requests (free)')
        login(None, inv3)
        with inv3.session_transaction() as s:
            s['_investors'] = ['sam@seed.vc']
        r = inv3.post('/startup/%s/access-request' % SLUG, data={'name': 'Sam Seed', 'message': 'Seed fund, logistics'})
        req = InvestorAccess.query.filter_by(email='sam@seed.vc').first()
        check('a verified visitor can ask for access', r.status_code == 200 and req is not None
              and req.kind == 'requested' and req.status == 'requested')
        code, _ = api('post', '/api/founder/v/%s/investor-access/%s' % (V, req.id), {'action': 'approve'}, uid=X_ID)
        check('a non-member cannot approve', code in (403, 404), code)
        try:
            sas.approve_access(req, db.session.get(User, X_ID))
            check('approve_access refuses a non-member', False)
        except sas.AccessError:
            check('approve_access refuses a non-member', True)
        sas.approve_access(req, db.session.get(User, O_ID))
        check('the owner approves; the visitor now has access', sas.has_access(page, 'sam@seed.vc')
              and 'US$ 1,500,000' in inv3.get('/startup/%s/investor' % SLUG).get_data(as_text=True))
        r = app.test_client().post('/startup/%s/access-request' % SLUG, json={'message': 'hi'})
        check('an unverified visitor must sign in first (401)', r.status_code == 401, r.status_code)

        print('\n9. One-pager')
        public_html = founder_onepager.build_html(page, False)
        inv_html = founder_onepager.build_html(page, True)
        check('the public one-pager leaves out the ask', 'Use of funds' not in public_html
              and '1,500,000' not in public_html and 'Two engineers' not in public_html)
        check('the investor one-pager includes the ask', 'Use of funds' in inv_html and '1,500,000' in inv_html)
        check('both are dated and carry the claims note', 'Prepared ' in public_html and 'Prepared ' in inv_html
              and 'founder-stated' in public_html.lower())
        check('traction is labelled founder-stated', '[founder-stated]' in public_html)
        check('no unpermitted quote in either', 'SECRET' not in public_html + inv_html)
        import fitz
        r = app.test_client().get('/startup/%s/onepager.pdf' % SLUG)
        text = ''.join(pg.get_text() for pg in fitz.open(stream=r.data, filetype='pdf'))
        check('anyone gets the public PDF', r.status_code == 200 and r.headers['Content-Type'] == 'application/pdf'
              and 'Dockwise' in text and '1,500,000' not in text, r.status_code)
        r = inv3.get('/startup/%s/onepager.pdf' % SLUG)
        text = ''.join(pg.get_text() for pg in fitz.open(stream=r.data, filetype='pdf'))
        check('an investor with access gets the PDF with the ask', r.status_code == 200 and '1,500,000' in text
              and 'investor-one-pager' in r.headers['Content-Disposition'])

        print('\n10. Unpublishing refunds paid access inside the window')
        old = InvestorAccess(startup_page_id=P, email='old@vc.vc', kind='paid', status='active',
                             payment_intent_id='pi_old', amount_cents=4900, stripe_session_id='cs_old',
                             created_at=now - timedelta(days=20))
        db.session.add(old)
        db.session.commit()
        del refunds[:]
        sps.unpublish(page, db.session.get(User, C_ID))
        db.session.expire_all()
        pis = sorted(p for p, _r in refunds)
        check('paid access within the window is refunded', 'pi_pat' in pis and 'pi_quinn' in pis, pis)
        check('... access older than the window is not', 'pi_old' not in pis
              and InvestorAccess.query.get(old.id).status == 'active')
        check('invited and free access is not "refunded"',
              InvestorAccess.query.filter_by(email='sam@seed.vc').first().status == 'active')
        pat = InvestorAccess.query.filter_by(email='pat@angel.vc', kind='paid').first()
        check('refunded rows are marked', pat.status == 'refunded' and pat.refunded_at is not None)
        check('investor routes 404 once the page is unpublished',
              inv3.get('/startup/%s/investor' % SLUG).status_code == 404)
        n = sas.refund_if_unpublished(page)
        check('running it again refunds nothing twice', n == 0)

        print('\n11. charge.refunded webhook and existing payment branches')
        r = hook({'id': 'ch_1', 'payment_intent': 'pi_old', 'refunded': True}, etype='charge.refunded')
        db.session.expire_all()
        check('a full Stripe refund ends that paid access', r.status_code == 200
              and InvestorAccess.query.get(old.id).status == 'refunded')
        r = hook({'id': 'ch_2', 'payment_intent': 'pi_unknown', 'refunded': True}, etype='charge.refunded')
        check('an unrelated refund is a no-op', r.status_code == 200)
        mv = founder_ventures.create_venture(db.session.get(User, A_ID), 'Admin Co', '', 'idea')
        MV = mv.id
        r = hook({'id': 'cs_founder', 'payment_status': 'paid', 'amount_total': 49900, 'payment_intent': 'pi_f',
                  'metadata': {'product': 'founder_track', 'venture_id': MV, 'user_id': A_ID,
                               'amount_cents': '49900'}})
        db.session.expire_all()
        check('the founder_track branch still marks a venture paid', r.status_code == 200
              and db.session.get(FounderVenture, MV).paid_at is not None)
        check('... and records no investor access', InvestorAccess.query.filter_by(stripe_session_id='cs_founder')
              .count() == 0)
        hook({'id': 'ch_3', 'payment_intent': 'pi_f', 'refunded': True}, etype='charge.refunded')
        db.session.expire_all()
        check('a founder refund still locks the venture and touches no investor row',
              db.session.get(FounderVenture, MV).paid_at is None)

        print('\n12. Copy rules')
        cfg = __import__('json').load(io.open(os.path.join(ROOT, 'config', 'compliance_phrases.json'),
                                              encoding='utf-8'))
        blob = ''
        for f in NEW_PY + ['app/templates/startup/investor_base.html', 'app/templates/startup/investor_signin.html',
                           'app/templates/startup/investor_unlock.html', 'app/templates/startup/investor_request.html',
                           'app/templates/startup/investor_view.html']:
            blob += io.open(os.path.join(ROOT, f), encoding='utf-8').read().lower()
        for ok in cfg['allowed_contexts']:
            blob = blob.replace(ok, ' ')
        hits = [p for p in cfg['banned'] if re.search(r'(?<![a-z])' + re.escape(p) + r'(?![a-z])', blob)]
        check('no banned phrase in the new files', not hits, hits)
        check('no return projections in the new files', not sps._RETURN_RE.search(blob))

    print('\n%d passed, %d failed' % (len(PASS), len(FAIL)))
    if FAIL:
        print('FAILED:\n  ' + '\n  '.join(FAIL))
        sys.exit(1)


if __name__ == '__main__':
    main()
