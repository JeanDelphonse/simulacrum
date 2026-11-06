"""Compliance risk reduction (COMPLIANCE_PLAN.md). One section per plan step.

The network is blocked; Anthropic, SendGrid and Alpaca are faked where used.

    python test_compliance.py
"""
import os
import re
import sys
from datetime import datetime, timedelta

os.environ.setdefault('DATABASE_URL', 'sqlite:///:memory:')
os.environ.setdefault('SECRET_KEY', 'test-only')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PASS, FAIL = [], []


def check(label, condition, detail=''):
    (PASS if condition else FAIL).append(label)
    line = '  {} {}{}'.format('ok  ' if condition else 'FAIL', label, ('  -- ' + str(detail)) if detail else '')
    enc = sys.stdout.encoding or 'ascii'
    print(line.encode(enc, 'replace').decode(enc))


def main():
    import requests
    network = []
    requests.post = requests.get = (lambda *a, **k: network.append(a[0] if a else k)
                                    or (_ for _ in ()).throw(AssertionError('network')))
    # SendGrid has its own HTTP client: fake it, recording what would be sent.
    import sendgrid
    SENT = []

    class _FakeSG:
        def __init__(self, api_key=None, *a, **k):
            self.api_key = api_key

        def send(self, message):
            SENT.append(message.get() if hasattr(message, 'get') else message)
            return type('R', (), {'status_code': 202, 'headers': {'X-Message-Id': 'm%d' % len(SENT)}, 'body': ''})()
    sendgrid.SendGridAPIClient = _FakeSG
    from app import create_app
    from app.extensions import db
    from utils.id_gen import generate_id, generate_pk
    from cryptography.fernet import Fernet
    app = create_app('testing')
    app.config.update(SQLALCHEMY_DATABASE_URI='sqlite:///:memory:', WTF_CSRF_ENABLED=False, PROPAGATE_EXCEPTIONS=True,
                      ENCRYPTION_KEY=Fernet.generate_key().decode())

    with app.app_context():
        from flask import g
        from app import models as _models  # noqa: F401
        db.create_all()
        from app.models.user import User
        from app.models.audit_log import AuditLog
        from app.models.compliance import LegalText
        from app.services import legal_text

        def user(admin=False, email=None):
            u = User(id=generate_id(), email=email or ('u%s@t.local' % generate_id()), full_name='Test Person',
                     password_hash='x', email_verified=True, is_admin=admin,
                     onboarding_completed_at=datetime.utcnow())
            db.session.add(u)
            db.session.commit()
            return u

        client = app.test_client()

        def login(u):
            from sqlalchemy import inspect as _insp
            uid = _insp(u).identity[0]          # works on a detached instance too
            with client.session_transaction() as s:
                s['_user_id'] = uid
                s['_fresh'] = True
            g.pop('_login_user', None)

        # ── 1. Foundation ───────────────────────────────────────────────────
        print('\n1. generate_pk and the legal-text source')
        pks = {generate_pk() for _ in range(200)}
        check('generate_pk: 9 uppercase alphanumerics', all(re.fullmatch(r'[A-Z0-9]{9}', p) for p in pks))
        check('…and unique in practice', len(pks) == 200)
        check('new tables default to generate_pk', LegalText(key='x', body='y').id is None
              and LegalText.__table__.c.id.default.arg.__name__ == 'generate_pk')

        check('a default is served before anything is seeded', legal_text.get('estimate_label') == 'Estimate, not a promise')
        check('placeholders are filled, missing ones blank',
              legal_text.get('email_reply_optout', sender_name='Ana') == 'Reply STOP to stop receiving emails from Ana.'
              and 'Sent by  via SimiSI' in legal_text.get('email_footer'))
        try:
            legal_text.get('no_such_key')
            unknown_raises = False
        except KeyError:
            unknown_raises = True
        check('an unknown key is an error, not silent text', unknown_raises)

        legal_text.ensure_seeded()
        rows = LegalText.query.all()
        check('every default is seeded', len(rows) == len(legal_text.DEFAULTS))
        check('…and every one starts as a draft for counsel', all(r.status == LegalText.STATUS_DRAFT for r in rows))
        check('the Jinja global renders the same text',
              app.jinja_env.from_string("{{ legal('estimate_label') }}").render() == 'Estimate, not a promise')

        admin = user(admin=True)
        legal_text.update('estimate_label', 'An estimate, not a promise', admin.id)
        row = LegalText.query.filter_by(key='estimate_label').first()
        check('an edit is served, bumps the version and stays a draft',
              legal_text.get('estimate_label') == 'An estimate, not a promise' and row.version == 2
              and row.status == LegalText.STATUS_DRAFT)
        check('…and is audit-logged with the old and new wording',
              AuditLog.query.filter_by(action='legal_text_updated').count() == 1)
        legal_text.set_status('estimate_label', True, admin.id)
        check('an admin can record counsel approval', LegalText.query.filter_by(key='estimate_label').first().status
              == LegalText.STATUS_APPROVED)
        legal_text.update('estimate_label', 'Estimate, not a promise', admin.id)
        check('editing an approved text makes it a draft again',
              LegalText.query.filter_by(key='estimate_label').first().status == LegalText.STATUS_DRAFT)

        src = ''
        for root, _d, files in os.walk('app'):
            for f in files:
                if f.endswith(('.py', '.html', '.js')):
                    src += open(os.path.join(root, f), encoding='utf-8', errors='ignore').read()
        check('no code sets counsel approval except the admin action',
              src.count('STATUS_APPROVED if approved') == 1 and "status='counsel_approved'" not in src)

        member = user()
        login(member)
        check('members cannot open Admin → Legal text', client.get('/admin/legal-text').status_code == 403)
        check('…or change a text', client.put('/api/admin/legal-text/estimate_label', json={'approved': True}).status_code == 403)
        login(admin)
        page = client.get('/admin/legal-text')
        check('admins see every text with its status', page.status_code == 200
              and page.get_data(as_text=True).count('class="lt-card"') == len(legal_text.DEFAULTS)
              and 'Draft for counsel' in page.get_data(as_text=True))
        r = client.put('/api/admin/legal-text/l5_banner', json={'body': '  '})
        check('an empty text is refused', r.status_code == 400)

        # ── 3. Copy ─────────────────────────────────────────────────────────
        print('\n3. Earnings-claims copy')
        from app.models.outreach_campaign import OutreachTemplate
        from app.services import outreach_campaign_service as ocs
        import ast
        import subprocess
        old_src = subprocess.run(['git', 'show', '387cbcd:app/services/outreach_campaign_service.py'],
                                 capture_output=True, text=True, encoding='utf-8').stdout
        i = old_src.index('DEFAULT_TEMPLATES = {')
        old = ast.literal_eval(old_src[i + len('DEFAULT_TEMPLATES = '):old_src.index('\n}\n', i) + 2])
        db.session.add(OutreachTemplate(template_key='drip_3', name='d3', subject=old['drip_3']['subject'],
                                        preview_text='p', body=old['drip_3']['body'], is_drip=True))
        db.session.add(OutreachTemplate(template_key='drip_2', name='d2', subject='Our own subject',
                                        preview_text='p', body='An admin wrote this.', is_drip=True))
        db.session.commit()
        check('an unedited old drip is served as the new default',
              ocs.get_template('drip_3')['subject'] == ocs.DEFAULT_TEMPLATES['drip_3']['subject']
              and 'while you sleep' not in ocs.get_template('drip_3')['subject'].lower())
        check('…an admin-edited drip is left alone', ocs.get_template('drip_2')['body'] == 'An admin wrote this.')
        ocs.seed_default_templates()
        check('seeding upgrades only the unedited row',
              OutreachTemplate.query.filter_by(template_key='drip_3').first().subject
              == ocs.DEFAULT_TEMPLATES['drip_3']['subject']
              and OutreachTemplate.query.filter_by(template_key='drip_2').first().body == 'An admin wrote this.')

        paid = user()
        paid.voice_training_paid_at = datetime.utcnow()
        db.session.commit()
        login(paid)
        voice = client.get('/settings/voice').get_data(as_text=True)
        check('the voice-training script makes no income or results claims',
              'Read these paragraphs aloud' in voice and '46,000' not in voice and 'autopilot' not in voice
              and 'first client' not in voice)
        from app.models.simulation import SimulationLayer
        check('stored layer names no longer promise passive income',
              SimulationLayer.LAYER_NAMES[4] == 'Recurring Income — After the Initial Build'
              and 'Money Working' not in SimulationLayer.LAYER_NAMES[5])
        mig = open('sql/064_compliance.sql', encoding='utf-8').read()
        check('…and the migration renames existing simulations', "LIKE 'Automated Residual%'" in mig)
        login(admin)
        home = client.get('/').get_data(as_text=True)
        check("the home page's Layer 5 note comes from the legal-text source",
              legal_text.get('l5_note_short') in home and 'Advisory only' not in home)

        # ── 4. Estimates ────────────────────────────────────────────────────
        print('\n4. Every projected income figure through the estimate component')
        offenders = []
        for base in ('app/templates', 'app/static/js'):
            for root, _d, files in os.walk(base):
                for f in files:
                    path = os.path.join(root, f).replace(os.sep, '/')
                    if not f.endswith(('.html', '.js')) or path.endswith(('_estimate.html', 'estimate.js')):
                        continue
                    for n, line in enumerate(open(path, encoding='utf-8'), 1):
                        if re.search(r'est_monthly_(low|high)', line) and not re.search(
                                r'estimate\(|SimiEstimate|tojson|totLo|totHi', line):
                            offenders.append('%s:%d' % (path, n))
        check('no template or script formats a projected range itself', not offenders, offenders[:6])

        from app.models.simulation import Simulation, SimulationLayer, IncomeStream
        owner = user()
        sim = Simulation(id=generate_id(), user_id=owner.id, name='S', expertise_zone='Ops', status='complete',
                         agent_selection_confirmed_at=datetime.utcnow())
        db.session.add(sim)
        db.session.flush()
        for n in (1, 3):
            lay = SimulationLayer(id=generate_id(), simulation_id=sim.id, layer_number=n,
                                  layer_name=SimulationLayer.LAYER_NAMES[n], ai_narrative='N')
            db.session.add(lay)
            db.session.flush()
            db.session.add(IncomeStream(id=generate_id(), layer_id=lay.id, name='Stream %d' % n,
                                        est_monthly_low=1000 * n, est_monthly_high=2500 * n,
                                        ai_reasoning='Assumes two clients a month at market rates.'))
        db.session.commit()
        login(owner)
        page = client.get('/simulations/%s' % sim.id).get_data(as_text=True)
        check('the results page shows ranges through the component', page.count('class="est"') == 2
              and '$1,000–$2,500/mo' in page and '$3,000–$7,500/mo' in page)
        check('…each labelled and with its basis one click away',
              page.count(legal_text.get('estimate_label')) >= 2 and page.count('How we estimated this') == 2
              and 'Assumes two clients a month at market rates.' in page)
        check('…and the base-rate note on Layer 3 only', page.count('class="est-p est-base"') == 1)
        tpl = app.jinja_env.from_string("{% import 'partials/_estimate.html' as e %}{{ e.estimate(None, 4000) }}|"
                                        "{{ e.estimate(None, None) }}")
        out = tpl.render()
        check('one-sided figures read as a ceiling; nothing at all renders nothing',
              'up to $4,000/mo' in out and out.endswith('|'))
        check('every page carries the JS twin with the same wording',
              'js/estimate.js' in page and '"label": "%s"' % legal_text.get('estimate_label') in page)
        from app.services import agent_briefing
        check('agent prompts ask for ranges, never single numbers',
              'low–high range' in agent_briefing.agent_rules({}) if hasattr(agent_briefing, 'agent_rules')
              else 'low–high range' in open('app/services/agent_briefing.py', encoding='utf-8').read())

        # ── 5. Testimonials, case studies, share card ───────────────────────
        print('\n5. Testimonials, illustrative labels and the share-ROI card')
        from app.services.claims import mentions_results
        check('results talk is spotted', all(mentions_results(t) for t in (
            'I landed 3 clients in a month', 'Made $4,000', 'Saves me from writing 50 emails',
            'my revenue doubled', 'great ROI')) and not mentions_results('Lovely to work with and very kind.'))
        from app.models.feedback import UserFeedback
        fb_cols = {c.name for c in UserFeedback.__table__.columns}
        base = dict(id=generate_id(), user_id=owner.id, star_rating=5, outcome_text='o', name_display='full')
        base = {k: v for k, v in base.items() if k in fb_cols}
        db.session.add(UserFeedback(**dict(base, id=generate_id(), quote_text='It booked me two new clients.',
                                           status='approved')))
        db.session.add(UserFeedback(**dict(base, id=generate_id(), quote_text='PENDING REVIEW TEXT', status='pending')))
        db.session.commit()
        import app.blueprints.pages.routes as _pr
        if hasattr(_pr, '_landing_cache'):
            _pr._landing_cache['ts'] = 0  # expire the 5-minute landing cache
        home = client.get('/').get_data(as_text=True)
        check('a results testimonial carries the typical-results disclosure',
              'It booked me two new clients.' in home and legal_text.get('typical_results') in home)
        check('a review that is not approved never shows', 'PENDING REVIEW TEXT' not in home)
        check('sample runs and Playbook case studies are labelled illustrative',
              home.count(legal_text.get('illustrative_label').replace("'", '&#39;')) >= 2)
        for s_url in ('/samples/maya', '/samples/marcus'):
            body = client.get(s_url).get_data(as_text=True)
            check('%s is labelled an illustrative case study' % s_url,
                  'uc-illustrative' in body and 'composite example' in body)

        sim.amount_charged_cents = 2450
        db.session.commit()
        login(owner)
        card = client.get('/simulations/%s/share-roi' % sim.id)
        check('the owner sees an unshared card, with the share control', card.status_code == 200
              and 'Share publicly' in card.get_data(as_text=True))
        check('…labelled self-reported, with the disclosure',
              legal_text.get('self_reported_label') in card.get_data(as_text=True)
              and legal_text.get('share_roi_disclosure') in card.get_data(as_text=True))
        with client.session_transaction() as s:
            s.clear()
        g.pop('_login_user', None)
        check('a stranger gets nothing until it is shared', client.get('/simulations/%s/share-roi' % sim.id).status_code == 404)
        intruder = user()
        login(intruder)
        check('only the owner can share it', client.post('/api/simulations/%s/share-roi' % sim.id,
                                                         json={'enabled': True}).status_code == 403)
        login(owner)
        client.post('/api/simulations/%s/share-roi' % sim.id, json={'enabled': True})
        with client.session_transaction() as s:
            s.clear()
        g.pop('_login_user', None)
        shared = client.get('/simulations/%s/share-roi' % sim.id)
        check('once shared, it is public, still with the disclosure and no owner controls',
              shared.status_code == 200 and legal_text.get('share_roi_disclosure') in shared.get_data(as_text=True)
              and 'Stop sharing' not in shared.get_data(as_text=True))
        check('sharing is audit-logged', AuditLog.query.filter_by(action='roi_share_on').count() == 1)

        # ── 6. Outcome check-ins ────────────────────────────────────────────
        print('\n6. Outcome check-ins')
        from app.models.compliance import OutcomeCheckin
        from app.services import outcome_service
        checker = user()
        s30 = Simulation(id=generate_id(), user_id=checker.id, name='Ops run', expertise_zone='Ops', status='complete',
                         created_at=datetime.utcnow() - timedelta(days=31))
        db.session.add(s30)
        db.session.commit()
        login(checker)
        dash = client.get('/dashboard').get_data(as_text=True)
        check('a simulation 31 days old asks for the day-30 check-in',
              'id="outcomeCard"' in dash and 'data-day="30"' in dash and '30 days in' in dash)
        fresh = user()
        db.session.add(Simulation(id=generate_id(), user_id=fresh.id, name='New', expertise_zone='Ops', status='complete'))
        db.session.commit()
        check('…a new one asks for nothing', outcome_service.due(fresh.id) is None)
        r = client.post('/api/outcome-checkins', json={'simulation_id': s30.id, 'day': 30, 'income_gained_range': 'under_1k',
                                              'first_client': True, 'job_landed': None, 'notes': 'Two calls booked.'})
        row = OutcomeCheckin.query.filter_by(simulation_id=s30.id, checkin_day=30).first()
        check('an answer is recorded', r.status_code == 200 and row and row.income_gained_range == 'under_1k'
              and row.first_client is True and row.job_landed is None and re.fullmatch(r'[A-Z0-9]{9}', row.id))
        check('…and the prompt goes away', 'id="outcomeCard"' not in client.get('/dashboard').get_data(as_text=True))
        check('a bad income band is refused', client.post('/api/outcome-checkins', json={
            'simulation_id': s30.id, 'day': 30, 'income_gained_range': 'a million'}).status_code == 400)
        login(intruder)
        check("nobody can answer for someone else's simulation", client.post('/api/outcome-checkins', json={
            'simulation_id': s30.id, 'day': 90, 'dismiss': True}).status_code == 400)
        s200 = Simulation(id=generate_id(), user_id=checker.id, name='Old run', expertise_zone='Ops', status='complete',
                          created_at=datetime.utcnow() - timedelta(days=200))
        db.session.add(s200)
        db.session.commit()
        check('a long-missed simulation jumps to its latest due day', outcome_service.due(checker.id)['day'] == 180)
        outcome_service.record(checker.id, {'simulation_id': s200.id, 'day': 180, 'dismiss': True})
        check("…and 'don't ask again' closes it and the days it skipped",
              outcome_service.due(checker.id) is None
              and OutcomeCheckin.query.filter_by(simulation_id=s200.id, dismissed=True).count() == 3)
        summary = outcome_service.admin_summary()
        check('Admin sees aggregate counts only', summary['answered'] == 1 and summary['by_day'][30]['first_client'] == 1
              and 'notes' not in str(summary))
        readers = []
        for root, _d, files in os.walk('app'):
            for f in files:
                if f.endswith(('.py', '.html')) and 'outcome_service' not in f and 'compliance' not in f \
                        and f != '__init__.py':
                    txt = open(os.path.join(root, f), encoding='utf-8').read()
                    if 'OutcomeCheckin' in txt or 'outcome_checkins' in txt:
                        readers.append(os.path.join(root, f))
        check('nothing public reads the check-ins', not readers, readers)

        # ── 7. Layer 5 prompt rules ─────────────────────────────────────────
        print('\n7. Layer 5: education and planning only (prompts)')
        from app.services import agent_registry
        l5 = [a for a in agent_registry.get_all_agents() if a.get('layer') == 5]
        rules_core = legal_text.get('l5_agent_rules').split('End with this exact line')[0].strip()
        check('every Layer 5 agent gets the education-only rules',
              len(l5) == 10 and all(rules_core in agent_briefing.system_prompt(a) for a in l5))
        tax = agent_briefing.system_prompt(agent_registry.get_agent_meta('tax_optimization'))
        estate = agent_briefing.system_prompt(agent_registry.get_agent_meta('estate_planning'))
        alloc = agent_briefing.system_prompt(agent_registry.get_agent_meta('income_allocation'))
        check('…ending with the right referral line (CPA, attorney, fiduciary adviser)',
              legal_text.get('l5_referral_cpa') in tax and legal_text.get('l5_referral_attorney') in estate
              and legal_text.get('l5_referral_adviser') in alloc)
        check('legacy alias action types resolve to the same rules',
              rules_core in agent_briefing.system_prompt(agent_registry.get_agent_meta('portfolio_analysis') or {}))
        check('other layers get none of it', rules_core not in agent_briefing.system_prompt(
            agent_registry.get_agent_meta('rate_card')))
        brief_src = open('app/services/agent_briefing.py', encoding='utf-8').read()
        check('no disclaimer wording is pasted into the prompt code',
              'licensed professional (CFP, CPA or attorney' not in brief_src and "legal_text.get('l5_agent_rules'" in brief_src)
        fr = agent_registry.get_agent_meta('fund_recommendations')
        dca = agent_registry.get_agent_meta('dca_schedule')
        spec = ' '.join(fr['output_spec'] + fr.get('external_facts', [])).lower()
        check('the fund agent explains fund types and names no funds',
              fr['label'] == 'Fund Types Explained' and 'curate' not in fr['description'].lower()
              and 'no named funds or tickers' in spec and 'tickers and expense' not in spec)
        check('the contribution agent has no brokerage picker or brokerage integration',
              dca['label'] == 'Regular Contribution Concepts' and dca['integrations'] == []
              and all(f['key'] != 'brokerage' for f in dca['prompt_form'])
              and not any('brokerage' in x.lower() for x in dca['output_spec']))
        check('no Layer 5 spec asks for a recommendation, carriers or a plain disclaimer',
              not any(x in ('Disclaimer', 'Recommended coverage parameters', 'Recommendation at the stated revenue')
                      for a in l5 for x in a['output_spec'])
              and not any('carrier' in x for a in l5 for x in a.get('external_facts', [])))
        l5sim = Simulation(id=generate_id(), user_id=owner.id, name='L5', expertise_zone='Ops', status='complete')
        db.session.add(l5sim)
        db.session.commit()
        login(owner)
        page5 = client.get('/simulations/%s/layers/5' % l5sim.id, follow_redirects=True).get_data(as_text=True)
        page1 = client.get('/simulations/%s/layers/1' % l5sim.id, follow_redirects=True).get_data(as_text=True)
        check('Layer 5 pages carry the education-only banner, others do not',
              legal_text.get('l5_banner') in page5 and legal_text.get('l5_banner') not in page1)

        # ── 8. Layer 5 output filter ────────────────────────────────────────
        print('\n8. Layer 5 output filter')
        from types import SimpleNamespace
        from app.services import claude, compliance_filter
        from app.models.compliance import ComplianceEvent
        check('a seeded ticker is caught', any(r == 'ticker' for r, _ in compliance_filter.check_l5(
            'A broad fund such as VTI keeps costs low.')))
        check('…and a cash-tag, a fund family and a trade instruction',
              {r for r, _ in compliance_filter.check_l5('Look at $ARKK. Vanguard is cheap.\n- Buy 20 shares of it.\n'
                                                         'You should allocate $500 a month.')}
              >= {'cashtag', 'named_product', 'trade_instruction'})
        clean = ('A common approach is to keep three to six months of expenses in cash. In a scenario where '
                 'income varies, some people set aside a share of each payment for taxes. Questions for an adviser: '
                 'how much risk fits my timeline?')
        check('education passes untouched', compliance_filter.check_l5(clean) == [])

        class Fake:
            def __init__(self, texts):
                self.texts, self.calls = list(texts), []
                self.messages = self

            def create(self, **kw):
                self.calls.append(kw)
                return SimpleNamespace(content=[SimpleNamespace(text=self.texts.pop(0) if self.texts else clean)],
                                       usage=SimpleNamespace(input_tokens=5, output_tokens=5), stop_reason='end_turn')

        real_client, real_log = claude._client, claude._log_interaction
        claude._log_interaction = lambda *a, **k: None     # disposes the engine; the test DB is in memory
        plan = '{"objective": "explain", "key_inputs": [], "gaps": [], "outline": []}'

        def run(texts, agent='fund_recommendations', ask='Which stock should I buy with my severance?'):
            fake = Fake([plan] + texts)
            claude._client = lambda: fake
            out = claude._execute_agent_action_body(
                action_type=agent, layer_number=5, expertise_zone='Ops', parsed_text='',
                user_inputs={'question': ask}, user_id=owner.id, simulation_id=sim.id, model='m')
            return out, fake
        try:
            out, fake = run(['Buy VTI and $QQQ now. You should put $5,000 into Vanguard.', clean])
            check('"which stock should I buy": the reply with tickers is regenerated, the clean one kept',
                  out.startswith('A common approach') and 'VTI' not in out and len(fake.calls) == 3)
            check('…the regeneration used the stricter rules',
                  legal_text.get('l5_agent_rules_strict') in fake.calls[-1]['system'])
            check('…and it ends with the referral line', legal_text.get('l5_referral_adviser') in out)
            ev = ComplianceEvent.query.filter_by(agent='fund_recommendations', outcome='regenerated').all()
            check('…with a compliance event per rule broken', {e.rule for e in ev} >= {'ticker', 'cashtag'}
                  and all(re.fullmatch(r'[A-Z0-9]{9}', e.id) for e in ev) and ev[0].user_id == owner.id)
            out2, _ = run(['Buy SPY.', 'Still: buy 10 shares of AAPL.'], agent='tax_optimization')
            check('twice in a row: the safe fallback is saved instead',
                  out2.startswith(legal_text.get('l5_fallback')) and 'AAPL' not in out2
                  and legal_text.get('l5_referral_cpa') in out2)
            check('…and logged as a fallback', ComplianceEvent.query.filter_by(agent='tax_optimization',
                                                                              outcome='fallback').count() >= 1)
            out3, fake3 = run([clean], agent='estate_planning')
            check('a clean output costs no extra call and gets its referral line',
                  len(fake3.calls) == 2 and legal_text.get('l5_referral_attorney') in out3)
        finally:
            claude._client, claude._log_interaction = real_client, real_log
        login(admin)
        cpage = client.get('/admin/compliance').get_data(as_text=True)
        check('Admin → Compliance lists the events', 'fund_recommendations' in cpage and 'ticker' in cpage
              and 'regenerated' in cpage and 'aggregate only' in cpage)
        login(member)
        check('…members cannot open it', client.get('/admin/compliance').status_code == 403)

        # ── 10. Layer 5 kill switch ─────────────────────────────────────────
        print('\n10. Layer 5 kill switch')
        from app.services import l5_feature
        from app.models.layer6 import Layer6ActionQueue
        from app.services import layer6 as layer6_service

        def db_ok(where):
            try:
                User.query.count()
                return True
            except Exception as e:
                print('   db broken after %s: %s' % (where, str(e)[:160]))
                db.session.rollback()
                return False

        check('on by default', l5_feature.enabled())
        login(member)
        check('members cannot flip it', client.put('/api/admin/compliance/l5', json={'mode': 'off'}).status_code == 403)
        login(admin)
        r = client.put('/api/admin/compliance/l5', json={'mode': 'off', 'reason': 'counsel review'})
        check('an admin switches it off, with a reason, audit-logged', r.status_code == 200 and not l5_feature.enabled()
              and AuditLog.query.filter_by(action='layer5_switch_off').count() == 1)
        check('…and the Compliance page shows it off',
              'Switch Layer 5 on' in client.get('/admin/compliance').get_data(as_text=True))
        routes_src = open('app/blueprints/simulations/routes.py', encoding='utf-8').read()
        check('every run endpoint refuses Layer 5 while it is off', routes_src.count('_l5.blocks(') == 3)
        q = Layer6ActionQueue(id=generate_id(), simulation_id=sim.id, action_type='income_allocation', source_layer=5)
        cols = {c.name for c in Layer6ActionQueue.__table__.columns}
        for col, val in (('cycle_id', generate_id()), ('priority', 1), ('bayesian_score', 0.5)):
            if col in cols and getattr(q, col, None) is None:
                setattr(q, col, val)
        db.session.add(q)
        db.session.commit()
        layer6_service._execute_action_sync(q)
        check('the orchestrator skips Layer 5 dispatches', q.status == Layer6ActionQueue.STATUS_REJECTED
              and q.agent_action_id is None and db_ok('orchestrator'))
        fake_calls = []
        real_dispose = db.engine.dispose
        claude._client = lambda: fake_calls.append(1)
        db.engine.dispose = lambda *a, **k: None      # the test DB lives in memory
        try:
            out = claude.execute_agent_action(action_type='income_allocation', layer_number=5, expertise_zone='Ops',
                                              parsed_text='', user_inputs={}, user_id=owner.id, simulation_id=sim.id)
        finally:
            claude._client, db.engine.dispose = real_client, real_dispose
        check('…and the agent entry point makes no model call',
              out == legal_text.get('l5_unavailable') and not fake_calls and db_ok('entry point'))
        check('other layers still run (the switch is Layer 5 only)', not l5_feature.blocks(1))
        login(owner)
        # The setup page asks Claude to score agents; offline here, and the real
        # _log_interaction would dispose the in-memory engine.
        claude._client = lambda: (_ for _ in ()).throw(RuntimeError('offline'))
        claude._log_interaction = lambda *a, **k: None
        try:
            sel = client.get('/simulations/%s/setup' % sim.id).get_data(as_text=True)
        finally:
            claude._client, claude._log_interaction = real_client, real_log
        check('the agent selector says Layer 5 is unavailable', legal_text.get('l5_unavailable') in sel)
        login(admin)
        client.put('/api/admin/compliance/l5', json={'mode': 'on'})
        check('switching it back on restores Layer 5', l5_feature.enabled())

        # ── 11. One suppression list ────────────────────────────────────────
        print('\n11. One suppression check on every send and import')
        from app.models.contact import Contact
        from app.models.outreach_email import EmailSuppression
        from app.models.mail_crm import UserSuppression, ContactDncFlag, email_hash
        from app.services import suppression_service
        from app.services.outreach_email_service import send_outreach_email
        sender = user()
        ssim = Simulation(id=generate_id(), user_id=sender.id, name='Out', expertise_zone='Ops', status='complete')
        db.session.add(ssim)
        db.session.add(EmailSuppression(email='gone@x.com', reason='unsubscribe'))
        db.session.add(UserSuppression(user_id=sender.id, email_hash=email_hash('Mine@X.com'), reason='unsubscribe'))
        db.session.commit()

        def contact(email, **kw):
            c = Contact(id=generate_id(), user_id=sender.id, first_name='Dana', last_name='Lee', email=email,
                        source=kw.pop('source', 'manual_entry'), pipeline_stage='prospect', **kw)
            db.session.add(c)
            db.session.commit()
            return c
        c_ok, c_dnc = contact('ok@x.com'), contact('dnc@x.com', do_not_contact=True)
        c_flag = contact('flag@x.com')
        db.session.add(ContactDncFlag(contact_id=c_flag.id, reason='integration_disabled'))
        db.session.commit()
        check('global, member, Do-Not-Contact and Email-to-CRM flags all suppress',
              suppression_service.check(sender.id, 'gone@x.com') == 'global'
              and suppression_service.check(sender.id, 'mine@x.com') == 'member'
              and suppression_service.check(sender.id, 'dnc@x.com') == 'do_not_contact'
              and suppression_service.check(sender.id, 'flag@x.com') == 'dnc_flag'
              and suppression_service.check(sender.id, 'ok@x.com') is None)
        check("a member's own list doesn't silence another member", suppression_service.check(owner.id, 'mine@x.com') is None)
        late = contact('GONE@x.com', source='apollo')
        mine = contact('mine@x.com', source='csv_import')
        check('a suppressed address imported later (Apollo, CSV) arrives as Do Not Contact',
              late.do_not_contact and mine.do_not_contact and not c_ok.do_not_contact)
        for c in (late, c_dnc, c_flag):
            r = send_outreach_email(simulation_id=ssim.id, contact_id=c.id, subject='Hi', html_body='<p>x</p>',
                                    from_email='simi@simulacrumai.io', from_name='Pat')
            check('send to %s is refused (%s)' % (c.email, r.get('reason')), r['status'] == 'skipped'
                  and (r['reason'].startswith('suppressed:') or r['reason'] == 'do_not_contact'))
        r = send_outreach_email(simulation_id=ssim.id, contact_id=c_ok.id, subject='Hi', html_body='<p>x</p>',
                                from_email='simi@simulacrumai.io', from_name='Pat')
        app.config['SENDGRID_API_KEY'] = 'test-key'
        from app.services import sender_identity as _si
        _si.set_postal_address(User.query.get(sender.id), '1 Market St, San Francisco, CA 94105')
        r = send_outreach_email(simulation_id=ssim.id, contact_id=c_ok.id, subject='Hi', html_body='<p>x</p>',
                                from_email='simi@simulacrumai.io', from_name='Pat')
        check('a clear contact is sent (to the fake SendGrid only)', r['status'] == 'sent' and SENT, r)
        from app.services import artifact_actions
        fake_action = SimpleNamespace(simulation_id=ssim.id, action_type='consulting_outreach')
        picks = {p['email'].lower(): p for p in artifact_actions.contact_candidates(fake_action, sender.id)}
        check('the send-to-contacts picker uses the same check',
              not picks['gone@x.com']['eligible'] and not picks['mine@x.com']['eligible'] and picks['ok@x.com']['eligible'])
        check('partner referrals respect the global list', suppression_service.check(None, 'gone@x.com') == 'global')

        # ── 12. Opt-out, postal address, honest subjects ────────────────────
        print('\n12. Opt-out by default, postal address, reply STOP, honest subjects')
        from app.models.layer6 import ActionItem
        from app.services import sender_identity
        mailer = user()
        msim = Simulation(id=generate_id(), user_id=mailer.id, name='Mail', expertise_zone='Ops', status='complete')
        db.session.add(msim)
        db.session.commit()
        to = Contact(id=generate_id(), user_id=mailer.id, first_name='Kim', last_name='Ray', email='kim@x.com',
                     source='manual_entry', pipeline_stage='prospect')
        db.session.add(to)
        db.session.commit()
        sent_before = len(SENT)
        r = send_outreach_email(simulation_id=msim.id, contact_id=to.id, subject='Quick idea', html_body='<p>Hi</p>',
                                from_email='simi@simulacrumai.io', from_name='Mo Lee')
        check('no mailing address: the send is refused', r == {'status': 'skipped', 'reason': 'no_postal_address'}
              and len(SENT) == sent_before)
        check('…and the GCC asks for it once', ActionItem.query.filter_by(
            simulation_id=msim.id, item_type='postal_address_required').count() == 1)
        send_outreach_email(simulation_id=msim.id, contact_id=to.id, subject='Quick idea', html_body='<p>Hi</p>',
                            from_email='simi@simulacrumai.io', from_name='Mo Lee')
        check('…not twice', ActionItem.query.filter_by(simulation_id=msim.id,
                                                      item_type='postal_address_required').count() == 1)
        login(mailer)
        check('a vague address is refused', client.put('/api/settings/postal-address',
                                                       json={'address': 'Somewhere'}).status_code == 400)
        r = client.put('/api/settings/postal-address', json={'address': 'PO Box 123, Santa Clara, CA 95050'})
        check('a PO box is accepted, stored encrypted, and the reminder clears', r.status_code == 200
              and 'Santa Clara' not in (User.query.get(mailer.id).postal_address_enc or '')
              and ActionItem.query.filter_by(simulation_id=msim.id, item_type='postal_address_required',
                                             status=ActionItem.STATUS_ACTIVE).count() == 0)
        check('Settings shows the address', 'PO Box 123' in client.get('/settings/notifications').get_data(as_text=True))
        r = send_outreach_email(simulation_id=msim.id, contact_id=to.id, subject='Re: our call', html_body='<p>Hi</p>',
                                from_email='simi@simulacrumai.io', from_name='Mo Lee')
        check('a first email may not pretend to be a reply', r['reason'] == 'deceptive_subject')
        r = send_outreach_email(simulation_id=msim.id, contact_id=to.id, subject='Quick idea', html_body='<p>Hi</p>',
                                from_email='simi@simulacrumai.io', from_name='Mo Lee')
        msg = SENT[-1]
        body = str(msg.get('content'))
        heads = msg.get('headers') or {}
        check('it sends with the legal footer: sender, postal address, unsubscribe link, reply STOP',
              r['status'] == 'sent' and 'Sent by Mo Lee via SimiSI' in body and 'PO Box 123' in body
              and 'Unsubscribe</a>' in body and 'reply STOP' in body)
        check('…and the RFC 8058 one-click headers', 'List-Unsubscribe' in heads
              and heads.get('List-Unsubscribe-Post') == 'List-Unsubscribe=One-Click')
        r = send_outreach_email(simulation_id=msim.id, contact_id=to.id, subject='Re: Quick idea', html_body='<p>x</p>',
                                from_email='simi@simulacrumai.io', from_name='Mo Lee')
        check('a real follow-up may say Re:', r['status'] == 'sent')
        res = client.post('/webhooks/sendgrid/inbound', data={
            'to': 'reply-%s@mail.simulacrumai.io' % to.id, 'from': 'kim@x.com',
            'text': 'STOP\n\nOn Tue, Mo Lee wrote:\n> Quick idea'})
        if res.status_code == 404:
            from app.blueprints.integrations import routes as _ir
            rule = [r_ for r_ in app.url_map.iter_rules() if r_.endpoint.endswith('sendgrid_inbound_parse')][0]
            res = client.post(rule.rule, data={'to': 'reply-%s@mail.simulacrumai.io' % to.id, 'from': 'kim@x.com',
                                               'text': 'STOP\n\nOn Tue, Mo Lee wrote:\n> Quick idea'})
        to_after = Contact.query.get(to.id)
        check('a reply of STOP opts the person out at once', res.status_code == 200
              and EmailSuppression.query.filter_by(email='kim@x.com').first() is not None and to_after.do_not_contact)
        check('…and nothing more goes to them', send_outreach_email(
            simulation_id=msim.id, contact_id=to.id, subject='Again', html_body='<p>x</p>',
            from_email='simi@simulacrumai.io', from_name='Mo Lee')['status'] == 'skipped')
        from app.blueprints.integrations.routes import _is_stop_reply
        check('a reply that merely quotes STOP is not an opt-out', not _is_stop_reply(
            'Thanks, interested!\n> Reply STOP to stop receiving emails'))
        from app.services import outreach_campaign_service as ocs2
        held = ocs2._dispatch_send(SimpleNamespace(id='X', to_email='new@x.com', user_id=mailer.id, status='queued'))
        check("SimiSI's own campaigns hold until Admin sets SimiSI's postal address",
              held.get('reason') == 'no_business_address')
        src = open('app/services/send_pipeline.py', encoding='utf-8').read()
        check('confirmed batches refuse without a mailing address',
              "sender_identity.postal_address(user)" in src and "legal_text.get('postal_address_required')" in src)

        # ── 13. Caps, warm-up, auto-pause ───────────────────────────────────
        print('\n13. Send caps, warm-up and auto-pause')
        from app.models.outreach_email import EmailLog
        from app.models.agent_action import AgentAction
        from app.models.action_step import ActionStep
        from app.services import send_limits
        from app.models.platform_settings import PlatformSetting
        heavy = user()
        sender_identity.set_postal_address(User.query.get(heavy.id), '9 Elm St, Austin, TX 78701')
        hsim = Simulation(id=generate_id(), user_id=heavy.id, name='H', expertise_zone='Ops', status='complete')
        db.session.add(hsim)
        act = AgentAction(id=generate_id(), simulation_id=hsim.id, layer_number=1, action_type='cold_email_campaign',
                          status='complete')
        db.session.add(act)
        db.session.commit()
        check('a new sender starts on the warm-up schedule', send_limits.daily_cap(heavy.id) == 10)

        filler = Contact(id=generate_id(), user_id=heavy.id, first_name='Fill', last_name='Er', email='fill@x.com',
                         source='manual_entry', pipeline_stage='prospect')
        db.session.add(filler)
        db.session.commit()

        def log_sent(n, action_id=None, days_ago=0, **kw):
            for i in range(n):
                db.session.add(EmailLog(id=generate_id(), simulation_id=hsim.id, contact_id=filler.id, action_id=action_id,
                                        subject='s', from_email='f@x.com', from_name='F', to_email='t%d@x.com' % i,
                                        status=kw.get('status', 'sent'),
                                        sent_at=datetime.utcnow() - timedelta(days=days_ago, minutes=i),
                                        bounced_at=kw.get('bounced_at')))
            db.session.commit()
        log_sent(10)
        kc = Contact(id=generate_id(), user_id=heavy.id, first_name='Lee', last_name='Moss', email='lee@x.com',
                     source='manual_entry', pipeline_stage='prospect')
        db.session.add(kc)
        db.session.commit()
        r = send_outreach_email(simulation_id=hsim.id, contact_id=kc.id, subject='Hello', html_body='<p>x</p>',
                                from_email='simi@simulacrumai.io', from_name='Hal')
        check('day 1: the 11th email is deferred, not dropped', r == {'status': 'deferred', 'reason': 'over_daily_cap'})
        EmailLog.query.filter(EmailLog.simulation_id == hsim.id).update(
            {'sent_at': datetime.utcnow() - timedelta(days=30)}, synchronize_session=False)
        db.session.commit()
        check('after warm-up the Admin cap applies (50 by default)', send_limits.daily_cap(heavy.id) == 50)
        PlatformSetting.set('send_cap_per_campaign_day', '3')
        db.session.commit()
        log_sent(3, action_id=act.id)
        r = send_outreach_email(simulation_id=hsim.id, contact_id=kc.id, subject='Hello', html_body='<p>x</p>',
                                from_email='simi@simulacrumai.io', from_name='Hal', action_id=act.id)
        check('a campaign over its own daily cap is deferred', r['reason'] == 'over_campaign_cap')
        PlatformSetting.set('send_cap_per_campaign_day', '25')
        db.session.commit()

        step = ActionStep(id=generate_id(), agent_action_id=act.id, simulation_id=hsim.id, step_number=2, total_steps=3,
                          action_type='cold_email_campaign', step_type='email_followup', subject='Hello again',
                          scheduled_for=datetime.utcnow(), condition_type='no_reply', condition_ref=kc.id)
        db.session.add(step)
        db.session.commit()
        log_sent(20, action_id=act.id, bounced_at=datetime.utcnow())       # 20 of 23 bounced
        paused = send_limits.review_campaign(act.id)
        item = ActionItem.query.filter_by(item_type='campaign_paused', source_action_id=act.id).first()
        check('a high bounce rate pauses the campaign', paused and send_limits.is_paused(act.id)
              and ActionStep.query.get(step.id).status == ActionStep.STATUS_SUSPENDED)
        check('…and tells the member why, with the numbers', item is not None and '%' in (item.description or '')
              and 'bounced' in item.description)
        r = send_outreach_email(simulation_id=hsim.id, contact_id=kc.id, subject='Hello', html_body='<p>x</p>',
                                from_email='simi@simulacrumai.io', from_name='Hal', action_id=act.id)
        check('a paused campaign sends nothing', r['reason'] == 'campaign_paused')
        login(intruder)
        check('only the owner can resume it', client.post('/api/campaigns/%s/resume' % act.id).status_code == 403)
        login(heavy)
        res = client.post('/api/campaigns/%s/resume' % act.id)
        check('the owner resumes it: follow-ups are scheduled again', res.status_code == 200
              and not send_limits.is_paused(act.id) and ActionStep.query.get(step.id).status == ActionStep.STATUS_SCHEDULED)
        small = AgentAction(id=generate_id(), simulation_id=hsim.id, layer_number=1, action_type='cold_email_campaign',
                            status='complete')
        db.session.add(small)
        db.session.commit()
        log_sent(5, action_id=small.id, bounced_at=datetime.utcnow())
        check('too few sends to judge: no pause', not send_limits.review_campaign(small.id))
        login(admin)
        r = client.put('/api/admin/compliance/settings', json={'warmup_schedule': 'ten'})
        check('Admin limits are validated', r.status_code == 400)
        r = client.put('/api/admin/compliance/settings', json={'outreach_business_address': '1 Infinite Loop, Cupertino, CA 95014',
                                                               'send_cap_per_user_day': '40'})
        check('…and saved', r.status_code == 200 and send_limits.setting('send_cap_per_user_day') == 40
              and sender_identity.business_address().startswith('1 Infinite Loop'))
        check('…from Admin → Compliance', 'outreach_business_address' in client.get('/admin/compliance').get_data(as_text=True))

        # ── 14. Import source ───────────────────────────────────────────────
        print('\n14. Imports declare where the list came from')
        import io as _io
        csv_text = 'first_name,last_name,email\nAna,Diaz,ana@d.com\nBen,Ko,ben@k.com\n'

        def upload(source=None, url='/api/contacts/import/csv'):
            data = {'file': (_io.BytesIO(csv_text.encode()), 'list.csv')}
            if source is not None:
                data['list_source'] = source
            return client.post(url, data=data, content_type='multipart/form-data')
        importer = user()
        login(importer)
        r = upload()
        check('an import with no declared source is refused', r.status_code == 400
              and r.get_json().get('code') == 'list_source')
        r = upload('purchased')
        check('a purchased list is blocked, with the legal-text reason', r.status_code == 400
              and r.get_json()['error'] == legal_text.get('import_source_blocked'))
        check('…so is a scraped one', upload('scraped').status_code == 400)
        check('nothing was imported by the refused attempts', Contact.query.filter_by(user_id=importer.id).count() == 0)
        r = upload('own_contacts')
        rows = Contact.query.filter_by(user_id=importer.id).all()
        check('an own-contacts list imports, and each contact keeps its declared source',
              r.status_code == 200 and len(rows) == 2 and all(c.source == 'csv_import'
              and c.source_notes == 'List source: My own contacts or clients' for c in rows))
        login(admin)
        r = client.post('/api/admin/prospects/import', json={'csv': 'firm_name,website\nAcme,acme.com\n',
                                                             'list_source': 'purchased'})
        check('the admin prospects import refuses bought lists too', r.status_code == 400)
        login(importer)
        page = client.get('/contacts').get_data(as_text=True)
        check('the import form asks where the list came from', 'id="importListSource"' in page
              and "can't be imported" in page)
        check('Apollo-sourced contacts keep their source field', Contact.query.filter_by(source='apollo').count() >= 1)

    check('no network calls', not network, network)
    print('\n%d passed, %d failed' % (len(PASS), len(FAIL)))
    if FAIL:
        for label in FAIL:
            print('  FAILED: ' + label)
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
