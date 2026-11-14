"""Founder Track end to end (FOUNDER_PLAN.md, integration of WP0-WP7).

One founder journey through the real routes, with Claude, Stripe and SendGrid
faked and the network blocked:

  create a venture -> upload a deck -> evaluate -> pay (fake webhook) -> build
  the team -> override a parameter -> build the plan -> one next step -> run
  an agent step (output + trace) -> complete it, unlocking its dependent ->
  run a customer-facing agent -> approve its draft (nothing is sent) ->
  invite and accept a co-founder -> both answer alignment -> a difference
  becomes a plan step -> agree a proof plan -> rung 3 -> draft and publish the
  startup page (decision right) -> view it publicly -> request an intro
  (Contact created) -> the runs show in the admin observability Founder tab
  -> flag off: everything 404s and existing pages are unchanged.

In-memory SQLite. Never starts a server.

    python test_founder_flow.py
"""
import io
import os
import re
import sys
import tempfile
from datetime import datetime
from types import SimpleNamespace

os.environ.setdefault('DATABASE_URL', 'sqlite:///:memory:')
os.environ.setdefault('SECRET_KEY', 'test-only')
ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

PASS, FAIL = [], []

DECK = ("Acme Dental helps dental clinics cut no-shows.\n"
        "Clinics lose $40,000 a year to missed appointments.\n"
        "Three clinic managers told us no-shows cost us two hours a day.\n"
        "Our team is two engineers and no one in sales.\n"
        "We will charge $199 per clinic per month.")


def check(label, condition, detail=''):
    (PASS if condition else FAIL).append(label)
    line = '  {} {}{}'.format('ok  ' if condition else 'FAIL', label, ('  -- ' + str(detail)) if detail else '')
    enc = sys.stdout.encoding or 'ascii'
    print(line.encode(enc, 'replace').decode(enc))


def make_pdf(text):
    import fitz
    doc = fitz.open()
    page = doc.new_page()
    page.insert_textbox(fitz.Rect(40, 40, 560, 800), text, fontsize=10)
    data = doc.tobytes()
    doc.close()
    return data


class FakeClaude(object):
    """One fake for every founder model call. The forced tool decides the
    answer: evaluate_dimension (evaluation) or deliver_output (agents)."""

    def __init__(self):
        self.calls = []
        self.messages = self

    def create(self, **kw):
        tool = kw['tools'][0]
        name = tool['name']
        self.calls.append(name)
        if name == 'evaluate_dimension':
            dim = re.search(r'dimension "([a-z_]+)"', kw['messages'][0]['content'][1]['text']).group(1)
            out = self.evaluate(dim)
        else:
            label = re.match(r'You are the (.+?) agent', kw['system']).group(1)
            out = {'content': '## %s\nWork based on the deck (pitch_deck@v1).' % label, 'confidence': 'medium',
                   'assumptions': ['Clinics will talk to us']}
            if 'drafts' in tool['input_schema']['properties']:
                out['drafts'] = [{'target': 'Clinic manager', 'subject': 'Three questions about no-shows',
                                  'body': 'Hi, could we ask you three questions about missed appointments?'}]
        block = SimpleNamespace(type='tool_use', id='tu_%d' % len(self.calls), name=name, input=out)
        return SimpleNamespace(content=[block], usage=SimpleNamespace(input_tokens=900, output_tokens=300))

    @staticmethod
    def evaluate(dim):
        if dim == 'problem':
            return {'covered': True, 'score': 70, 'findings': [
                {'ftype': 'strength', 'severity': 'low', 'title': 'Pain is costed',
                 'detail': 'The deck puts a number on the pain.',
                 'citation_text': 'Clinics lose $40,000 a year to missed appointments', 'confidence': 0.8},
                {'ftype': 'gap', 'severity': 'high', 'title': 'Pain is not confirmed by customers',
                 'detail': 'Only three managers were asked.',
                 'citation_text': 'Three clinic managers told us', 'confidence': 0.7,
                 'recommended_agent': 'fnd_pain_evidence'}]}
        if dim == 'customer':
            return {'covered': True, 'score': 55, 'findings': [],
                    'ladder_evidence': [{'rung': 2, 'citation_text': 'no-shows cost us two hours a day'}]}
        if dim == 'team':
            return {'covered': True, 'score': 30, 'findings': [
                {'ftype': 'gap', 'severity': 'medium', 'title': 'No one sells',
                 'detail': 'Nobody on the team owns sales.', 'citation_text': 'no one in sales',
                 'confidence': 0.9}]}
        return {'covered': True, 'score': 60, 'findings': []}


def main():
    import requests
    requests.post = requests.get = lambda *a, **k: (_ for _ in ()).throw(AssertionError('network'))

    # SendGrid: record every message, never call out.
    import sendgrid
    SENT = []

    class _FakeSG(object):
        def __init__(self, api_key=None, *a, **k):
            pass

        def send(self, message):
            SENT.append(message.get())
            return type('R', (), {'status_code': 202, 'body': '', 'headers': {}})()
    sendgrid.SendGridAPIClient = _FakeSG

    # Stripe: a fake checkout session and a webhook that trusts the test.
    import stripe
    created = []

    class FakeSession(dict):
        def __getattr__(self, k):
            try:
                return self[k]
            except KeyError:
                raise AttributeError(k)

    def fake_create(**kw):
        sid = 'cs_flow_%d' % (len(created) + 1)
        created.append(kw)
        return FakeSession(id=sid, url='https://checkout.stripe.test/' + sid)
    stripe.checkout.Session.create = staticmethod(fake_create)

    from app import create_app
    from app.extensions import db
    from utils.id_gen import generate_id
    app = create_app('testing')
    tmp_root = tempfile.mkdtemp(prefix='fnd_flow_')
    upload_root = os.path.join(tmp_root, 'uploads')
    os.makedirs(upload_root)
    from cryptography.fernet import Fernet
    app.config.update(SQLALCHEMY_DATABASE_URI='sqlite:///:memory:', WTF_CSRF_ENABLED=False,
                      UPLOAD_FOLDER=upload_root, FOUNDER_FILES_FOLDER=os.path.join(tmp_root, 'founder_private'),
                      ENCRYPTION_KEY=Fernet.generate_key().decode(), EMAIL_PROVIDER='sendgrid',
                      SENDGRID_API_KEY='test', STRIPE_SECRET_KEY='sk_test_fake', STRIPE_WEBHOOK_SECRET='whsec_fake',
                      SERVER_NAME=None)
    from app.blueprints.payments import routes as pay_routes
    next_event = {}
    pay_routes.construct_webhook_event = lambda payload, sig: next_event['e']

    with app.app_context():
        db.create_all()
        from flask import g
        from app.models.agent_plan import ActionApproval
        from app.models.contact import Contact
        from app.models.founder import (AgentBlueprint, AgentParamOverride, EvaluationFinding, EvaluationRun,
                                        FounderRunMeta, FounderVenture, StartupPage, VentureAgentOutput,
                                        VentureArtifact, VentureMember, VentureOutreachAction, VenturePlanStep)
        from app.models.observability import AgentRun
        from app.models.user import User
        from app.services import founder_agent_service as fas
        from app.services import founder_evaluation_service as fes
        from app.services import founder_feature, founder_orchestrator as fo
        now = datetime.utcnow()

        def user(email, name, admin=False):
            u = User(id=generate_id(), email=email, full_name=name, password_hash='x', is_admin=admin,
                     email_verified=True, onboarding_completed_at=now)
            db.session.add(u)
            db.session.commit()
            return u.id

        F_ID = user('fran@x.com', 'Fran Founder')
        C_ID = user('cam@x.com', 'Cam Cofounder')
        M_ID = user('member@x.com', 'Mo Member')            # an ordinary SimiSI member
        A_ID = user('admin@x.com', 'Ada Admin', admin=True)

        fake = FakeClaude()
        fes.AI_CLIENT = fake
        fas.AI_CLIENT = fake
        fes._spawn = lambda run_id, uid, ids: fes._execute(run_id, uid, ids)          # run inline
        fas._spawn = lambda step_id, uid, trigger, inputs, prior=None: fas._run_by_id(step_id, uid, trigger, inputs,
                                                                                    prior)

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

        def text(r):
            return r.get_data(as_text=True)

        def strip_volatile(html):
            html = re.sub(r'name="csrf[-_]token" content="[^"]*"', '', html)
            html = re.sub(r'value="[A-Za-z0-9._-]{30,}"', '', html)
            return re.sub(r'nonce="[^"]*"', '', html)

        # Existing pages before the track has ever been switched on.
        EXISTING = ('/', '/pricing', '/dashboard', '/sitemap.xml')
        baseline = {}
        for uid in (None, M_ID, F_ID):
            login(uid)
            for p in EXISTING:
                r = client.get(p)
                baseline[(uid, p)] = (r.status_code, strip_volatile(text(r)))

        print('\n1. Track off: founder routes 404')
        login(F_ID)
        check('/founder is 404 while the track is off', client.get('/founder').status_code == 404)
        check('creating a venture is 404 while the track is off',
              client.post('/api/founder/ventures', json={'name': 'X'}).status_code == 404)
        founder_feature.set_mode('founder_track', 'on', A_ID)

        print('\n2. Create a venture and upload a deck')
        r = client.post('/api/founder/ventures', json={'name': 'Acme Dental', 'one_liner': 'Fewer no-shows',
                                                       'stage': 'idea'})
        check('the founder creates a venture', r.status_code == 201, (r.status_code, r.get_json()))
        V_ID = r.get_json()['venture_id']
        base, api = '/founder/v/%s' % V_ID, '/api/founder/v/%s' % V_ID

        def venture():
            db.session.expire_all()
            return db.session.get(FounderVenture, V_ID)

        r = client.post(api + '/artifacts', data={'kind': 'pitch_deck', 'file': (io.BytesIO(make_pdf(DECK)), 'deck.pdf')},
                        content_type='multipart/form-data')
        check('the deck uploads', r.status_code in (200, 201), (r.status_code, r.get_json()))
        art = VentureArtifact.query.filter_by(venture_id=V_ID, kind='pitch_deck', is_current=True).first()
        check('the deck text is extracted', art is not None and 'no-shows' in (art.text_content or ''))

        print('\n3. Evaluate')
        r = client.post(api + '/evaluate')
        check('the evaluation runs', r.status_code == 202, (r.status_code, r.get_json()))
        run = fes.latest_run(venture(), done_only=True)
        check('the evaluation finished with findings', run is not None and run.status == 'done'
              and EvaluationFinding.query.filter_by(venture_id=V_ID).count() >= 2,
              [(x.status) for x in EvaluationRun.query.all()])
        check('the documents moved the venture to rung 2', venture().rung == 2, venture().rung)
        html = text(client.get(base + '/report'))
        check('the unpaid report is a preview: finding text is locked',
              'Pain is not confirmed by customers' not in html)
        check('the report links to the venture sections, including the startup page',
              '/founder/v/%s/page"' % V_ID in html and '/founder/v/%s/plan"' % V_ID in html)
        check('the team API is locked until payment (402)', client.post(api + '/team/build').status_code == 402)

        print('\n4. Pay (fake Stripe webhook)')
        r = client.post(base + '/checkout')
        check('checkout redirects to Stripe', r.status_code in (302, 303)
              and r.headers.get('Location', '').startswith('https://checkout.stripe.test/'), r.status_code)
        md = created[-1]['metadata']
        check('the session carries the venture and product', md.get('venture_id') == V_ID
              and md.get('product') == 'founder_track', md)
        session_obj = {'id': 'cs_flow_1', 'payment_status': 'paid', 'amount_total': int(md.get('amount_cents') or 0),
                       'payment_intent': 'pi_flow', 'metadata': dict(md)}
        next_event['e'] = {'type': 'checkout.session.completed', 'data': {'object': session_obj}}
        login(None)
        r = client.post('/api/payments/webhook', data=b'{}', headers={'Stripe-Signature': 'sig'})
        check('the webhook marks the venture paid', r.status_code == 200 and venture().paid_at is not None,
              (r.status_code, venture().paid_at))
        login(F_ID)
        html = text(client.get(base + '/report'))
        check('the paid report shows finding text', 'Pain is not confirmed by customers' in html)

        print('\n5. Team: build, override, accept')
        r = client.post(api + '/team/build')
        bps = r.get_json().get('blueprints') or []
        keys = {b['agent_key'] for b in bps}
        check('the team is built from the findings', r.status_code == 200 and 'fnd_pain_evidence' in keys, keys)
        pain_bp = next(b for b in bps if b['agent_key'] == 'fnd_pain_evidence')
        r = client.post(api + '/team/override', json={'param_key': 'action_cap', 'value': 5,
                                                       'blueprint_id': pain_bp['id']})
        ov = AgentParamOverride.query.filter_by(venture_id=V_ID, param_key='action_cap').first()
        check('a parameter override is saved with the new value', r.status_code == 200 and ov is not None
              and '5' in (ov.new_value or ''), (r.status_code, r.get_json()))
        for layer in sorted({b['layer'] for b in bps}):
            client.post(api + '/team/accept', json={'layer': layer})
        check('the founder accepts the team',
              AgentBlueprint.query.filter_by(venture_id=V_ID, status='accepted').count() == len(bps))
        check('the team page renders', client.get(base + '/team').status_code == 200)

        print('\n6. Plan and the one next step')
        r = client.post(api + '/plan/build')
        check('the plan is built', r.status_code == 200 and r.get_json()['steps'], r.get_json())

        def step(key):
            db.session.expire_all()
            return VenturePlanStep.query.filter_by(venture_id=V_ID, agent_key=key).first()
        ps, pe = step('fnd_problem_statement'), step('fnd_pain_evidence')
        check('the plan includes the catalog prerequisite, ready, and its dependent, locked',
              ps is not None and pe is not None and ps.state == 'ready' and pe.state == 'locked',
              (ps and ps.state, pe and pe.state))
        nxt = client.get(api + '/next').get_json()
        check('the workspace offers one primary next step', nxt.get('primary') is not None
              and isinstance(nxt.get('alternates'), list), nxt)
        html = text(client.get(base))
        check('the workspace renders the next step and links every section',
              all('/founder/v/%s/%s"' % (V_ID, s) in html for s in ('alignment', 'proof', 'page', 'report', 'support',
                                                                     'plan', 'team')))
        check('the member nav offers the Founder Track while it is on', 'href="/founder"' in text(client.get('/dashboard')))

        print('\n7. Run an agent step (output + trace)')
        r = client.post('/api/founder/steps/%s/run' % ps.id)
        check('the run is accepted', r.status_code == 202, (r.status_code, r.get_json()))
        out = VentureAgentOutput.query.filter_by(plan_step_id=ps.id).first()
        check('the agent wrote an output', out is not None and bool(out.content))
        meta = FounderRunMeta.query.filter_by(venture_id=V_ID, agent_key='fnd_problem_statement').first()
        hdr = db.session.get(AgentRun, meta.obs_run_id) if meta else None
        check('the run is traced: founder_run_meta + an F1 agent run', meta is not None and hdr is not None
              and hdr.layer == 'F1' and out.obs_run_id == hdr.id, (meta, hdr and hdr.layer))
        r = client.get('/api/founder/outputs/%s' % out.id)
        check('the founders can read the output', r.status_code == 200 and r.get_json()['output']['content'])

        print('\n8. Complete a step: its dependent unlocks')
        ps = step('fnd_problem_statement')
        if ps.state != 'done':
            r = client.post('/api/founder/steps/%s/complete' % ps.id, json={'evidence_note': 'Reviewed'})
            check('the founder marks the step done', r.status_code == 200, r.get_json())
        check('the problem statement is done', step('fnd_problem_statement').state == 'done')
        check('the pain-evidence step unlocked', step('fnd_pain_evidence').state == 'ready',
              step('fnd_pain_evidence').state)

        print('\n9. A customer-facing agent drafts; approving sends nothing')
        n_sent = len(SENT)
        pe = step('fnd_pain_evidence')
        r = client.post('/api/founder/steps/%s/run' % pe.id)
        check('the customer-facing agent runs', r.status_code == 202, r.get_json())
        drafts = VentureOutreachAction.query.filter_by(venture_id=V_ID, plan_step_id=pe.id).all()
        check('it produced drafts awaiting approval', drafts and all(d.status == 'awaiting_approval' for d in drafts)
              and step('fnd_pain_evidence').state == 'awaiting_approval', [(d.status) for d in drafts])
        r = client.post('/api/founder/actions/%s/approve' % drafts[0].id)
        db.session.expire_all()
        d0 = db.session.get(VentureOutreachAction, drafts[0].id)
        check('the founder approves the draft', r.status_code == 200 and d0.status == 'approved'
              and d0.approved_by == F_ID, r.get_json())
        check('nothing was sent: no email, no sent_at', len(SENT) == n_sent and d0.sent_at is None
              and VentureOutreachAction.query.filter_by(venture_id=V_ID, status='sent').count() == 0)
        check('the approval is recorded', ActionApproval.query.filter_by(target_type='fnd_outreach',
                                                                         target_id=d0.id).count() == 1)
        check('the step settles once every draft is decided', step('fnd_pain_evidence').state == 'done',
              step('fnd_pain_evidence').state)

        print('\n10. Invite and accept a co-founder')
        A = api + '/alignment'
        r = client.post(A + '/invite', json={'email': 'cam@x.com', 'title': 'CTO'})
        check('the owner invites a co-founder (one email)', r.status_code == 201 and len(SENT) == n_sent + 1,
              (r.status_code, r.get_json()))
        inv = VentureMember.query.filter_by(venture_id=V_ID, status='invited').first()
        login(C_ID)
        r = client.post('/founder/join/%s' % inv.invite_token)
        db.session.expire_all()
        check('the co-founder accepts and becomes an active member', r.status_code == 302
              and db.session.get(VentureMember, inv.id).status == 'active', r.status_code)
        check('the co-founder can open the workspace', client.get(base).status_code == 200)

        print('\n11. Alignment: both answer; a difference becomes a plan step')
        same = {'roles': {'choice': 'agreed'}, 'time': {'choice': 'all_full'}, 'equity': {'choice': 'equal_vest'},
                'decisions': {'choice': 'consensus'}, 'leaving': {'choice': 'return_unvested'}}
        login(F_ID)
        r = client.post(A + '/answers', json={'answers': dict(same, vision={'choice': 'venture_scale'})})
        check('the founder answers', r.status_code == 200, r.get_json())
        login(C_ID)
        r = client.post(A + '/answers', json={'answers': dict(same, vision={'choice': 'sell'})})
        check('the co-founder answers', r.status_code == 200, r.get_json())
        al = VenturePlanStep.query.filter_by(venture_id=V_ID, kind='alignment').all()
        check('the vision difference is one alignment step on the plan', len(al) == 1
              and 'vision' in al[0].title.lower() and al[0].state == 'ready', [(s.title, s.state) for s in al])
        plan_html = text(client.get(base + '/plan'))
        check('the alignment step shows on the plan page', al and al[0].title in plan_html)

        print('\n12. Agree a proof plan: rung 3')
        P = api + '/proof'
        login(F_ID)
        body = {'customer_name': 'Smile Co', 'pain': 'No-shows cost two hours a day',
                'baseline': '18% of appointments are no-shows', 'smallest_build': 'SMS reminders for one clinic',
                'duration': '4 weeks', 'on_success': 'Paid pilot',
                'provides': {'founder': ['Reminder service'], 'customer': ['Appointment export']},
                'success_criteria': [{'metric': 'No-show rate', 'kind': 'cost', 'baseline': '18%',
                                      'target': 'under 10%', 'how_measured': 'Clinic booking system'}]}
        r = client.post(P, json=body)
        check('the proof plan is saved', r.status_code == 200, r.get_json())
        plan_id = r.get_json()['plan_id']
        r = client.post(P + '/%s/agree' % plan_id)
        check('the plan is agreed', r.status_code == 200, r.get_json())
        if venture().rung < 3:
            client.post(P + '/sync')
        check('the venture is at rung 3', venture().rung == 3, venture().rung)
        ws = text(client.get(base))
        check('the workspace shows the new rung and the rung change in its activity',
              'Evidence rung 3 of 5' in ws, re.findall(r'Evidence rung[^<]{0,60}', ws)[:3])

        print('\n13. Draft and publish the startup page (decision right)')
        r = client.get(base + '/page')
        check('the editor opens and drafts the page', r.status_code == 200
              and StartupPage.query.filter_by(venture_id=V_ID).count() == 1, r.status_code)
        r = client.post(api + '/page', json={'name': 'Acme Dental', 'one_liner': 'Fewer no-shows for dental clinics',
                                             'problem': 'Clinics lose hours every day to missed appointments.'})
        check('the founder saves the page', r.status_code == 200, r.get_json())
        login(C_ID)
        r = client.post(api + '/page/publish', json={'action': 'publish'})
        check('the co-founder (no publish right) cannot publish', r.status_code == 403, r.status_code)
        r = client.post(api + '/page/publish', json={'action': 'request'})
        check('the co-founder can ask the right holder to publish', r.status_code == 200, r.get_json())
        login(F_ID)
        r = client.post(api + '/page/publish', json={'action': 'publish'})
        page = StartupPage.query.filter_by(venture_id=V_ID).first()
        db.session.refresh(page)
        check('the owner (holder of the publish right) publishes', r.status_code == 200
              and page.status == 'published' and page.publish_approved_by == F_ID, r.get_json())
        SLUG = page.slug

        print('\n14. Public view and an intro request')
        login(None)
        r = client.get('/startup/%s' % SLUG)
        check('the public page renders', r.status_code == 200 and 'Acme Dental' in text(r), r.status_code)
        r = client.post('/startup/%s/request' % SLUG, json={'kind': 'intro', 'name': 'Ivy Investor',
                                                          'email': 'ivy@fund.vc', 'firm': 'Ivy Fund',
                                                          'message': 'Would love an intro.'})
        check('an intro request is accepted', r.status_code == 200, (r.status_code, r.get_json()))
        c = Contact.query.filter_by(user_id=F_ID, email='ivy@fund.vc').first()
        check('a Contact is created for the venture owner', c is not None and c.source == 'startup_page')

        print('\n15. Admin observability: the Founder tab')
        login(A_ID)
        page_html = text(client.get('/admin/observability'))
        check('the console has the Founder Track tab', 'id="obFounderTab"' in page_html)
        meta_api = client.get('/api/admin/observability/founder/meta').get_json() or {}
        check('the tab is offered while the track is on', meta_api and meta_api.get('mode') != 'off', meta_api)
        runs = client.get('/api/admin/observability/founder/runs?hours=24&venture=%s' % V_ID).get_json()['runs']
        agents = {x['agent_key'] for x in runs}
        check('the venture runs are listed with F codes: evaluation and agent runs',
              {'fnd_evaluation', 'fnd_problem_statement', 'fnd_pain_evidence'} <= agents
              and all(re.match(r'^F[1-5]$', x['layer'] or '') for x in runs), [(x['agent_key'], x['layer']) for x in runs])
        fleet = client.get('/api/admin/observability/founder/fleet?hours=24').get_json()
        check('the fleet counts them', fleet['totals']['runs'] >= 3, fleet['totals'])
        trace = client.get('/api/admin/observability/founder/ventures/%s/trace' % V_ID).get_json()
        kinds = {i['kind'] for i in trace['items']}
        check('the venture trace has runs, step changes, the approval and the evaluation',
              {'run', 'step', 'approval', 'evaluation'} <= kinds, kinds)
        check('a founder cannot read the Founder tab APIs',
              (login(F_ID), client.get('/api/admin/observability/founder/fleet'))[1].status_code in (302, 403, 404))

        print('\n16. Flag off: everything 404s, existing pages unchanged')
        founder_feature.set_mode('founder_track', 'off', A_ID)
        login(F_ID)
        founder_paths = ['/founder', base, base + '/report', base + '/team', base + '/plan', base + '/alignment',
                         base + '/roles', base + '/proof', base + '/page', base + '/support', base + '/intake',
                         api + '/next', api + '/live', '/api/founder/outputs/%s' % out.id]
        codes = {p: client.get(p).status_code for p in founder_paths}
        check('every founder page and API is 404', all(c == 404 for c in codes.values()), codes)
        check('founder POSTs are 404', client.post(api + '/plan/build').status_code == 404
              and client.post('/api/founder/actions/%s/approve' % drafts[0].id).status_code == 404)
        login(C_ID)
        check('the co-founder gets 404 too', client.get(base).status_code == 404)
        login(None)
        check('the public startup page is 404', client.get('/startup/%s' % SLUG).status_code == 404)
        check('intro requests are 404', client.post('/startup/%s/request' % SLUG, json={}).status_code == 404)
        login(A_ID)
        meta_api = client.get('/api/admin/observability/founder/meta').get_json() or {}
        check('the admin Founder tab reports the switch off', meta_api.get('mode') == 'off', meta_api)
        changed = []
        for uid in (None, M_ID, F_ID):
            login(uid)
            for p in EXISTING:
                r = client.get(p)
                now_ = (r.status_code, strip_volatile(text(r)))
                if now_ != baseline[(uid, p)]:
                    changed.append((uid, p, r.status_code, baseline[(uid, p)][0]))
        check('existing pages are unchanged for visitors, members and the founder', not changed, changed)
        login(F_ID)
        check('no Founder Track item in the member nav', 'href="/founder"' not in text(client.get('/dashboard')))

    print('\n%d passed, %d failed' % (len(PASS), len(FAIL)))
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
