"""SME console: artifacts on /sme/users/<id> show as Markdown, never raw JSON.

    python test_sme_artifacts.py
"""
import json
import os
import sys
import warnings
from datetime import datetime

warnings.filterwarnings('ignore')
os.environ.setdefault('DATABASE_URL', 'sqlite:///:memory:')
os.environ.setdefault('SECRET_KEY', 'test-only')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PASS, FAIL = [], []


def check(label, condition, detail=''):
    (PASS if condition else FAIL).append(label)
    print('  %s %s%s' % ('ok  ' if condition else 'FAIL', label, ('  -- %s' % (detail,)) if detail and not condition else ''))


CAMPAIGN = json.dumps({
    'version': '1.0', 'agent': 'cold_email_campaign',
    'prospects': [{'first_name': 'Ana', 'last_name': 'Ruiz', 'email': 'ana@x.com', 'job_title': 'CTO',
                   'company_name': 'Acme', 'crm_contact_id': None,
                   'sequence': [{'step': 1, 'subject': 'Quick idea', 'body': 'Hi Ana,\n\nLine two.',
                                 'send_status': 'draft', 'send_delay_days': 0},
                                {'step': 2, 'subject': 'Following up', 'body': 'Hi again.',
                                 'send_status': 'pending', 'send_delay_days': 7}]}]})
EMPTY = json.dumps({'version': '1.0', 'agent': 'consulting_outreach', 'prospects': [],
                    'research_summary': {'empty_reason': 'Connect Apollo to find prospects.',
                                         'total_researched': 0, 'pass1_duration_seconds': 2.1}})
SPONSOR = json.dumps({'agent': 'sponsorship_outreach', 'newsletter_name': 'Data Weekly',
                      'sponsors': [{'company': 'Snowpipe', 'contact_name': 'Lee',
                                    'email_draft': {'subject': 'Sponsor?', 'body': 'Hello'}}],
                      'rate_card': {'flat': 'Per issue'}, 'agreement_template': '# Agreement\n\nTerms here.'})
MARKDOWN = '# Positioning\n\n- Clear offer\n- **Bold** point\n\n| A | B |\n|---|---|\n| 1 | 2 |'


def main():
    from app import create_app
    from app.extensions import db
    app = create_app('testing')
    app.config.update(SQLALCHEMY_DATABASE_URI='sqlite:///:memory:', WTF_CSRF_ENABLED=False, PROPAGATE_EXCEPTIONS=True)
    with app.app_context():
        from flask import g
        from app import models as _models  # noqa: F401
        from app.models import bayesian as _b, sme as _s  # noqa: F401
        db.create_all()
        from app.services import artifact_actions as aa

        print('\n1. JSON artifacts become Markdown')
        md = aa.to_markdown(CAMPAIGN)
        check('campaign: a heading per prospect, title and company, each email with subject and body',
              '### Ana Ruiz' in md and '*CTO · Acme*' in md and '**Email 1: Quick idea**' in md
              and 'Hi Ana,\n\nLine two.' in md and '**Email 2: Following up**' in md, md)
        check('no JSON braces or internal fields left', '{' not in md and 'send_status' not in md
              and 'crm_contact_id' not in md and 'cold_email_campaign' not in md, md)
        md = aa.to_markdown(EMPTY)
        check('an empty campaign says why, once, without timings',
              md.count('Connect Apollo to find prospects.') == 1 and 'seconds' not in md.lower(), md)
        md = aa.to_markdown(SPONSOR)
        check('sponsorship: fields, sponsors with their email, rate card, agreement text',
              '**Newsletter name:** Data Weekly' in md and '### Snowpipe' in md and '**Email 1: Sponsor?**' in md
              and '## Rate card' in md and '# Agreement' in md, md)
        check('Markdown artifacts are returned as written', aa.to_markdown(MARKDOWN) == MARKDOWN)
        check('broken JSON is shown as written, not dropped', aa.to_markdown('{"a": ') == '{"a": ')
        html = aa.markdown_html('<script>alert(1)</script>\n\n{"x": "<img src=x onerror=1>"}')
        check('the HTML is escaped (no script or event handlers)', '<script' not in html and '<img' not in html, html)

        print('\n2. The SME console API')
        from app.models.user import User
        from app.models.profile import UserProfile
        from app.models.sme import SimiSME
        from app.models.simulation import Simulation
        from app.models.agent_action import AgentAction
        from utils.id_gen import generate_id
        for uid in ('SMEUSER01', 'SMEUSER02', 'MEMBRAR01'):
            db.session.add(User(id=uid, email='%s@t.local' % uid.lower(), full_name='T ' + uid, password_hash='x',
                                email_verified=True, onboarding_completed_at=datetime.utcnow()))
        writer = SimiSME(auth_user_id='SMEUSER01', first_name='Wren', last_name='Writer', email='wren@t.local',
                         status=SimiSME.STATUS_ACTIVE)
        writer.zones = ['marketing']
        finance = SimiSME(auth_user_id='SMEUSER02', first_name='Fin', last_name='Ance', email='fin@t.local',
                          status=SimiSME.STATUS_ACTIVE)
        finance.zones = ['finance']
        db.session.add_all([writer, finance])
        db.session.flush()
        db.session.add(UserProfile(user_id='MEMBRAR01', username='memberar', sme_id=writer.id))
        sim = Simulation(id=generate_id(), user_id='MEMBRAR01', name='Plan', status='completed')
        db.session.add(sim)
        for layer, kind, content in ((1, 'cold_email_campaign', CAMPAIGN), (2, 'positioning', MARKDOWN),
                                     (5, 'wealth_plan', '# Wealth\n\nPrivate figures 123.')):
            db.session.add(AgentAction(id=generate_id(), simulation_id=sim.id, layer_number=layer, action_type=kind,
                                       status=AgentAction.STATUS_COMPLETE, artifact=content,
                                       completed_at=datetime.utcnow()))
        db.session.commit()

        def as_user(client, uid):
            with client.session_transaction() as s:
                s['_user_id'] = uid
                s['_fresh'] = True
            g.pop('_login_user', None)

        c = app.test_client()
        as_user(c, 'SMEUSER01')
        r = c.get('/api/sme/users/MEMBRAR01/simulations/%s' % sim.id)
        arts = {a['action_type']: a for a in (r.get_json() or {}).get('artifacts', [])}
        check('the SME gets the artifacts', r.status_code == 200 and len(arts) == 3, r.status_code)
        camp = arts.get('cold_email_campaign', {})
        check('a JSON artifact arrives as Markdown and rendered HTML, not JSON',
              not camp.get('artifact', '{').lstrip().startswith('{') and '<h4>Ana Ruiz</h4>' in camp.get('artifact_html', '')
              and '<strong>Email 1: Quick idea</strong>' in camp.get('artifact_html', ''), camp.get('artifact_html', '')[:300])
        pos = arts.get('positioning', {})
        check('a Markdown artifact is rendered (heading, list, bold, table)',
              all(t in pos.get('artifact_html', '') for t in ('<h2>Positioning</h2>', '<li>', '<strong>Bold</strong>', '<table>')),
              pos.get('artifact_html', '')[:300])
        l5 = arts.get('wealth_plan', {})
        check('Layer 5 stays gated for a non-finance SME: placeholder only',
              l5.get('gated') is True and 'Private figures' not in json.dumps(l5)
              and 'Finance advisors only' in l5.get('artifact_html', ''), l5)
        as_user(c, 'SMEUSER02')
        r = c.get('/api/sme/users/MEMBRAR01/simulations/%s' % sim.id)
        check('an unassigned SME still gets nothing', r.status_code in (403, 404), r.status_code)

        print('\n3. The page')
        tpl = open('app/templates/sme/user_detail.html', encoding='utf-8').read()
        check('the page shows the rendered HTML, not the raw text', '${a.artifact_html' in tpl
              and 'esc((a.artifact' not in tpl and '.slice(0,1200)' not in tpl)

    print('\n%d passed, %d failed' % (len(PASS), len(FAIL)))
    for label in FAIL:
        print('  FAILED: ' + label)
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
