"""Founder Track customer email (CUSTOMER_EMAIL_PLAN.md, SIM-PRD-FND-001 decision
of Nov 21, 2026): contact book, per-agent access, approval rules for the seven
customer agents, sending in the founder's name, scheduling, bounces/complaints
and the auto-pause, opt-outs, replies, observability, and that nothing changes
while the setting is off.

Run: python test_founder_customer_email.py
"""
import io
import json
import os
import sys
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

PASS, FAIL = [], []
NEW_PY = ('app/services/founder_customer_email.py', 'app/blueprints/founder/customer_email.py',
          'cron_customer_email.py')


def check(label, condition, detail=''):
    (PASS if condition else FAIL).append(label)
    print(('  ok   ' if condition else '  FAIL ') + label + ('' if condition else '  -> %r' % (detail,)))


def main():
    import requests
    requests.post = requests.get = lambda *a, **k: (_ for _ in ()).throw(AssertionError('network'))
    import sendgrid
    SENT = []

    class _FakeSG(object):
        def __init__(self, api_key=None, *a, **k):
            pass

        def send(self, message):
            SENT.append(message.get())
            return type('R', (), {'status_code': 202, 'body': '', 'headers': {'X-Message-Id': 'SG%d' % len(SENT)}})()
    sendgrid.SendGridAPIClient = _FakeSG

    print('\n0. Python 3.9 safe, migration present')
    missing = [f for f in NEW_PY
               if 'from __future__ import annotations' not in io.open(os.path.join(ROOT, f), encoding='utf-8').read()]
    check('every new module starts with the __future__ import', not missing, missing)
    sql = io.open(os.path.join(ROOT, 'sql', '080_founder_customer_email.sql'), encoding='utf-8').read()
    check('sql/080 creates the four tables and the two columns',
          all(t in sql for t in ('CREATE TABLE venture_contact ', 'CREATE TABLE venture_contact_access',
                                 'CREATE TABLE venture_email_message', 'CREATE TABLE venture_suppression',
                                 'customer_paused_at', 'send_meta')))

    import tempfile
    from app import create_app
    from app.extensions import db
    from utils.id_gen import generate_id
    app = create_app('testing')
    app.config.update(SQLALCHEMY_DATABASE_URI='sqlite:///:memory:', WTF_CSRF_ENABLED=False,
                      EMAIL_PROVIDER='sendgrid', SENDGRID_API_KEY='test', SERVER_NAME=None,
                      UPLOAD_FOLDER=tempfile.mkdtemp(prefix='custmail_'))
    from app.services import email_service
    email_service._send = lambda *a, **k: None
    from cryptography.fernet import Fernet
    app.config['ENCRYPTION_KEY'] = Fernet.generate_key().decode()

    with app.app_context():
        db.create_all()
        from flask import g
        from app.models.founder import (VentureContact, VentureEmailMessage, VentureOutreachAction,
                                        VenturePlanStep, VentureSuppression, VentureMember)
        from app.models.user import User
        from app.services import founder_config, founder_feature, founder_ventures, sender_identity
        from app.services import founder_customer_email as fce
        now = datetime.utcnow()

        def user(email, name, admin=False):
            u = User(id=generate_id(), email=email, full_name=name, password_hash='x', is_admin=admin,
                     email_verified=True, onboarding_completed_at=now)
            db.session.add(u)
            db.session.commit()
            return u.id

        A_ID = user('admin@x.com', 'Admin', admin=True)
        O_ID = user('olive@founder.com', 'Olive Owner')
        C_ID = user('cora@founder.com', 'Cora Cofounder')
        X_ID = user('out@x.com', 'Outsider')
        founder_feature.set_mode('founder_track', 'on', A_ID)
        v = founder_ventures.create_venture(db.session.get(User, O_ID), 'Dockwise', 'Dock scheduling', 'seed')
        v.paid_at = now
        db.session.add(VentureMember(venture_id=v.id, user_id=C_ID, role='cofounder', status='active', joined_at=now))
        db.session.commit()
        V = v.id
        sender_identity.set_postal_address(db.session.get(User, O_ID), '1 Market St, San Francisco, CA 94105')
        db.session.commit()

        layers = {'fnd_pain_evidence': 1, 'fnd_customer_interview': 1, 'fnd_poc_plan': 2,
                  'fnd_prototype_feedback': 2, 'fnd_pilot_offer': 3, 'fnd_pricing_test': 3,
                  'fnd_outbound_pipeline': 3}
        bodies = {'fnd_pricing_test': 'Hi [First name], our plan is $49 a month for [Company]. [Your name]',
                  'fnd_pilot_offer': 'Hi [First name], the pilot is $500. [Your name]'}
        ACT = {}
        for key, layer in layers.items():
            st = VenturePlanStep(venture_id=V, layer=layer, kind='agent', agent_key=key, title=key,
                                 state='awaiting_approval')
            db.session.add(st)
            db.session.flush()
            a = VentureOutreachAction(venture_id=V, plan_step_id=st.id, action_type='customer_message',
                                      level_required=3, status='awaiting_approval', subject='About [Company]',
                                      body=bodies.get(key, 'Hi [First name], quick question about [Company]. [Your name]'))
            db.session.add(a)
            db.session.flush()
            ACT[key] = a.id
        db.session.commit()

        client = app.test_client()
        for verb in ('get', 'post'):
            orig = getattr(client, verb)
            setattr(client, verb, (lambda o: lambda *a, **k: (g.pop('_login_user', None), o(*a, **k))[1])(orig))

        def login(uid):
            with client.session_transaction() as s:
                s['_user_id'], s['_fresh'] = uid, True

        def act(key):
            return db.session.get(VentureOutreachAction, ACT[key])

        def api(path, body=None, uid=O_ID):
            login(uid)
            r = client.post(path, json=body or {})
            return r.status_code, (r.get_json(silent=True) or {})

        def preview(key, ids, uid=O_ID):
            return api('/api/founder/actions/%s/customer/preview' % ACT[key], {'contact_ids': ids}, uid)

        def send(key, ids, each=None, campaign=None, schedule_at=None, uid=O_ID):
            code, p = preview(key, ids, uid)
            if code != 200:
                return code, p
            body = {'contact_ids': ids, 'approved_each': each if each is not None else p['needs_each'],
                    'approve_campaign': campaign if campaign is not None else bool(p['campaign']), 'hash': p['hash']}
            if schedule_at:
                body['schedule_at'] = schedule_at
            return api('/api/founder/actions/%s/customer/send' % ACT[key], body, uid)

        print('\n1. Setting off: nothing changes')
        login(O_ID)
        html = client.get('/founder/v/%s/plan' % V).get_data(as_text=True)
        check('plan drafts show no "Send through SimiSI" while off', 'Send through SimiSI' not in html
              and 'Approve draft' in html)
        check('no Contacts tab while off', '/founder/v/%s/contacts' % V not in html)
        code, body = preview('fnd_pain_evidence', ['x'])
        check('preview is refused while off', code == 409 and 'not switched on' in body.get('error', ''), body)
        check('agents are not offered suggested contacts while off',
              'suggested_contacts' not in json.dumps(__import__('app.services.founder_agent_service', fromlist=['x'])
                                                     ._tool(__import__('app.services.founder_registry', fromlist=['x'])
                                                            .get('fnd_pain_evidence'))))

        founder_config.set('fnd_customer_email', 'on', A_ID)
        founder_config.set('fnd_customer_from_address', 'founders@simisi.io', A_ID)

        print('\n2. Setting on: tab, links and pages')
        login(O_ID)
        html = client.get('/founder/v/%s/plan' % V).get_data(as_text=True)
        check('plan drafts link to the send page', 'Send through SimiSI' in html
              and '/founder/v/%s/send/%s' % (V, ACT['fnd_pain_evidence']) in html)
        check('the Contacts tab shows', '/founder/v/%s/contacts' % V in html)
        r = client.get('/founder/v/%s/contacts' % V)
        check('the contact book page loads', r.status_code == 200 and 'Contact book' in r.get_data(as_text=True))
        login(X_ID)
        check('an outsider cannot open the contact book', client.get('/founder/v/%s/contacts' % V).status_code in (403, 404))
        check('agents are offered suggested contacts while on',
              'suggested_contacts' in json.dumps(__import__('app.services.founder_agent_service', fromlist=['x'])
                                                 ._tool(__import__('app.services.founder_registry', fromlist=['x'])
                                                        .get('fnd_pain_evidence'))))

        print('\n3. Contact book')
        code, body = api('/api/founder/v/%s/contacts' % V, {'email': 'Ann@Acme.com', 'name': 'Ann Lee', 'company': 'Acme',
                                                             'agents': ['fnd_pain_evidence', 'fnd_outbound_pipeline']})
        check('a founder adds a contact (stored lower-case, approved)', code == 201
              and body['contact']['email'] == 'ann@acme.com' and body['contact']['state'] == 'approved', body)
        ANN = body['contact']['id']
        check('the agent ticks are saved', fce.access_map(v).get(ANN) == {'fnd_pain_evidence', 'fnd_outbound_pipeline'})
        code, body = api('/api/founder/v/%s/contacts' % V, {'email': 'ann@acme.com'})
        check('a duplicate is refused', code == 409, body)
        code, body = api('/api/founder/v/%s/contacts' % V, {'email': 'not-an-email'})
        check('a bad address is refused', code == 400, body)
        code, body = api('/api/founder/v/%s/contacts' % V, {'email': 'zed@x.com'}, uid=C_ID)
        check('a co-founder can add contacts too', code == 201, body)
        login(X_ID)
        r = client.post('/api/founder/v/%s/contacts' % V, json={'email': 'evil@x.com'})
        check('an outsider cannot add contacts', r.status_code in (403, 404))
        db.session.add(VentureSuppression(venture_id=V, email='gone@x.com', reason='opt_out'))
        db.session.commit()
        code, body = api('/api/founder/v/%s/contacts' % V, {'email': 'gone@x.com'})
        check('someone on the do-not-contact list cannot be added', code == 409, body)

        csv_text = 'Email,Name,Company\nbob@beta.com,Bob Ray,Beta\nbad-address,X,Y\nann@acme.com,Ann,Acme\ncat@gamma.com,Cat,Gamma\n'
        code, body = api('/api/founder/v/%s/contacts/import' % V, {'text': csv_text})
        check('CSV import needs the "my own list" confirmation', code == 400 and 'Purchased' in body.get('error', ''), body)
        code, body = api('/api/founder/v/%s/contacts/import' % V, {'text': csv_text, 'own_list': True,
                                                                    'agents': ['fnd_pricing_test']})
        check('CSV import adds the good rows and skips bad and duplicate ones',
              code == 200 and body['added'] == 2 and body['skipped_count'] == 2, body)
        bob = VentureContact.query.filter_by(venture_id=V, email='bob@beta.com').first()
        check('imported contacts are approved and ticked for the chosen agent',
              bob.state == 'approved' and bob.source == 'csv' and fce.access_map(v).get(bob.id) == {'fnd_pricing_test'})
        founder_config.set('fnd_csv_max_rows', 1, A_ID)
        code, body = api('/api/founder/v/%s/contacts/import' % V, {'text': csv_text, 'own_list': True})
        check('a CSV over the row limit is refused', code == 400 and 'most in one upload' in body.get('error', ''), body)
        founder_config.set('fnd_csv_max_rows', 500, A_ID)

        n = fce.suggest_contacts(v, 'fnd_pain_evidence', [{'email': 'dan@delta.com', 'name': 'Dan', 'reason': 'named in deck'},
                                                          {'email': 'nonsense'}, {'email': 'ann@acme.com'}])
        db.session.commit()
        dan = VentureContact.query.filter_by(venture_id=V, email='dan@delta.com').first()
        check('agent suggestions arrive as Proposed; bad and duplicate ones are dropped',
              n == 1 and dan.state == 'proposed' and dan.proposed_by_agent == 'fnd_pain_evidence')
        code, body = api('/api/founder/v/%s/contacts/%s/access' % (V, dan.id), {'agent': 'fnd_pain_evidence', 'allowed': True})
        check('a proposed contact cannot be given to an agent', code == 409, body)
        code, body = api('/api/founder/v/%s/contacts/%s/approve' % (V, dan.id))
        check('a founder approves a suggestion', code == 200 and db.session.get(VentureContact, dan.id).state == 'approved', body)
        code, body = api('/api/founder/v/%s/contacts/%s/access' % (V, dan.id), {'agent': 'fnd_pain_evidence', 'allowed': True})
        check('... and can then tick it for an agent', code == 200 and body['allowed'] is True, body)
        code, body = api('/api/founder/v/%s/contacts/%s/access' % (V, dan.id), {'agent': 'fnd_problem_statement', 'allowed': True})
        check('only customer agents can be ticked', code == 400, body)

        print('\n4. Recipients and approval rules')
        code, body = preview('fnd_pain_evidence', [bob.id])
        check('a contact not ticked for the agent cannot be picked', code == 409 and 'Tick them' in body.get('error', ''), body)
        code, body = preview('fnd_pain_evidence', [ANN, dan.id], uid=C_ID)
        check('only the decision-right holder sends (a co-founder without it is refused)', code == 403, body)
        code, body = preview('fnd_pain_evidence', [ANN, dan.id])
        check('Pain-Point Evidence first contact is approve-each even at level 3',
              code == 200 and sorted(body['needs_each']) == sorted([ANN, dan.id]) and not body['campaign'], body)
        check('the preview shows From "<founder> via SimiSI", the From address and Reply-To the founder',
              body.get('from_name') == 'Olive Owner via SimiSI' and body.get('from_email') == 'founders@simisi.io'
              and body.get('reply_to') == 'olive@founder.com', body)
        rec = {x['contact_id']: x for x in body['recipients']}
        check('merge fields are filled per recipient', rec[ANN]['subject'] == 'About Acme'
              and 'Hi Ann,' in rec[ANN]['html'] and 'Olive Owner' in rec[ANN]['html'], rec[ANN])
        code, p = preview('fnd_pain_evidence', [ANN, dan.id])
        code, body = api('/api/founder/actions/%s/customer/send' % ACT['fnd_pain_evidence'],
                         {'contact_ids': [ANN, dan.id], 'approved_each': [ANN], 'hash': p['hash']})
        check('every approve-each recipient must be approved on its own', code == 409 and 'on its own' in body.get('error', ''),
              body)
        act('fnd_pain_evidence').body = act('fnd_pain_evidence').body + ' Changed.'
        db.session.commit()
        code, body = api('/api/founder/actions/%s/customer/send' % ACT['fnd_pain_evidence'],
                         {'contact_ids': [ANN, dan.id], 'approved_each': [ANN, dan.id], 'hash': p['hash']})
        check('a draft that changed since the preview is refused', code == 409 and 'Preview again' in body.get('error', ''), body)

        n0 = len(SENT)
        code, body = send('fnd_pain_evidence', [ANN, dan.id])
        check('approved sends go out', code == 200 and body.get('sent') == 2, body)
        msgs = SENT[n0:]
        m0 = msgs[0] if msgs else {}
        check('the email is From "<founder> via SimiSI" <configured address>',
              m0.get('from', {}).get('name') == 'Olive Owner via SimiSI' and m0.get('from', {}).get('email') == 'founders@simisi.io', m0.get('from'))
        check('Reply-To is the founder', (m0.get('reply_to') or {}).get('email') == 'olive@founder.com', m0.get('reply_to'))
        content = json.dumps(m0.get('content'))
        check('it carries the unsubscribe link and the mailing address', '1 Market St' in content
              and 'founder-unsubscribe' in content and 'List-Unsubscribe' in json.dumps(m0.get('headers')), content[:300])
        a = act('fnd_pain_evidence')
        check('the draft is approved by the holder and marked sent', a.status == 'sent' and a.approved_by == O_ID
              and a.level_applied == 3, (a.status, a.approved_by, a.level_applied))
        rows = VentureEmailMessage.query.filter_by(outreach_action_id=a.id).all()
        check('one message row per recipient, with approval mode and EmailLog link',
              len(rows) == 2 and all(r.status == 'sent' and r.approval_mode == 'each' and r.email_log_id for r in rows),
              [(r.status, r.approval_mode, r.email_log_id) for r in rows])
        check('the contacts move to Contacted', db.session.get(VentureContact, ANN).state == 'contacted')
        code, body = preview('fnd_pain_evidence', [ANN])
        check('the same draft cannot go to the same person twice', code == 409 and 'Already sent' in body.get('error', ''), body)

        # Pain-Point follow-ups are campaign-approved at level 3.
        st2 = VenturePlanStep(venture_id=V, layer=1, kind='agent', agent_key='fnd_pain_evidence', title='round 2',
                              state='awaiting_approval')
        db.session.add(st2)
        db.session.flush()
        a2 = VentureOutreachAction(venture_id=V, plan_step_id=st2.id, action_type='customer_message', level_required=3,
                                   status='awaiting_approval', subject='Following up', body='Hi [First name], following up.')
        db.session.add(a2)
        db.session.commit()
        ACT['pain2'] = a2.id
        code, body = preview('pain2', [ANN, dan.id])
        check('follow-ups to people already emailed are campaign-approved', code == 200 and sorted(body['campaign']) ==
              sorted([ANN, dan.id]) and not body['needs_each'], body)
        a2.level_required = 2
        db.session.commit()
        code, body = preview('pain2', [ANN, dan.id])
        check('at level 2 every recipient is approve-each', code == 200 and len(body['needs_each']) == 2, body)
        a2.level_required = 1
        db.session.commit()
        code, body = preview('pain2', [ANN])
        check('at level 1 (advisor only) nothing is sent', code == 409 and 'advisor only' in body.get('error', ''), body)
        a2.level_required = 3
        db.session.commit()
        code, body = send('pain2', [ANN, dan.id], campaign=False)
        check('a campaign send needs the campaign approval', code == 409 and 'campaign' in body.get('error', ''), body)

        # Outbound Pipeline
        e1 = fce.add_contact(v, db.session.get(User, O_ID), 'p1@prospect.com', name='Pat One')
        e2 = fce.add_contact(v, db.session.get(User, O_ID), 'p2@prospect.com', name='Pia Two')
        for c in (e1, e2):
            fce.set_access(v, db.session.get(User, O_ID), c.id, 'fnd_outbound_pipeline', True)
        code, body = preview('fnd_outbound_pipeline', [e1.id, e2.id])
        check('Outbound Pipeline refuses two first contacts in one send', code == 409 and 'one prospect at a time' in body.get('error', ''), body)
        code, body = send('fnd_outbound_pipeline', [e1.id])
        check('Outbound Pipeline sends one first contact, approved individually', code == 200 and body.get('sent') == 1, body)
        st3 = VenturePlanStep(venture_id=V, layer=3, kind='agent', agent_key='fnd_outbound_pipeline', title='ob2',
                              state='awaiting_approval')
        db.session.add(st3)
        db.session.flush()
        a3 = VentureOutreachAction(venture_id=V, plan_step_id=st3.id, action_type='customer_message', level_required=3,
                                   status='awaiting_approval', subject='Re-checking in', body='Hi [First name], thanks.')
        db.session.add(a3)
        db.session.commit()
        ACT['ob2'] = a3.id
        code, body = preview('ob2', [e1.id])
        check('Outbound follow-ups go only to prospects who replied', code == 409 and 'who replied' in body.get('error', ''), body)
        code, body = api('/api/founder/v/%s/contacts/%s/replied' % (V, e1.id))
        check('"Mark replied" marks the contact Replied', code == 200 and db.session.get(VentureContact, e1.id).state == 'replied', body)
        code, body = preview('ob2', [e1.id])
        check('... then the follow-up is allowed, campaign-approved', code == 200 and body['campaign'] == [e1.id], body)

        # Proof-of-Concept Plan and Pilot Offer
        for c in (bob, e2):
            fce.set_access(v, db.session.get(User, O_ID), c.id, 'fnd_poc_plan', True)
            fce.set_access(v, db.session.get(User, O_ID), c.id, 'fnd_pilot_offer', True)
        code, body = preview('fnd_poc_plan', [bob.id, e2.id])
        check('the Proof-of-Concept Plan goes to one named decision-maker', code == 409 and 'one named' in body.get('error', ''), body)
        code, body = preview('fnd_pilot_offer', [bob.id])
        check('the Pilot Offer is refused to anyone but the PoC customer', code == 409 and 'proof-of-concept customer' in body.get('error', ''), body)
        code, body = send('fnd_poc_plan', [bob.id])
        check('sending the PoC plan marks that person as the PoC customer', code == 200 and body.get('sent') == 1
              and db.session.get(VentureContact, bob.id).poc_customer, body)
        code, body = preview('fnd_pilot_offer', [bob.id])
        check('... and the Pilot Offer can then go to them, approve-each', code == 200 and body['needs_each'] == [bob.id], body)

        # Pricing Test
        code, body = preview('fnd_pricing_test', [bob.id])
        check('the Pricing Test needs locked prices first', code == 409 and 'Lock the price' in body.get('error', ''), body)
        code, body = api('/api/founder/actions/%s/customer/settings' % ACT['fnd_pricing_test'], {'locked_prices': ['$99']})
        check('locking a set that misses a price the email states is refused', code == 409, body)
        code, body = api('/api/founder/actions/%s/customer/settings' % ACT['fnd_pricing_test'], {'locked_prices': ['$49']})
        check('the founder locks the price versions', code == 200 and body['meta']['locked_prices'] == ['$49'], body)
        code, body = api('/api/founder/actions/%s/customer/edit' % ACT['fnd_pricing_test'],
                         {'subject': 'Pricing', 'body': 'Hi [First name], it is now $59. [Your name]'})
        check('the draft cannot change a locked price', code == 409 and 'locked' in body.get('error', ''), body)
        code, body = preview('fnd_pricing_test', [bob.id, ANN] if False else [bob.id])
        check('with prices locked the Pricing Test previews (campaign)', code == 200 and body['campaign'] == [bob.id], body)

        # Customer Interview
        fce.set_access(v, db.session.get(User, O_ID), bob.id, 'fnd_customer_interview', True)
        code, body = preview('fnd_customer_interview', [bob.id])
        check('interview invites need a scheduling link', code == 409 and 'scheduling link' in body.get('error', ''), body)
        code, body = api('/api/founder/actions/%s/customer/settings' % ACT['fnd_customer_interview'],
                         {'scheduling_link': 'javascript:alert(1)'})
        check('the scheduling link must be https', code == 400, body)
        api('/api/founder/actions/%s/customer/settings' % ACT['fnd_customer_interview'],
            {'scheduling_link': 'https://cal.com/olive/20min'})
        code, body = preview('fnd_customer_interview', [bob.id])
        check('the scheduling link is added to the invite', code == 200
              and 'https://cal.com/olive/20min' in body['recipients'][0]['html'], body)

        print('\n5. Drafts, placeholders, rules, caps')
        code, body = api('/api/founder/actions/%s/customer/edit' % ACT['fnd_prototype_feedback'],
                         {'subject': 'Feedback', 'body': 'Hi [First name], see [Insert demo link]. [Your name]'})
        check('the holder edits a draft', code == 200 and act('fnd_prototype_feedback').body.startswith('Hi [First name]'), body)
        fce.set_access(v, db.session.get(User, O_ID), bob.id, 'fnd_prototype_feedback', True)
        code, body = preview('fnd_prototype_feedback', [bob.id])
        check('an unfilled placeholder blocks sending', code == 409 and '[Insert demo link]' in body.get('error', ''), body)
        code, body = api('/api/founder/actions/%s/customer/edit' % ACT['fnd_prototype_feedback'],
                         {'subject': 'Feedback', 'body': 'Hi [First name], you will 10x your returns guaranteed. [Your name]'}, uid=C_ID)
        check('a co-founder without the decision right cannot edit', code == 403, body)
        u = db.session.get(User, O_ID)
        pa = u.postal_address_enc
        u.postal_address_enc = None
        db.session.commit()
        api('/api/founder/actions/%s/customer/edit' % ACT['fnd_prototype_feedback'],
            {'subject': 'Feedback', 'body': 'Hi [First name], could you try the demo? [Your name]'})
        code, body = preview('fnd_prototype_feedback', [bob.id])
        check('no mailing address: refused', code == 409 and 'mailing address' in body.get('error', ''), body)
        u.postal_address_enc = pa
        db.session.commit()
        founder_config.set('fnd_action_cap_per_day', 1, A_ID)
        fce.set_access(v, db.session.get(User, O_ID), e2.id, 'fnd_prototype_feedback', True)
        code, body = preview('fnd_prototype_feedback', [bob.id, e2.id])
        check('the per-agent daily cap applies', code == 429, body)
        founder_config.set('fnd_action_cap_per_day', 10, A_ID)

        print('\n6. Scheduling and the worker')
        later = (datetime.utcnow() + timedelta(hours=2)).isoformat() + 'Z'
        n0 = len(SENT)
        code, body = send('fnd_prototype_feedback', [bob.id, e2.id], schedule_at=later)
        check('a send can be scheduled', code == 200 and body.get('scheduled') == 2 and len(SENT) == n0, body)
        sched = VentureEmailMessage.query.filter_by(outreach_action_id=ACT['fnd_prototype_feedback'], status='scheduled').all()
        check('scheduled rows wait', len(sched) == 2 and all(m.scheduled_for > datetime.utcnow() for m in sched))
        code, body = api('/api/founder/v/%s/messages/%s/cancel' % (V, sched[1].id))
        check('a scheduled email can be cancelled', code == 200 and db.session.get(VentureEmailMessage, sched[1].id).status == 'cancelled')
        out = fce.process_scheduled(now=datetime.utcnow())
        check('the worker leaves future emails alone', out['due'] == 0, out)
        out = fce.process_scheduled(now=datetime.utcnow() + timedelta(hours=3))
        check('the worker sends what is due', out['sent'] == 1 and len(SENT) == n0 + 1, out)

        print('\n7. Bounces, complaints, opt-outs and the auto-pause')
        from app.models.outreach_email import EmailLog
        bob_msg = (VentureEmailMessage.query.filter_by(venture_contact_id=bob.id, status='sent')
                   .order_by(VentureEmailMessage.sent_at).first())
        log = db.session.get(EmailLog, bob_msg.email_log_id)
        log.provider_message_id = 'BOUNCE1'
        db.session.commit()
        r = client.post('/api/webhooks/sendgrid', json=[{'event': 'bounce', 'sg_message_id': 'BOUNCE1.filter', 'email': 'bob@beta.com',
                                                          'reason': '550 no such user'}])
        check('a SendGrid bounce for a venture email is accepted', r.status_code == 200)
        check('... the contact is Bounced and on the venture do-not-contact list',
              db.session.get(VentureContact, bob.id).state == 'bounced'
              and VentureSuppression.query.filter_by(venture_id=V, email='bob@beta.com', reason='bounce').first() is not None)
        code, body = preview('fnd_pilot_offer', [bob.id])
        check('... and no agent can email them again', code == 409, body)
        code, body = api('/api/founder/v/%s/contacts/%s/restore' % (V, bob.id))
        check('a bounced contact cannot be restored by the founders', code == 409, body)

        from app.services import founder_investors
        login(O_ID)
        tok_url = founder_investors.unsubscribe_url(v, 'ann@acme.com')
        r = client.get(tok_url[tok_url.index('/outreach/'):])
        check('the unsubscribe link works', r.status_code == 200)
        check('... and puts the person on the venture do-not-contact list',
              db.session.get(VentureContact, ANN).state == 'opted_out'
              and VentureSuppression.query.filter_by(venture_id=V, email='ann@acme.com', reason='opt_out').first() is not None)

        founder_config.set('fnd_customer_rate_min_sends', 1, A_ID)
        dan_msg = VentureEmailMessage.query.filter_by(venture_contact_id=dan.id, status='sent').first()
        dlog = db.session.get(EmailLog, dan_msg.email_log_id)
        fce.add_contact(v, db.session.get(User, O_ID), 'q@later.com', name='Quinn')
        later2 = (datetime.utcnow() + timedelta(hours=5)).isoformat() + 'Z'
        q = VentureContact.query.filter_by(venture_id=V, email='q@later.com').first()
        fce.set_access(v, db.session.get(User, O_ID), q.id, 'fnd_prototype_feedback', True)
        code, body = send('fnd_prototype_feedback', [q.id], schedule_at=later2)
        check('(a scheduled email waiting)', code == 200 and body.get('scheduled') == 1, body)
        fce.on_email_event(dlog, 'spamreport', {})
        db.session.commit()
        v = db.session.get(type(v), V)
        check('a spam complaint over the threshold pauses the venture\'s customer email',
              v.customer_paused_at is not None and 'complaint rate' in (v.customer_paused_reason or ''), v.customer_paused_reason)
        check('... scheduled emails are cancelled',
              VentureEmailMessage.query.filter_by(venture_contact_id=q.id).first().status == 'cancelled')
        check('... and the complainer is on the do-not-contact list',
              db.session.get(VentureContact, dan.id).state == 'opted_out')
        e3 = fce.add_contact(v, db.session.get(User, O_ID), 'p3@prospect.com', name='Pez')
        fce.set_access(v, db.session.get(User, O_ID), e3.id, 'fnd_outbound_pipeline', True)
        code, body = preview('ob2', [e3.id])
        check('while paused nothing can be sent', code == 409 and 'paused' in body.get('error', ''), body)
        login(O_ID)
        check('the founders see the pause on the contact book', 'Customer email is paused' in
              client.get('/founder/v/%s/contacts' % V).get_data(as_text=True))

        print('\n8. Admin: observability and resume')
        login(A_ID)
        r = client.get('/api/admin/observability/founder/connectors?hours=720')
        d = r.get_json() or {}
        ce = d.get('customer_email') or {}
        check('Connectors shows customer email per venture', r.status_code == 200 and ce.get('available')
              and ce['totals']['sent'] >= 5 and ce['ventures'][0]['paused'] is True, ce.get('totals'))
        check('... with reply and complaint rates and per-agent rows', ce['totals'].get('complaint_pct') is not None
              and any(a_['agent'] == 'fnd_pain_evidence' for a_ in ce['agents']))
        from app.models.founder import StartupPage, InvestorLink
        pg = StartupPage(venture_id=V, slug='dockwise', name='Dockwise', status='published')
        db.session.add(pg)
        db.session.flush()
        db.session.add(InvestorLink(venture_id=V, startup_page_id=pg.id, token='t1', opened_at=datetime.utcnow(), open_count=2))
        db.session.add(InvestorLink(venture_id=V, startup_page_id=pg.id, token='t2'))
        db.session.commit()
        r = client.get('/api/admin/observability/founder/startup-pages?hours=720')
        prow = [p_ for p_ in (r.get_json() or {}).get('pages', []) if p_['slug'] == 'dockwise']
        check('Startup pages reports invited links opened (1 of 2)', r.status_code == 200 and prow
              and prow[0]['links_opened'] == 1 and prow[0]['links_issued'] == 2, prow)
        r = client.post('/api/admin/observability/founder/ventures/%s/customer-email' % V, json={'paused': False})
        v = db.session.get(type(v), V)
        check('an admin resumes customer email', r.status_code == 200 and v.customer_paused_at is None, r.get_json())
        login(O_ID)
        r = client.post('/api/admin/observability/founder/ventures/%s/customer-email' % V, json={'paused': False})
        check('a founder cannot use the admin resume', r.status_code in (302, 401, 403, 404), r.status_code)

        print('\n9. Replies from a connected mailbox')
        code, body = send('ob2', [e1.id], campaign=True)
        p3 = fce.add_contact(v, db.session.get(User, O_ID), 'p4@prospect.com', name='Paz')
        fce.set_access(v, db.session.get(User, O_ID), p3.id, 'fnd_outbound_pipeline', True)
        code, body = send('fnd_outbound_pipeline', [e3.id])
        sent_at = VentureEmailMessage.query.filter_by(venture_contact_id=e3.id, status='sent').first().sent_at
        n = fce.record_inbound(O_ID, 'p3@prospect.com', sent_at - timedelta(minutes=5))
        check('a message from before the send is not a reply', n == 0 and db.session.get(VentureContact, e3.id).state == 'contacted')
        n = fce.record_inbound(O_ID, 'P3@Prospect.com', sent_at + timedelta(minutes=5))
        check('a message after the send marks the contact Replied', n == 1 and db.session.get(VentureContact, e3.id).state == 'replied')
        from app.services import mail_sync
        msg = __import__('email').message_from_string('From: Pat <p1@prospect.com>\nDate: Mon, 23 Nov 2026 10:00:00 +0000\n\nhi')
        check('mail sync reads the Date header for reply matching', mail_sync._inbound_obs(1, msg)['date'] == datetime(2026, 11, 23, 10, 0))

        print('\n10. Deletion')
        from app.services import concierge_service as cs
        counts = cs._venture_rows(V)
        db.session.commit()
        check('deleting the venture removes its contact book, messages and do-not-contact list',
              all(counts.get(t, 0) > 0 for t in ('venture_contact', 'venture_email_message', 'venture_suppression',
                                                  'venture_contact_access'))
              and VentureContact.query.filter_by(venture_id=V).count() == 0, counts)

    print('\n%d passed, %d failed' % (len(PASS), len(FAIL)))
    for label in FAIL:
        print('  FAILED: ' + label)
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
