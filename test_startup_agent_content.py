"""Founder-approved agent analyses on the startup page (sql/076).

Layer 1 (Validate) analyses beside their public sections for every visitor;
every layer in the investor view's "full picture" for invited and paying
investors. Founders choose per agent (publishing-decision holder only); the
reviewed version is pinned; outreach/internal agents never show; public and
investor copy pass the page's compliance checks; contact details masked; raw
HTML escaped. In-memory SQLite; network and email blocked.

    python test_startup_agent_content.py
"""
import os
import re
import sys
from datetime import datetime, timedelta

os.environ.setdefault('DATABASE_URL', 'sqlite:///:memory:')
os.environ.setdefault('SECRET_KEY', 'test-only')
ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

PASS, FAIL = [], []


def check(label, condition, detail=''):
    (PASS if condition else FAIL).append(label)
    line = '  {} {}{}'.format('ok  ' if condition else 'FAIL', label, ('  -- ' + str(detail)) if detail and not condition else '')
    enc = sys.stdout.encoding or 'ascii'
    print(line.encode(enc, 'replace').decode(enc))


def main():
    import requests
    requests.post = requests.get = lambda *a, **k: (_ for _ in ()).throw(AssertionError('network'))
    import tempfile
    from app import create_app
    from app.extensions import db
    from utils.id_gen import generate_id
    app = create_app('testing')
    app.config.update(SQLALCHEMY_DATABASE_URI='sqlite:///:memory:', WTF_CSRF_ENABLED=False,
                      UPLOAD_FOLDER=tempfile.mkdtemp(prefix='stagent_'), BASE_URL='https://simisi.io')
    from app.services import email_service
    email_service._send = lambda *a, **k: None

    with app.app_context():
        db.create_all()
        from flask import g
        from app.models.founder import StartupPage, VentureAgentOutput, VentureMember
        from app.models.user import User
        from app.services import founder_config, founder_feature, founder_ventures
        from app.services import startup_access_service as sas
        from app.services import startup_page_service as sps
        now = datetime.utcnow()

        def user(email, admin=False):
            u = User(id=generate_id(), email=email, full_name=email.split('@')[0].title(), password_hash='x',
                     is_admin=admin, email_verified=True, onboarding_completed_at=now)
            db.session.add(u)
            db.session.commit()
            return u

        A_ID = user('admin@x.com', admin=True).id
        O_ID = user('owner@x.com').id
        C_ID = user('cof@x.com').id
        X_ID = user('outsider@x.com').id
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
                    s.pop(sas.SESSION_KEY, None)
                    s.pop(sas.SESSION_LINKS_KEY, None)

        founder_feature.set_mode('founder_track', 'on', A_ID)
        v = founder_ventures.create_venture(db.session.get(User, O_ID), 'Dockwise', 'Dock scheduling', 'seed')
        v.paid_at = now
        db.session.add(VentureMember(venture_id=v.id, user_id=C_ID, role='cofounder', status='active', joined_at=now))
        page = sps.ensure_page(v)
        page.status, page.name_check, page.published_at = 'published', 'clear', now
        page.problem = 'Trucks wait hours at the dock.'
        page.ask_round, page.ask_amount = 'SECRETROUND', 1500000
        db.session.commit()
        V, SLUG = v.id, page.slug

        def output(key, content, version=1, minutes=0):
            o = VentureAgentOutput(venture_id=V, plan_step_id='STEP%05d' % version, agent_key=key, content=content,
                                   version=version, created_at=now + timedelta(minutes=minutes))
            db.session.add(o)
            db.session.commit()
            return o

        output('fnd_problem_statement', '## Problem\n\n**Warehouse managers** lose four hours a day.\n\n'
                                        'Contact ops@acme.com for the data.\n\n<script>alert(1)</script>')
        output('fnd_market_sizing', 'Bottom-up: 14,000 warehouses x $3,000 a year.')
        output('fnd_runway_budget', '| Month | Burn |\n|---|---|\n| Jan | $8,000 |')
        output('fnd_roadmap', 'MVP in March, pilot in May.')
        output('fnd_investor_matching', 'SECRET INVESTOR LIST')
        output('fnd_outbound_pipeline', 'SECRET PROSPECTS')
        output('fnd_competitor_scan', 'Investors get a guaranteed 10x return.')

        def editor():
            login(O_ID)
            return client.get('/founder/v/%s/page' % V).get_data(as_text=True)

        def setc(key, audience, uid=O_ID):
            login(uid)
            r = client.post('/api/founder/v/%s/page/agent-content' % V, json={'agent_key': key, 'audience': audience})
            return r.status_code, (r.get_json(silent=True) or {})

        def public():
            login(None)
            return client.get('/startup/%s' % SLUG).get_data(as_text=True)

        print('\n1. The editor offers the analyses, founders choose')
        html = editor()
        check('the editor has a "From your agents" card', 'id="peAgents"' in html and 'From your agents' in html)
        check('analyses from every layer are offered', all(k in html for k in (
            'data-agent-key="fnd_problem_statement"', 'data-agent-key="fnd_runway_budget"',
            'data-agent-key="fnd_roadmap"', 'data-agent-key="fnd_market_sizing"')))
        check('outreach and internal agents are never offered',
              'fnd_investor_matching' not in html and 'fnd_outbound_pipeline' not in html)
        block = html.split('data-agent-key="fnd_runway_budget"')[1].split('</li>')[0]
        check('a non-Validate analysis cannot be offered for the public page', 'value="public"' not in block)
        block = html.split('data-agent-key="fnd_problem_statement"')[1].split('</li>')[0]
        check('a Validate analysis can', 'value="public"' in block and 'The problem, analysed' in block)
        check('nothing is shown until the founders choose', 'class="st-agent' not in public())

        print('\n2. Rules for choosing')
        code, body = setc('fnd_problem_statement', 'public', uid=C_ID)
        check('a co-founder without the publishing decision cannot choose', code == 400, (code, body))
        code, _ = setc('fnd_problem_statement', 'public', uid=X_ID)
        check('a non-member cannot', code in (403, 404), code)
        code, body = setc('fnd_runway_budget', 'public')
        check('a non-Validate analysis is refused for the public page', code == 400 and 'Validate' in body.get('error', ''))
        code, body = setc('fnd_investor_matching', 'investor')
        check('an outreach agent is refused even for investors', code == 400)
        code, body = setc('fnd_competitor_scan', 'public')
        check('an analysis that breaks the page rules is refused for the public page',
              code == 400 and 'return' in body.get('error', '').lower(), body)
        code, body = setc('fnd_competitor_scan', 'investor')
        check('... and for investors', code == 400, body)
        code, body = setc('fnd_customer_interview', 'public')
        check('an agent with no output yet is refused', code == 400 and 'no output' in body.get('error', ''))

        print('\n3. Public page: Validate analyses beside their sections')
        code, body = setc('fnd_problem_statement', 'public')
        check('the owner shows the Problem Statement publicly', code == 200 and body['selection']['audience'] == 'public',
              body)
        setc('fnd_market_sizing', 'public')
        setc('fnd_runway_budget', 'investor')
        setc('fnd_roadmap', 'investor')
        html = public()
        prob = html.split('id="problem"')[1].split('id="solution"')[0]
        check('the analysis sits in Problem and customer', 'data-agent="fnd_problem_statement"' in prob
              and "Prepared with SimiSI's Problem Statement agent" in prob and 'reviewed and shared by the founders' in prob)
        check('it is rendered from Markdown', '<strong>Warehouse managers</strong>' in prob)
        check('raw HTML is escaped, never run', '<script>alert(1)</script>' not in html)
        check('contact details are masked', 'ops@acme.com' not in html)
        mkt = html.split('id="market"')[1].split('id="team"')[0]
        check('Market Sizing sits in Market and business model', 'data-agent="fnd_market_sizing"' in mkt)
        check('investor-only analyses stay off the public page',
              'fnd_runway_budget' not in html and 'fnd_roadmap' not in html and 'id="full-picture"' not in html)
        check('the ask stays off the public page', 'SECRETROUND' not in html)

        print('\n4. Investor view: every layer, for invited and paying investors')
        founder_config.set('fnd_counsel_signoff_at', '2026-11-01', A_ID)
        founder_feature.set_mode('founder_investor', 'on', A_ID)
        link = sas.issue_link(db.session.get(StartupPage, page.id), db.session.get(User, O_ID), 'Ivy')
        login(None)
        inv = client.get('/startup/%s/i/%s' % (SLUG, link.token), follow_redirects=True).get_data(as_text=True)
        check('an invited investor sees "The full picture, layer by layer"', 'id="full-picture"' in inv
              and 'The full picture, layer by layer' in inv)
        full = inv.split('id="full-picture"')[1]
        check('analyses are grouped by layer (Validate, Build, Operate)',
              'data-agent="fnd_problem_statement"' in full and 'data-agent="fnd_roadmap"' in full
              and 'data-agent="fnd_runway_budget"' in full
              and full.index('L1 · Validate') < full.index('L2 · Build') < full.index('L4 · Operate'))
        check('tables render', '<th>Month</th>' in full)
        check('layers with nothing shared say so', 'Not shared yet' in full)
        check('the investor view lists it under "On this page"', 'href="#full-picture"' in inv)
        check('outreach agents never reach investors', 'SECRET INVESTOR LIST' not in inv and 'SECRET PROSPECTS' not in inv)
        from app.models.founder import InvestorAccess
        db.session.add(InvestorAccess(startup_page_id=page.id, email='payer@fund.vc', kind='paid', status='requested',
                                      created_at=now))
        db.session.commit()
        row = InvestorAccess.query.filter_by(email='payer@fund.vc').first()
        ctx = sas.investor_context(db.session.get(StartupPage, page.id), row)
        check('a paying investor awaiting approval also gets the full picture (but not the ask)',
              ctx['agent']['investor_total'] == 4 and not ctx.get('ask'), (ctx['agent']['investor_total'], ctx.get('ask')))

        print('\n5. The reviewed version is pinned')
        output('fnd_problem_statement', 'A NEWER UNREVIEWED VERSION', version=2, minutes=5)
        check('a newer agent run does not change the page', 'A NEWER UNREVIEWED VERSION' not in public())
        html = editor()
        block = html.split('data-agent-key="fnd_problem_statement"')[1].split('</li>')[0]
        check('the editor flags the newer version', 'data-agent-update' in block and 'Show version 2' in block)
        setc('fnd_problem_statement', 'public')
        check('approving it shows the new version', 'A NEWER UNREVIEWED VERSION' in public())
        code, _ = setc('fnd_problem_statement', 'none')
        check('"Not shown" removes it', code == 200 and 'fnd_problem_statement' not in public())

        print('\n6. Deploy')
        check('the migration exists', os.path.exists(os.path.join(ROOT, 'sql', '076_startup_agent_content.sql')))

    print('\n%d passed, %d failed' % (len(PASS), len(FAIL)))
    for label in FAIL:
        print('  FAILED: ' + label)
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
