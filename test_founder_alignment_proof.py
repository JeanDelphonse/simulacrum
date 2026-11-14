"""Founder Track proof path and team alignment (FOUNDER_PLAN.md WP5).

Co-founder invite token flow (wrong, used and expired tokens), answers kept
separate until every member answered, differences -> alignment plan steps,
equity / leaving differences -> legal steps, decision-right reassignment
permissions, step hand-offs, check-ins, the evidence ladder moving one rung at
a time on recorded evidence only, the benefits case refusing unmeasured
results, the customer quote needing permission, member-only access and the
flag-off 404. Review fixes: the paid gate, invite suppression / cap / resend
throttle, invites bound to the invited address, owners can't be cleared, layer
leads, first-check answers kept, read-only page GETs, earlier plans still
reachable, measured results never replaced by estimates, odd request bodies. In-memory SQLite; SendGrid faked; requests blocked.

    python test_founder_alignment_proof.py
"""
import io
import json
import os
import sys
from datetime import datetime, timedelta

os.environ.setdefault('DATABASE_URL', 'sqlite:///:memory:')
os.environ.setdefault('SECRET_KEY', 'test-only')
ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

PASS, FAIL = [], []

NEW_PY = ['app/services/founder_proof_service.py', 'app/services/founder_alignment_service.py',
          'app/blueprints/founder/team_proof.py']


def check(label, condition, detail=''):
    (PASS if condition else FAIL).append(label)
    line = '  {} {}{}'.format('ok  ' if condition else 'FAIL', label, ('  -- ' + str(detail)) if detail else '')
    enc = sys.stdout.encoding or 'ascii'
    print(line.encode(enc, 'replace').decode(enc))


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
            return type('R', (), {'status_code': 202, 'body': '', 'headers': {}})()
    sendgrid.SendGridAPIClient = _FakeSG

    print('\n1. New files are Python 3.9 safe')
    missing = [f for f in NEW_PY
               if 'from __future__ import annotations' not in io.open(os.path.join(ROOT, f), encoding='utf-8').read()]
    check('every new module starts with the __future__ import', not missing, missing)

    from app import create_app
    from app.extensions import db
    from utils.id_gen import generate_id
    app = create_app('testing')
    app.config.update(SQLALCHEMY_DATABASE_URI='sqlite:///:memory:', WTF_CSRF_ENABLED=False,
                      EMAIL_PROVIDER='sendgrid', SENDGRID_API_KEY='test', SERVER_NAME=None)

    with app.app_context():
        db.create_all()
        from flask import g
        from app.models.audit_log import AuditLog
        from app.models.founder import (AlignmentResponse, DecisionRight, FounderVenture, ProofPlan, ProofResult,
                                        VentureMember, VenturePlanStep, VentureStepEvent)
        from app.models.user import User
        from app.services import founder_feature, founder_ventures, legal_text
        from app.services import founder_alignment_service as fas
        from app.services import founder_evaluation_service as fes
        from app.services import founder_proof_service as fps
        now = datetime.utcnow()

        def user(email, admin=False, name=None):
            u = User(id=generate_id(), email=email, full_name=name or email.split('@')[0].title(), password_hash='x',
                     is_admin=admin, email_verified=True, onboarding_completed_at=now)
            db.session.add(u)
            db.session.commit()
            return u

        founder, cofounder, other, admin, third = (user('founder@x.com', name='Fran Founder'),
                                                   user('co@x.com', name='Cam Cofounder'), user('other@x.com'),
                                                   user('admin@x.com', admin=True), user('third@x.com'))
        F_ID, C_ID, O_ID, A_ID, T_ID = founder.id, cofounder.id, other.id, admin.id, third.id
        v = founder_ventures.create_venture(founder, 'Acme Dental', 'Fewer no-shows', 'idea')
        V_ID = v.id

        client = app.test_client()
        for verb in ('get', 'post'):
            orig = getattr(client, verb)
            setattr(client, verb, (lambda o: lambda *a, **k: (g.pop('_login_user', None), o(*a, **k))[1])(orig))

        def login(uid):
            with client.session_transaction() as s:
                if uid:
                    s['_user_id'], s['_fresh'] = uid, True
                else:
                    s.pop('_user_id', None)

        def venture():
            return db.session.get(FounderVenture, V_ID)

        def u(uid):
            return db.session.get(User, uid)

        A = '/api/founder/v/%s/alignment' % V_ID
        P = '/api/founder/v/%s/proof' % V_ID

        print('\n2. Track off: every WP5 route is 404')
        login(F_ID)
        paths = ['/founder/v/%s/alignment' % V_ID, '/founder/v/%s/proof' % V_ID, A, P, '/founder/join/anything']
        check('GET pages and APIs 404 while the track is off',
              all(client.get(p).status_code == 404 for p in paths), [client.get(p).status_code for p in paths])
        check('POST APIs 404 while the track is off',
              client.post(A + '/invite', json={'email': 'a@b.com'}).status_code == 404
              and client.post(P, json={}).status_code == 404)
        login(None)
        check('join page 404s for visitors while the track is off',
              client.get('/founder/join/anything').status_code == 404)
        founder_feature.set_mode('founder_track', 'allowlist', A_ID)
        founder_feature.set_user_access([F_ID, O_ID], 'allowed', A_ID)

        print('\n3a. Unpaid ventures: team, plan and proof are locked')
        login(F_ID)
        r = client.get('/founder/v/%s/alignment' % V_ID)
        check('an unpaid owner is sent to checkout from the alignment page',
              r.status_code == 302 and '/checkout' in r.headers['Location'], r.headers.get('Location'))
        r = client.get('/founder/v/%s/proof' % V_ID)
        check('and from the proof page', r.status_code == 302 and '/checkout' in r.headers['Location'])
        r = client.post(A + '/invite', json={'email': 'a@b.com'})
        check('the APIs answer 402 with the checkout URL', r.status_code == 402 and r.get_json().get('checkout_url'))
        check('the roles page and proof API are locked too',
              client.get('/founder/v/%s/roles' % V_ID).status_code == 302 and client.get(P).status_code == 402)
        venture().paid_at = now
        db.session.commit()

        print('\n3. Member-only access')
        login(O_ID)
        check('a non-member gets 403 on the alignment and proof pages',
              client.get('/founder/v/%s/alignment' % V_ID).status_code == 403
              and client.get('/founder/v/%s/proof' % V_ID).status_code == 403)
        check('a non-member gets 403 on the APIs',
              client.get(A).status_code == 403 and client.post(P, json={'customer_name': 'x'}).status_code == 403
              and client.post(A + '/answers', json={'answers': {'vision': {'choice': 'sell'}}}).status_code == 403)
        check('an unknown venture is 404', client.get('/founder/v/NOPE12345/proof').status_code == 404)
        login(None)
        check('anonymous users get 404 on venture pages (track hidden)',
              client.get('/founder/v/%s/alignment' % V_ID).status_code == 404)

        print('\n4. Solo founders skip alignment and are offered an invite')
        login(F_ID)
        html = client.get('/founder/v/%s/alignment' % V_ID).get_data(as_text=True)
        check('the page says alignment is skipped for solo founders', 'skipped for solo founders' in html)
        check('the solo page offers the invite form', 'id="fa-invite"' in html and 'Alignment check' not in html)
        check('the not-advice note renders', legal_text.get('founder_not_advice')[:40] in html)

        print('\n5. Co-founder invites')
        r = client.post(A + '/invite', json={'email': 'not-an-email'})
        check('a bad address is refused', r.status_code == 400)
        r = client.post(A + '/invite', json={'email': 'founder@x.com'})
        check('inviting yourself is refused', r.status_code == 400)
        r = client.post(A + '/invite', json={'email': 'Co@X.com', 'title': 'CTO'})
        d = r.get_json() or {}
        check('the owner invites a co-founder', r.status_code == 201 and d.get('email_sent') is True, d)
        inv = VentureMember.query.filter_by(venture_id=V_ID, status='invited').first()
        check('the invite is a venture_member row with a token and the lower-cased email',
              inv is not None and inv.invite_token and inv.invite_email == 'co@x.com' and inv.title == 'CTO'
              and inv.user_id is None and inv.role == 'cofounder')
        check('one transactional email went to the invitee with the join link',
              len(SENT) == 1 and SENT[0]['personalizations'][0]['to'][0]['email'] == 'co@x.com'
              and inv.invite_token in json.dumps(SENT[0]) and 'Fran Founder' in SENT[0]['subject'], SENT[-1:])
        token1 = inv.invite_token
        r = client.post(A + '/invite', json={'email': 'co@x.com'})
        check('an immediate re-send to the same pending address is refused (429)',
              r.status_code == 429 and len(SENT) == 1)
        inv.created_at = now - timedelta(minutes=fas.INVITE_RESEND_MINUTES + 1)
        db.session.commit()
        r = client.post(A + '/invite', json={'email': 'co@x.com'})
        db.session.refresh(inv)
        check('re-inviting the same address re-issues the token (one row)',
              r.status_code == 201 and inv.invite_token != token1
              and VentureMember.query.filter_by(venture_id=V_ID, status='invited').count() == 1)
        TOKEN = inv.invite_token
        check('the old token no longer works', fas.resolve_invite(token1)[2] == 'invalid')
        from app.models.outreach_email import EmailSuppression
        db.session.add(EmailSuppression(email='gone@x.com', reason='unsubscribe'))
        db.session.commit()
        n_sent = len(SENT)
        r = client.post(A + '/invite', json={'email': 'gone@x.com'})
        check('a suppressed address is refused and not emailed', r.status_code == 409 and len(SENT) == n_sent
              and VentureMember.query.filter_by(venture_id=V_ID, invite_email='gone@x.com').count() == 0)
        saved_cap = fas.invite_daily_cap
        try:
            # The cap comes from founder_config (fnd_invite_daily_cap); pin it low for this check.
            fas.invite_daily_cap = lambda: 2
            r = client.post(A + '/invite', json={'email': 'capped@x.com'})
            check('the daily invite cap is enforced per venture (429)', r.status_code == 429
                  and 'tomorrow' in r.get_json()['error'] and len(SENT) == n_sent)
        finally:
            fas.invite_daily_cap = saved_cap
        r = client.post(A + '/invite', json={'email': 'third@x.com'})
        third_inv = VentureMember.query.filter_by(venture_id=V_ID, invite_email='third@x.com').first()
        r = client.post(A + '/invites/%s/revoke' % third_inv.id)
        db.session.refresh(third_inv)
        check('the owner can cancel an invite', r.status_code == 200 and third_inv.status == 'left')

        print('\n6. The join flow')
        login(None)
        r = client.get('/founder/join/wrong-token')
        check('a wrong token shows the invalid-invite page with 404',
              r.status_code == 404 and 'valid' in r.get_data(as_text=True) and 'cancelled' in r.get_data(as_text=True))
        r = client.get('/founder/join/%s' % third_inv.invite_token)
        check('a cancelled invite is not usable', r.status_code == 404)
        r = client.get('/founder/join/%s' % TOKEN)
        html = r.get_data(as_text=True)
        check('a visitor sees who invited them and a sign-in link back to the invite',
              r.status_code == 200 and 'Fran Founder invited you to join Acme Dental' in html
              and '/login?next=' in html and 'CTO' in html)
        r = client.post('/founder/join/%s' % TOKEN)
        check('accepting while signed out redirects to sign-in', r.status_code == 302 and '/login' in r.headers['Location'])
        login(T_ID)
        html = client.get('/founder/join/%s' % TOKEN).get_data(as_text=True)
        check('another account sees which address the invite went to, masked, and no join button',
              'c***@x.com' in html and 'Join as' not in html, html[-600:])
        r = client.post('/founder/join/%s' % TOKEN)
        check('a forwarded link cannot be used by another account',
              r.status_code == 200 and founder_ventures.get_member(venture(), T_ID) is None
              and not founder_feature.is_enabled(u(T_ID)) and 'c***@x.com' in r.get_data(as_text=True))
        login(C_ID)
        check('the co-founder is not on the allowlist yet', not founder_feature.is_enabled(u(C_ID)))
        r = client.post('/founder/join/%s' % TOKEN)
        db.session.refresh(inv)
        check('accepting links the account and opens the alignment page',
              r.status_code == 302 and r.headers['Location'].endswith('/founder/v/%s/alignment' % V_ID)
              and inv.status == 'active' and inv.user_id == C_ID and inv.joined_at is not None, r.headers.get('Location'))
        check('an accepted invite grants the co-founder early access', founder_feature.is_enabled(u(C_ID)))
        from app.models.mail_crm import UserFeatureOverride
        ov = UserFeatureOverride.query.filter_by(user_id=C_ID, feature_key='founder_track').first()
        check('the access is tagged with the venture and inviter, and audit-logged',
              ov is not None and V_ID in (ov.reason or '') and ov.set_by == F_ID
              and AuditLog.query.filter_by(action='founder_access_via_invite', resource_id=V_ID).count() == 1)
        check('the join is audit-logged', AuditLog.query.filter_by(action='founder_cofounder_joined').count() == 1)
        login(T_ID)
        r = client.get('/founder/join/%s' % TOKEN)
        check('a used token says so (410)', r.status_code == 410 and 'already been used' in r.get_data(as_text=True))
        r = client.post('/founder/join/%s' % TOKEN)
        check('a used token cannot be accepted again', r.status_code == 410)
        check('the third user did not join', founder_ventures.get_member(venture(), T_ID) is None)
        exp = VentureMember(venture_id=V_ID, invite_email='late@x.com', invite_token='expiredtoken123', status='invited',
                            role='cofounder', created_at=now - timedelta(days=fas.invite_days() + 1))
        db.session.add(exp)
        db.session.commit()
        r = client.post('/founder/join/expiredtoken123')
        check('an expired token is refused (410)', r.status_code == 410 and 'expired' in r.get_data(as_text=True)
              and founder_ventures.get_member(venture(), T_ID) is None)
        blocked = user('blocked@x.com')
        founder_feature.set_user_access([blocked.id], 'blocked', A_ID)
        exp.created_at, exp.invite_email = now, 'blocked@x.com'
        db.session.commit()
        try:
            fas.accept_invite('expiredtoken123', blocked)
            ok = False
        except fas.AlignmentError:
            ok = True
        check('an account an admin blocked cannot join through an invite', ok and exp.status == 'invited')
        login(C_ID)
        r = client.post(A + '/invite', json={'email': 'someone@x.com'})
        check('a co-founder (not the owner) cannot invite', r.status_code == 400 and 'owner' in r.get_json()['error'])

        print('\n7. Answers stay separate until both answered')
        login(F_ID)
        fanswers = {'vision': {'choice': 'venture_scale', 'note': 'FOUNDER-SECRET-NOTE'},
                    'roles': {'choice': 'agreed'}, 'time': {'choice': 'all_full'},
                    'equity': {'choice': 'equal_vest'}, 'decisions': {'choice': 'consensus'},
                    'leaving': {'choice': 'return_unvested'}}
        r = client.post(A + '/answers', json={'answers': {'vision': {'choice': 'nonsense'}}})
        check('an unknown option is refused', r.status_code == 400)
        r = client.post(A + '/answers', json={'answers': {'mood': {'choice': 'x'}}})
        check('an unknown question is refused', r.status_code == 400)
        r = client.post(A + '/answers', json={'answers': fanswers})
        check('the founder saves their answers', r.status_code == 200 and r.get_json()['steps_created'] == 0)
        login(C_ID)
        view = client.get(A).get_json()['view']
        check('the co-founder sees no founder answers yet',
              all(not q['revealed'] and q['answers'] == [] and q['mine'] is None for q in view['questions']))
        html = client.get('/founder/v/%s/alignment' % V_ID).get_data(as_text=True)
        check("the founder's note is not in the co-founder's page", 'FOUNDER-SECRET-NOTE' not in html
              and 'Alignment check' in html)
        r = client.post(A + '/answers', json={'answers': {'vision': {'choice': 'sell', 'note': 'CO-NOTE'}}})
        view = r.get_json()['view']
        vq = next(q for q in view['questions'] if q['key'] == 'vision')
        rq = next(q for q in view['questions'] if q['key'] == 'roles')
        check('a question both answered is revealed with both answers',
              vq['revealed'] and vq['status'] == 'differ' and {a['note'] for a in vq['answers']} ==
              {'FOUNDER-SECRET-NOTE', 'CO-NOTE'})
        check('a question only one answered stays hidden', not rq['revealed'] and rq['answers'] == []
              and rq['answered'] == 1 and rq['total'] == 2)
        login(F_ID)
        fview = client.get(A).get_json()['view']
        check("the founder can't see the co-founder's answer to a question they haven't both answered",
              all(q['answers'] == [] for q in fview['questions'] if q['key'] != 'vision'))

        print('\n8. Differences become plan steps; equity goes to a lawyer')
        steps = VenturePlanStep.query.filter_by(venture_id=V_ID).all()
        check('the vision difference became one alignment step',
              len(steps) == 1 and steps[0].kind == 'alignment' and steps[0].state == 'ready'
              and 'vision' in steps[0].title.lower(), [(s.kind, s.title) for s in steps])
        login(C_ID)
        client.post(A + '/answers', json={'answers': {
            'roles': {'choice': 'agreed'}, 'time': {'choice': 'mixed'}, 'equity': {'choice': 'weighted_vest'},
            'decisions': {'choice': 'consensus'}, 'leaving': {'choice': 'return_unvested'}}})
        steps = VenturePlanStep.query.filter_by(venture_id=V_ID).all()
        kinds = sorted((s.kind, s.title) for s in steps)
        check('differences on vision and time are alignment steps',
              sum(1 for s in steps if s.kind == 'alignment') == 2, kinds)
        legal = [s for s in steps if s.kind == 'legal']
        check('the equity difference is a legal step that sends them to a lawyer',
              len(legal) == 1 and 'lawyer' in legal[0].title.lower() and 'lawyer' in legal[0].description.lower()
              and 'equity' in legal[0].title.lower(), kinds)
        check('matching answers make no step', not any('roles' in s.title.lower() or 'decision' in s.title.lower()
                                                       or 'leaves' in s.title.lower() for s in steps))
        check('the step text takes no sides', all('does not take sides' in (s.description or '') for s in steps))
        check('steps sit on the team layer with step events', all(s.layer == fas.TEAM_LAYER for s in steps)
              and all(VentureStepEvent.query.filter_by(plan_step_id=s.id).count() >= 1 for s in steps))
        client.post(A + '/answers', json={'answers': {'time': {'choice': 'mixed', 'note': 'still mixed'}}})
        check('re-saving answers does not duplicate steps',
              VenturePlanStep.query.filter_by(venture_id=V_ID).count() == 3)
        cmp_ = fas.compare(venture())
        check('compare shows match / differ per question',
              cmp_ == {'vision': 'differ', 'roles': 'match', 'time': 'differ', 'equity': 'differ',
                       'decisions': 'match', 'leaving': 'match'}, cmp_)
        html = client.get('/founder/v/%s/alignment' % V_ID).get_data(as_text=True)
        check('the page shows match and differ, and the lawyer note', 'You match' in html and 'You differ' in html
              and 'see a lawyer' in html and 'FOUNDER-SECRET-NOTE' in html)
        client.post(A + '/answers', json={'answers': {'leaving': {'choice': 'keep_all'}}})
        check('a later difference on someone leaving is a legal step too',
              VenturePlanStep.query.filter_by(venture_id=V_ID, kind='legal').count() == 2)
        r = client.post(A + '/answers', json={'answers': {'vision': {'choice': 'venture_scale'}}})
        row = AlignmentResponse.query.filter_by(member_id=founder_ventures.get_member(venture(), C_ID).id,
                                                question_key='vision').first()
        stored = json.loads(row.answer)
        check('a change after the reveal keeps the first-check answer',
              r.status_code == 200 and stored['choice'] == 'venture_scale' and stored['first_choice'] == 'sell'
              and stored['changes_after_reveal'] == 1 and r.get_json()['now_match'] == ['vision'], stored)
        rates = fas.agreement_rates(venture())
        check('agreement is reported at the first check and now (leaving matched first, vision matches now)',
              rates == {'questions': 6, 'match_first_check': 3, 'match_now': 3}, rates)
        vstep = VenturePlanStep.query.filter(VenturePlanStep.venture_id == V_ID,
                                             VenturePlanStep.title.ilike('%vision%')).first()
        check('the open vision step is told the answers now match (step stays for the founders to close)',
              vstep.state not in ('done', 'skipped') and any('now match' in (e.note or '') for e in
                                                             VentureStepEvent.query.filter_by(plan_step_id=vstep.id)))
        client.post(A + '/answers', json={'answers': {'vision': {'choice': 'sell'}}})
        check('moving back to differ while the step is open adds no duplicate',
              VenturePlanStep.query.filter(VenturePlanStep.venture_id == V_ID,
                                           VenturePlanStep.title.ilike('%vision%')).count() == 1)

        print('\n9. Decision rights')
        owner_m = founder_ventures.get_member(venture(), F_ID)
        co_m = founder_ventures.get_member(venture(), C_ID)
        r = client.post(A + '/rights', json={'decision_type': 'spend', 'member_id': co_m.id})
        check('a co-founder who does not hold a decision cannot reassign it', r.status_code == 403)
        login(F_ID)
        r = client.post(A + '/rights', json={'decision_type': 'nope', 'member_id': co_m.id})
        check('an unknown decision type is refused', r.status_code == 400)
        r = client.post(A + '/rights', json={'decision_type': 'spend', 'member_id': 'NOTAMEMBR'})
        check('a non-member holder is refused', r.status_code == 400)
        r = client.post(A + '/rights', json={'decision_type': 'spend', 'member_id': co_m.id})
        check('the owner gives the spend decision to the co-founder',
              r.status_code == 200 and fas.holder_for(venture(), 'spend').id == co_m.id)
        check('only one spend row exists', DecisionRight.query.filter_by(venture_id=V_ID, decision_type='spend').count() == 1)
        login(C_ID)
        r = client.post(A + '/rights', json={'decision_type': 'hiring', 'member_id': co_m.id})
        check('the co-founder still cannot take a decision the owner holds', r.status_code == 403
              and fas.holder_for(venture(), 'hiring').id == owner_m.id)
        r = client.post(A + '/rights', json={'decision_type': 'spend', 'member_id': owner_m.id})
        check('the current holder can hand it on', r.status_code == 200
              and fas.holder_for(venture(), 'spend').id == owner_m.id)
        check('reassignments are audit-logged',
              AuditLog.query.filter_by(action='founder_decision_right_changed').count() == 2)
        r = client.post(A + '/rights', json={'decision_type': 'layer_lead', 'layer': 1, 'member_id': co_m.id})
        check("a co-founder can't name themselves lead of a layer the owner leads", r.status_code == 403)
        login(F_ID)
        r = client.post(A + '/rights', json={'decision_type': 'layer_lead', 'member_id': co_m.id})
        check('a layer lead needs a layer', r.status_code == 400)
        r = client.post(A + '/rights', json={'decision_type': 'layer_lead', 'layer': 3, 'member_id': co_m.id})
        leads = {l['layer']: l for l in (r.get_json() or {}).get('leads', [])}
        check('the owner names the co-founder lead of L3 Sell', r.status_code == 200
              and leads[3]['holder_member_id'] == co_m.id and leads[3]['named']
              and leads[1]['holder_member_id'] == owner_m.id and not leads[1]['named'], leads)
        r = client.post(A + '/rights', json={'decision_type': 'customer_contact', 'layer': 3, 'member_id': co_m.id})
        rights = r.get_json()['rights']
        check('a layer-specific right is listed with its layer, and routes approvals',
              any(x['layer'] == 3 and x['decision_type'] == 'customer_contact' and x['holder_member_id'] == co_m.id
                  for x in rights) and fas.holder_for(venture(), 'customer_contact', 3).id == co_m.id
              and fas.holder_for(venture(), 'customer_contact').id == owner_m.id)
        html = client.get('/founder/v/%s/roles' % V_ID).get_data(as_text=True)
        check('the roles page shows layer leads and the layer-only rights',
              'Who leads each layer' in html and 'L3 Sell' in html and 'For one layer only' in html)
        login(C_ID)

        print('\n10. Step owners, hand-offs and check-ins')
        step = VenturePlanStep.query.filter_by(venture_id=V_ID, kind='alignment').first()
        login(F_ID)
        prev_owner = step.owner_member_id
        r = client.post(A + '/steps/%s/owner' % step.id, json={'member_id': ''})
        db.session.refresh(step)
        check('a step owner cannot be cleared (every step has one owner)',
              r.status_code == 400 and step.owner_member_id == prev_owner)
        step.owner_member_id = None
        db.session.commit()
        login(C_ID)
        r = client.post(A + '/steps/%s/owner' % step.id, json={'member_id': co_m.id})
        db.session.refresh(step)
        check('a member takes an unowned step', r.status_code == 200 and step.owner_member_id == co_m.id)
        login(F_ID)
        client.post(A + '/steps/%s/owner' % step.id, json={'member_id': owner_m.id})
        login(C_ID)
        r = client.post(A + '/steps/%s/owner' % step.id, json={'member_id': co_m.id})
        check("a co-founder can't take a step someone else owns", r.status_code == 403)
        login(F_ID)
        r = client.post(A + '/steps/%s/owner' % step.id, json={'member_id': co_m.id})
        db.session.refresh(step)
        ev = [json.loads(e.note) for e in VentureStepEvent.query.filter_by(plan_step_id=step.id)
              .order_by(VentureStepEvent.created_at.asc()).all() if 'step_owner_changed' in (e.note or '')]
        check('the step owner hands it on and every hand-off is a step event',
              r.status_code == 200 and step.owner_member_id == co_m.id and len(ev) >= 3
              and any(e.get('to_member_id') == co_m.id and e.get('from_member_id') == owner_m.id for e in ev), ev)
        check('hand-offs are audit-logged', AuditLog.query.filter_by(action='founder_step_handoff').count() >= 3)
        r = client.post(A + '/cadence', json={'days': 400})
        check('an out-of-range cadence is refused', r.status_code == 400)
        r = client.post(A + '/cadence', json={'days': 14})
        check('the cadence is saved on the venture', r.status_code == 200 and venture().checkin_cadence_days == 14)
        step.state, step.completed_at = 'done', datetime.utcnow()
        db.session.add(VenturePlanStep(venture_id=V_ID, layer=1, kind='manual', title='Locked thing', state='locked'))
        db.session.add(VenturePlanStep(venture_id=V_ID, layer=3, kind='agent', title='Approve pilot email',
                                       state='awaiting_approval'))
        db.session.commit()
        s = fas.checkin_summary(venture())
        check('the summary lists done, blocked and pending decisions',
              [x['title'] for x in s['done']] == [step.title] and [x['title'] for x in s['blocked']] == ['Locked thing']
              and 'Approve pilot email' in [x['title'] for x in s['pending']]
              and any(x['kind'] == 'legal' for x in s['pending']), s)
        r = client.post(A + '/checkin')
        s = r.get_json()['checkin']
        check('a check-in is recorded and the next one is due by the cadence',
              r.status_code == 200 and s['last_checkin'] and s['next_due'] and not s['overdue'] and s['done'] == [])
        html = client.get('/founder/v/%s/roles' % V_ID).get_data(as_text=True)
        check('the roles page renders decision rights and the check-in view',
              'Who decides' in html and 'Check-ins' in html and 'Decisions pending' in html
              )
        ahtml = client.get('/founder/v/%s/alignment' % V_ID).get_data(as_text=True)
        check('the alignment page is one task, with links back to the workspace and plan',
              'Who decides' not in ahtml and 'Alignment check' in ahtml
              and '/founder/v/%s"' % V_ID in ahtml and '/founder/v/%s/plan' % V_ID in ahtml)

        print('\n11. Proof plan: criteria set in advance')
        login(C_ID)
        r = client.post(P, json={'customer_name': 'Smile Co', 'pain': 'No-shows', 'success_criteria': [
            {'metric': 'No-show rate'}]})
        check('a criterion without target and method is refused', r.status_code == 400)
        plan_body = {'customer_name': 'Smile Co', 'pain': 'No-shows cost two hours a day',
                     'baseline': '18% of appointments are no-shows', 'smallest_build': 'SMS reminders for one clinic',
                     'duration': '4 weeks', 'on_success': 'Paid pilot at $500 per month',
                     'provides': {'founder': ['Reminder service'], 'customer': ['Appointment export']},
                     'success_criteria': [
                         {'metric': 'No-show rate', 'kind': 'cost', 'baseline': '18%', 'target': 'under 10%',
                          'how_measured': 'Clinic booking system'},
                         {'metric': 'Front-desk time on calls', 'kind': 'time', 'baseline': '2 hours a day',
                          'target': 'under 1 hour a day', 'how_measured': 'Staff time log'},
                         {'metric': '', 'target': '', 'how_measured': ''}]}
        r = client.post(P, json=dict(plan_body, customer_name=''))
        check('a draft can be saved before it is complete', r.status_code == 200)
        PLAN = r.get_json()['plan_id']
        plan = db.session.get(ProofPlan, PLAN)
        check('empty criterion rows are dropped and criteria get ids',
              [c['id'] for c in plan.success_criteria] == ['c1', 'c2'])
        r = client.post(P, json=plan_body)
        check('a second draft is refused while one is open', r.status_code == 400)
        r = client.post(P + '/%s/results' % PLAN, json={'results': [{'criterion_id': 'c1', 'value': '9%'}]})
        check('results before the plan is agreed are refused', r.status_code == 400
              and 'in advance' in r.get_json()['error'])
        r = client.post(P + '/%s/agree' % PLAN)
        check('agreeing needs a named customer', r.status_code == 400 and 'the customer' in r.get_json()['error'])
        r = client.post(P, json=dict(plan_body, plan_id=PLAN))
        check('the draft is updated in place', r.status_code == 200 and r.get_json()['plan_id'] == PLAN)
        check('the venture is still at rung 1', venture().rung == 1)
        r = client.post(P + '/%s/agree' % PLAN)
        db.session.refresh(plan)
        check('the plan is agreed', r.status_code == 200 and plan.status == 'agreed' and plan.agreed_at is not None)
        check('rung 1 does not jump to 3 on an agreed plan', venture().rung == 1)
        r = client.post(P, json=dict(plan_body, plan_id=PLAN, success_criteria=[
            {'metric': 'Anything', 'target': 'x', 'how_measured': 'y'}]))
        check('agreed criteria cannot change', r.status_code == 400 and 'fixed' in r.get_json()['error'])
        fes.set_rung(venture(), 2, 'Customers confirmed the pain (test)', user_id=A_ID)
        html = client.get('/founder/v/%s/proof' % V_ID).get_data(as_text=True)
        check('viewing the proof page changes nothing; it offers to update the ladder',
              venture().rung == 2 and 'id="fp-sync"' in html)
        r = client.post(P + '/sync')
        html = client.get('/founder/v/%s/proof' % V_ID).get_data(as_text=True)
        check('at rung 2 the agreed plan moves the venture to rung 3 (one step)', r.status_code == 200
              and r.get_json()['reached'] == [3] and venture().rung == 3
              and 'rung 3 of 5' in html and 'Smile Co' in html and 'id="fp-sync"' not in html)

        print('\n12. Results, benefits case and quote permission')
        r = client.post(P + '/%s/results' % PLAN, json={'results': [
            {'criterion_id': 'c1', 'value': '9%', 'basis': 'estimate', 'met': '1'},
            {'criterion_id': 'c2', 'value': 'about 1.5 hours', 'basis': 'estimate'}],
            'quote_text': 'Our front desk finally breathes', 'quote_permission': False})
        res = ProofResult.query.filter_by(proof_plan_id=PLAN).first()
        check('estimates are recorded but are not measured evidence', r.status_code == 200 and not res.measured
              and venture().rung == 3)
        check('a plan with only estimates stays agreed, not complete',
              db.session.get(ProofPlan, PLAN).status == 'agreed')
        r = client.post(P + '/%s/benefits' % PLAN)
        check('the benefits case refuses unmeasured results', r.status_code == 400
              and 'measured' in r.get_json()['error'])
        r = client.post(P + '/%s/payment' % PLAN, json={'kind': 'paid'})
        check('a payment is refused before measured results', r.status_code == 400 and venture().rung == 3)
        r = client.post(P + '/%s/results' % PLAN, json={'results': [
            {'criterion_id': 'c1', 'value': '9%', 'basis': 'measured', 'met': '1'},
            {'criterion_id': 'c2', 'value': 'about 1.5 hours', 'basis': 'estimate', 'met': '0'}],
            'quote_text': 'Our front desk finally breathes', 'quote_permission': False})
        check('one criterion still estimated: no rung 4', r.status_code == 200 and venture().rung == 3)
        r = client.post(P + '/%s/benefits' % PLAN)
        text = (r.get_json() or {}).get('benefits_text', '')
        check('the benefits case uses the measured result', r.status_code == 200
              and 'Measured results' in text and 'before 18%, after 9%' in text, text)
        check('the estimate is labelled as an estimate, apart from measured results',
              'Estimates (not measured)' in text and 'about 1.5 hours (estimate)' in text
              and text.index('Estimates') > text.index('before 18%'), text)
        check('the quote is left out without permission', 'front desk finally breathes' not in text
              and fps.quote_for_use(fps.latest_result(db.session.get(ProofPlan, PLAN))) is None)
        r = client.post(P + '/%s/results' % PLAN, json={'results': [{'criterion_id': 'c9', 'value': '1'}]})
        check('a result for a criterion not in the plan is refused', r.status_code == 400)
        r = client.post(P + '/%s/results' % PLAN, json={'results': [
            {'criterion_id': 'c1', 'value': '9%', 'basis': 'measured', 'met': '1'},
            {'criterion_id': 'c2', 'value': '50 minutes a day', 'basis': 'measured', 'met': '1'}],
            'quote_text': 'Our front desk finally breathes', 'quote_permission': True})
        check('measured results for every criterion move the venture to rung 4', r.status_code == 200
              and venture().rung == 4 and db.session.get(ProofPlan, PLAN).status == 'complete')
        r = client.post(P + '/%s/results' % PLAN, json={'results': [
            {'criterion_id': 'c1', 'value': '8%', 'basis': 'measured'},
            {'criterion_id': 'c2', 'value': 'maybe 40 minutes', 'basis': 'estimate'}]})
        check('a fully measured result is not replaced by estimates', r.status_code == 400
              and fps.latest_result(db.session.get(ProofPlan, PLAN)).measured)
        r = client.post(P + '/%s/benefits' % PLAN)
        text = r.get_json()['benefits_text']
        check('with permission, the quote appears in the benefits case',
              '"Our front desk finally breathes" (Smile Co)' in text and 'Estimates' not in text, text)
        html = client.get('/founder/v/%s/proof' % V_ID).get_data(as_text=True)
        check('the proof page shows the benefits case and the payment form',
              'Benefits case' in html and 'id="fp-pay"' in html and 'All criteria measured' in html)

        print('\n13. Paid: rung 5, step by step, never a jump')
        r = client.post(P + '/%s/payment' % PLAN, json={'kind': 'bribe'})
        check('an unknown payment kind is refused', r.status_code == 400)
        r = client.post(P + '/%s/payment' % PLAN, json={'kind': 'commitment', 'note': 'Signed LOI'})
        check('a signed commitment moves the venture to rung 5', r.status_code == 200 and venture().rung == 5)
        r = client.post(P + '/%s/payment' % PLAN, json={'kind': 'paid'})
        check('a second payment on the same result is refused', r.status_code == 400)
        r = client.post(P + '/%s/results' % PLAN, json={'results': [{'criterion_id': 'c1', 'value': '1%',
                                                                       'basis': 'measured'}]})
        check('results behind a recorded payment cannot be replaced', r.status_code == 400)
        notes = [(a.extra or {}).get('note', '') for a in AuditLog.query.filter_by(
            action='founder_rung_changed', resource_id=V_ID).order_by(AuditLog.created_at.asc()).all()]
        moves = [n.split(':')[0] for n in notes]
        check('every rung change moved exactly one rung and was audit-logged',
              moves == ['Rung 1 -> 2', 'Rung 2 -> 3', 'Rung 3 -> 4', 'Rung 4 -> 5'], moves)
        html = client.get('/founder/v/%s/proof' % V_ID).get_data(as_text=True)
        check('the proof page renders the complete state and the not-advice note',
              'rung 5 of 5' in html and 'Run another proof of concept' in html
              and legal_text.get('founder_not_advice')[:40] in html)
        r = client.post(P, json=dict(plan_body, customer_name='Second Clinic'))
        PLAN_B = (r.get_json() or {}).get('plan_id')
        r2 = client.post(P, json=dict(plan_body, customer_name='Second Clinic'))
        check('a second plan can start once the first has results; a double submit makes no second draft',
              r.status_code == 200 and r2.status_code == 400
              and ProofPlan.query.filter_by(venture_id=V_ID, status='draft').count() == 1)
        html = client.get('/founder/v/%s/proof' % V_ID).get_data(as_text=True)
        check('the page lists every plan', 'Your proof-of-concept plans' in html and 'Second Clinic' in html
              and '?plan=%s' % PLAN in html)
        html = client.get('/founder/v/%s/proof?plan=%s' % (V_ID, PLAN)).get_data(as_text=True)
        check('an earlier plan opens by id', 'data-plan="%s"' % PLAN in html and 'Recorded' in html)
        html = client.get('/founder/v/%s/proof?plan=NOPE' % V_ID).get_data(as_text=True)
        check('an unknown plan id falls back to the newest', 'data-plan="%s"' % PLAN_B in html)

        print('\n13b. Odd request bodies are 400s, not 500s')
        r = client.post(P, json=['not', 'a', 'dict'])
        check('a JSON list body', r.status_code in (200, 400))
        r = client.post(P, json={'plan_id': PLAN_B, 'success_criteria': [
            {'metric': 'm', 'target': 't', 'how_measured': 'h', 'kind': ['x']}]})
        check('a non-string criterion kind', r.status_code == 200)
        r = client.post(P, json={'plan_id': ['x']})
        check('a non-string plan id', r.status_code == 400)
        r = client.post(P + '/%s/payment' % PLAN, json={'kind': ['paid']})
        check('a non-string payment kind', r.status_code == 400)
        r = client.post(A + '/rights', json={'decision_type': ['spend'], 'member_id': {'a': 1}})
        check('non-string decision type and member id', r.status_code == 400)
        r = client.post(A + '/invite', json={'email': ['x@y.com']})
        check('a non-string email', r.status_code == 400)
        r = client.post(A + '/answers', json='just a string')
        check('a JSON string body', r.status_code == 400)

        print('\n14. sync_rung never skips a rung')
        u2 = user('solo@x.com')
        founder_feature.set_user_access([u2.id], 'allowed', A_ID)
        v2 = founder_ventures.create_venture(u2, 'Solo Co', '', 'idea')
        v2.paid_at = now
        p2 = ProofPlan(venture_id=v2.id, customer_name='Big Client', success_criteria=[
            {'id': 'c1', 'metric': 'm', 'target': 't', 'how_measured': 'h', 'kind': 'other', 'baseline': ''}],
            status='complete', agreed_at=now)
        db.session.add(p2)
        db.session.flush()
        db.session.add(ProofResult(venture_id=v2.id, proof_plan_id=p2.id, measured=True, paid_at=now,
                                   results=[{'criterion_id': 'c1', 'value': 'v', 'basis': 'measured'}]))
        db.session.commit()
        check('below rung 2, full evidence still does not move the rung',
              fps.sync_rung(v2) == [] and v2.rung == 1)
        try:
            from app.services import founder_orchestrator as orch
            from app.models.founder import AgentBlueprint
            db.session.add(AgentBlueprint(venture_id=v2.id, agent_key='fnd_problem_statement', layer=1,
                                          status='accepted', params={}))
            db.session.commit()
            orch.build_plan(v2, u2)
            for st in orch.steps(v2):
                if st.kind == 'agent':
                    st.state = 'skipped'
            db.session.commit()
            orch.refresh(v2)
        except (ImportError, AttributeError):
            orch = None
        fes.set_rung(v2, 2, 'test', user_id=A_ID)
        reached = fps.sync_rung(v2)
        check('from rung 2 it climbs 3, 4, 5 one call at a time', reached == [3, 4, 5] and v2.rung == 5, reached)
        if orch is not None:
            agree_step = orch.proof_step(v2, 'agree')
            run_step = orch.proof_step(v2, 'run')
            check("the plan's open proof steps are marked done with the evidence (WP4 present)",
                  agree_step is not None and agree_step.state == 'done' and run_step.state == 'done'
                  and orch.proof_step(v2, 'paid').state == 'done'
                  and 'rung' in (agree_step.evidence_note or ''),
                  [(x.title, x.state) for x in orch.steps(v2) if x.kind == 'proof'])
        v3 = founder_ventures.create_venture(other, 'Other Co', '', 'idea')
        fes.set_rung(v3, 2, 'test', user_id=A_ID)
        p3 = ProofPlan(venture_id=v3.id, customer_name='Client', status='complete', agreed_at=now,
                       success_criteria=[{'id': 'c1', 'metric': 'm', 'target': 't', 'how_measured': 'h'}])
        db.session.add(p3)
        db.session.flush()
        db.session.add(ProofResult(venture_id=v3.id, proof_plan_id=p3.id, measured=False, paid_at=now,
                                   results=[{'criterion_id': 'c1', 'value': 'v', 'basis': 'estimate'}]))
        db.session.commit()
        check('a payment without measured results stops at rung 3', fps.sync_rung(v3) == [3] and v3.rung == 3)
        saved = fes.set_rung
        try:
            del fes.set_rung
            v4 = founder_ventures.create_venture(third, 'Fallback Co', '', 'idea')
            v4.rung = 2
            db.session.add(ProofPlan(venture_id=v4.id, customer_name='C', status='agreed', agreed_at=now,
                                     success_criteria=[{'id': 'c1', 'metric': 'm', 'target': 't', 'how_measured': 'h'}]))
            db.session.commit()
            reached = fps.sync_rung(v4)
            fallback_ok = reached == [3] and v4.rung == 3 and AuditLog.query.filter_by(
                action='founder_rung_changed', resource_id=v4.id).count() == 1
        finally:
            fes.set_rung = saved
        check('without set_rung, the guarded fallback writes the rung and an audit row', fallback_ok)

        print('\n14b. An earlier plan can still be paid after a newer one starts')
        u6 = user('six@x.com')
        v6 = founder_ventures.create_venture(u6, 'Six Co', '', 'idea')
        crit = [{'id': 'c1', 'metric': 'm', 'target': 't', 'how_measured': 'h', 'kind': 'other', 'baseline': ''}]
        pa = ProofPlan(venture_id=v6.id, customer_name='A Co', status='agreed', agreed_at=now, success_criteria=crit,
                       pain='p', baseline='b', smallest_build='s', duration='d', on_success='o')
        db.session.add(pa)
        db.session.commit()
        fps.record_result(v6, u6, pa.id, [{'criterion_id': 'c1', 'value': '5', 'basis': 'measured'}])
        pb = fps.save_plan(v6, u6, {'customer_name': 'B Co'})
        fps.record_payment(v6, u6, pa.id, 'paid')
        check('payment on plan A is recorded while plan B is the newest',
              fps.current_plan(v6).id == pb.id and fps.best_result(pa).paid_at is not None)

        print('\n14c. A difference that comes back after its step is done makes a new step')
        u7, u8 = user('seven@x.com'), user('eight@x.com')
        v7 = founder_ventures.create_venture(u7, 'Seven Co', '', 'idea')
        db.session.add(VentureMember(venture_id=v7.id, user_id=u8.id, role='cofounder', status='active',
                                     joined_at=now, created_at=now))
        db.session.commit()
        fas.submit_answers(v7, u7, {'roles': {'choice': 'written'}})
        fas.submit_answers(v7, u8, {'roles': {'choice': 'open'}})
        st = VenturePlanStep.query.filter_by(venture_id=v7.id, kind='alignment').all()
        st[0].state = 'done'
        db.session.commit()
        fas.submit_answers(v7, u8, {'roles': {'choice': 'open', 'note': 'still open'}})
        check('a done step is not re-created while nothing changed',
              VenturePlanStep.query.filter_by(venture_id=v7.id, kind='alignment').count() == 1)
        fas.submit_answers(v7, u8, {'roles': {'choice': 'written'}})
        fas.submit_answers(v7, u8, {'roles': {'choice': 'agreed'}})
        check('a new difference after the step was done makes a new step',
              VenturePlanStep.query.filter_by(venture_id=v7.id, kind='alignment').count() == 2)

        print('\n15. Solo venture proof page and a co-founder (non-owner) view')
        login(u2.id)
        html = client.get('/founder/v/%s/proof' % v2.id).get_data(as_text=True)
        check('the proof page renders for a venture with no open plan', 'Proof-of-concept plan' in html
              or 'Run another proof of concept' in html)
        r = client.get('/founder/v/%s/alignment' % v2.id)
        check('the alignment page renders for another solo founder', r.status_code == 200)
        login(F_ID)
        check('responses are stored one row per member and question',
              AlignmentResponse.query.filter_by(venture_id=V_ID).count() == 12)

    print('\n%d passed, %d failed' % (len(PASS), len(FAIL)))
    if FAIL:
        print('FAILED: ' + '; '.join(FAIL))
        sys.exit(1)


if __name__ == '__main__':
    main()
