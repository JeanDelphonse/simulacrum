"""EXEC_PLAN P2 — derived execution config, contact picker, send-as-draft.

  * config/agent_execution.json equals what the rules derive (no hand edits)
  * the owner's classification decisions hold; nothing defaults to L4
  * the contact picker excludes or explains every protected contact
  * "Send to contacts" prepares per-recipient DRAFTS only: no email, no
    scheduled step, no network call

Runs on in-memory SQLite.

    python test_agent_execution.py
"""
import json
import os
import sys

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


def config_checks():
    print('\n1. Derived config')
    from app.services import agent_execution as ax
    from app.services.agent_registry import get_all_agents
    derived = ax.derive_config()
    on_disk = json.load(open(ax.CONFIG_PATH, encoding='utf-8'))
    check('config/agent_execution.json equals the derived config (regenerate with '
          '`python -m app.services.agent_execution --write`)', on_disk == derived)
    agents = derived['agents']
    check('every agent in agents.json has an entry',
          set(agents) == {a['action_type'] for a in get_all_agents()}, len(agents))
    allowed = {'shape', 'side_effect_class', 'hard_gate', 'default_autonomy',
               'supported_actions', 'suggested_tools', 'methods'}
    check('no fields beyond the spec', all(set(e) == allowed for e in agents.values()))

    cls = {k: v['side_effect_class'] for k, v in agents.items()}
    check('owner decision: role_search is none', cls['role_search'] == 'none')
    check('owner decision: newsletter and group_coaching are send_blast',
          cls['newsletter'] == cls['group_coaching'] == 'send_blast')
    check('owner decision: waitlist_landing_page stays send_blast', cls['waitlist_landing_page'] == 'send_blast')
    check('dca_schedule is money_move and hard-gated',
          cls['dca_schedule'] == 'money_move' and agents['dca_schedule']['hard_gate'])
    check('contractual agents (PandaDoc) are hard-gated',
          all(agents[a]['hard_gate'] for a in ('consulting_proposal', 'agreement_template', 'ip_licensing')))
    check('hard_gate is exactly the four dangerous classes',
          all(e['hard_gate'] == (e['side_effect_class'] in ax.HARD_GATE_CLASSES) for e in agents.values()))
    layer = {a['action_type']: a['layer'] for a in get_all_agents()}
    check('every L5 agent is generate-only (L0)',
          all(agents[a]['default_autonomy'] == 'L0' for a in agents if layer[a] == 5))
    check('nothing defaults to L4', all(e['default_autonomy'] in ('L0', 'L1', 'L2', 'L3') for e in agents.values()))
    check('send/sign/post agents default to L1',
          all(e['default_autonomy'] == 'L1' for a, e in agents.items()
              if layer[a] != 5 and e['side_effect_class'] in ('send_blast', 'contractual', 'public_post')))
    check('reversible / payment-link agents default to L2',
          all(e['default_autonomy'] == 'L2' for a, e in agents.items()
              if layer[a] != 5 and e['side_effect_class'] in ('reversible', 'money_link')))
    ce = agents['cold_email_campaign']
    check('cold email: sequence shape with the spec\'s method',
          ce['shape'] == 'sequence' and ce['methods']['steps'] ==
          ['research', 'personalize', 'send', 'wait', 'follow_up', 'stop_on_reply'], ce['methods'])
    check('sequence agents get per-step verbs', 'step_reschedule' in ce['supported_actions'])
    check('shapes: sow/saas/IPS are not mis-read as schedule/page',
          agents['sow_template']['shape'] == 'document' and agents['saas_product_spec']['shape'] == 'document'
          and agents['investment_policy']['shape'] == 'config', [agents[a]['shape'] for a in
                                                               ('sow_template', 'saas_product_spec', 'investment_policy')])
    check('send_to_contacts offered for send_blast and contact agents only',
          all(('send_to_contacts' in e['supported_actions']) ==
              (e['side_effect_class'] == 'send_blast' or a in
               {x['action_type'] for x in get_all_agents() if x.get('produces_contacts')})
              for a, e in agents.items()))
    check('integrations map to tools; primitives always present',
          'stripe_payment_link' in agents['rate_card']['suggested_tools']
          and all({'ai_refine', 'export_pdf'} <= set(e['suggested_tools']) for e in agents.values()))


def main():
    config_checks()
    import requests
    network = []
    requests.post = requests.get = lambda *a, **k: network.append(a[0] if a else k) or (_ for _ in ()).throw(
        AssertionError('network call'))

    app = create_app('testing')
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'

    with app.app_context():
        db.create_all()
        from datetime import datetime
        from flask import g
        from app.models.user import User
        from app.models.simulation import Simulation
        from app.models.agent_action import AgentAction
        from app.models.artifact import ArtifactVersion
        from app.models.artifact_exec import ArtifactUnit, ArtifactAction
        from app.models.contact import Contact
        from app.models.outreach_email import EmailLog, EmailSuppression
        from app.models.action_step import ActionStep
        from app.models.platform_settings import PlatformSetting
        from app.services import recipient_ledger

        owner = User(id=generate_id(), email='o@x.com', full_name='Owner', password_hash='x',
                     email_verified=True, onboarding_completed_at=datetime.utcnow())
        db.session.add(owner)
        db.session.flush()
        sim = Simulation(id=generate_id(), user_id=owner.id, name='S', expertise_zone='Ops', status='complete')
        db.session.add(sim)
        db.session.flush()

        def artifact(agent, content):
            a = AgentAction(id=generate_id(), simulation_id=sim.id, layer_number=1, action_type=agent,
                            status='complete', artifact=content, created_by=owner.id,
                            completed_at=datetime.utcnow())
            db.session.add(a)
            db.session.flush()
            db.session.add(ArtifactVersion(id=generate_id(), action_id=a.id, simulation_id=sim.id,
                                           layer_number=1, action_type=agent, version_number=1,
                                           content=content, is_current=True))
            db.session.commit()
            return a

        ref = artifact('referral_network', 'Hi [First Name], could you introduce me at [Company]? '
                                           'Book here: [Booking Link]')
        neg = artifact('negotiation_script', '# Script')
        camp = artifact('cold_email_campaign', json.dumps({'prospects': []}))

        def contact(first, email, company='Co', **kw):
            c = Contact(id=generate_id(), user_id=owner.id, first_name=first, last_name='X', email=email,
                        company_name=company, source='manual', **kw)
            db.session.add(c)
            db.session.flush()
            return c

        ok1 = contact('Ann', 'ann@a.com', 'Alpha')
        ok2 = contact('Bo', 'bo@b.com', '')                      # no company → unresolved [Company]
        dnc = contact('Dan', 'dan@d.com', do_not_contact=True)
        sup = contact('Sue', 'sue@s.com')
        ph = contact('[First Name 1]', 'p@enterprisecompany1.com')
        arc = contact('Arc', 'arc@a.com', is_archived=True)
        used = contact('Uma', 'uma@u.com')
        db.session.add(EmailSuppression(id=generate_id(), email='sue@s.com', reason='unsubscribe'))
        db.session.commit()
        recipient_ledger.record(sim.id, 'referral_network', ref.id, [{'email': 'uma@u.com'}])
        ids = {k: v.id for k, v in dict(ref=ref, neg=neg, camp=camp, ok1=ok1, ok2=ok2, dnc=dnc, sup=sup,
                                        ph=ph, arc=arc, used=used, owner=owner, sim=sim).items()}

        client = app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = ids['owner']
            sess['_fresh'] = True
        g.pop('_login_user', None)

        print('\n2. Contact picker')
        res = client.get('/api/artifacts/%s/contacts' % ids['ref'])
        d = res.get_json() or {}
        by = {c['id']: c for c in d.get('contacts', [])}
        check('picker answers', res.status_code == 200, res.status_code)
        check('eligible contacts are selectable', by[ids['ok1']]['eligible'] and by[ids['ok2']]['eligible'])
        check('archived contacts are left out', ids['arc'] not in by)
        for key, reason in (('dnc', 'Do not contact'), ('sup', 'Unsubscribed or bounced'),
                            ('ph', 'Placeholder, not a real person'), ('used', 'Already contacted by this agent')):
            check('%s shown but not selectable (%s)' % (key, reason),
                  not by[ids[key]]['eligible'] and by[ids[key]]['reason'] == reason, by[ids[key]])
        check('search narrows the list',
              [c['id'] for c in client.get('/api/artifacts/%s/contacts?q=alpha' % ids['ref']).get_json()['contacts']]
              == [ids['ok1']])
        check('agents without send_to_contacts refuse the picker',
              client.get('/api/artifacts/%s/contacts' % ids['neg']).status_code == 400)
        check('campaign artifacts refuse (they have their own send controls)',
              client.get('/api/artifacts/%s/contacts' % ids['camp']).status_code == 400)

        print('\n3. Prepare drafts (nothing sent)')
        res = client.post('/api/artifacts/%s/send-drafts' % ids['ref'], json={
            'contact_ids': [ids['ok1'], ids['ok2'], ids['dnc'], ids['sup'], ids['used']],
            'subject': 'Intro for [First Name]', 'body': ref.artifact})
        d = res.get_json() or {}
        drafts = {x['contact_id']: x for x in d.get('drafts', [])}
        check('drafts prepared for the eligible people only',
              res.status_code == 200 and set(drafts) == {ids['ok1'], ids['ok2']} and len(d['skipped']) == 3, d)
        a1 = drafts.get(ids['ok1'], {})
        check('merge fields resolved per recipient',
              a1.get('subject') == 'Intro for Ann' and 'Hi Ann' in a1.get('body', '')
              and 'at Alpha' in a1.get('body', ''), a1)
        check('unresolved placeholders flagged for the preview',
              any('[Booking Link]' in w for w in a1.get('warnings', []))
              and any('[Company]' in w for w in drafts.get(ids['ok2'], {}).get('warnings', [])))
        check('result says nothing was sent', d.get('sent') == 0)
        units = ArtifactUnit.query.filter_by(artifact_id=ids['ref']).all()
        check('drafts stored as variant units with status draft',
              len(units) == 2 and all(u.kind == 'variant' and u.status == 'draft' for u in units))
        check('no email logged, no step scheduled',
              EmailLog.query.count() == 0 and ActionStep.query.count() == 0)
        check('prepare_send logged in the action log',
              ArtifactAction.query.filter_by(artifact_id=ids['ref'], verb='prepare_send').count() == 1)
        check('recipients are not claimed in the ledger by a draft',
              'ann@a.com' not in recipient_ledger.used_keys(ids['sim'], 'referral_network'))

        res = client.post('/api/artifacts/%s/send-drafts' % ids['ref'], json={
            'contact_ids': [ids['ok1']], 'subject': '', 'body': 'x'})
        check('a subject is required', res.status_code == 400)
        PlatformSetting.set('exec_draft_max_recipients', '1')
        db.session.commit()
        res = client.post('/api/artifacts/%s/send-drafts' % ids['ref'], json={
            'contact_ids': [ids['ok1'], ids['ok2']], 'subject': 'S', 'body': 'B'})
        check('the admin batch cap is enforced', res.status_code == 400 and '1' in res.get_json()['message'])
        PlatformSetting.set('exec_draft_max_recipients', '50')
        db.session.commit()
        check('drafts can be listed', len(client.get('/api/artifacts/%s/send-drafts' % ids['ref'])
                                          .get_json()['drafts']) == 2)
        res = client.post('/api/artifacts/%s/send-drafts/discard' % ids['ref'], json={})
        check('drafts can be discarded', res.get_json().get('discarded') == 2
              and not client.get('/api/artifacts/%s/send-drafts' % ids['ref']).get_json()['drafts'])

        print('\n4. Page and log')
        page = client.get('/simulations/%s/artifacts/%s' % (ids['sim'], ids['ref'])).get_data(as_text=True)
        check('"Send to contacts" shown on a send-capable artifact', 'aabSendBtn' in page)
        page = client.get('/simulations/%s/artifacts/%s' % (ids['sim'], ids['neg'])).get_data(as_text=True)
        check('…and not on a generate-only one', 'aabSendBtn' not in page)
        client.get('/api/artifacts/%s/export.pdf' % ids['neg'])
        check('P1 verbs are logged too (export)',
              ArtifactAction.query.filter_by(artifact_id=ids['neg'], verb='export').count() == 1)
        check('no network calls at any point', not network, network)

    print('\n%d passed, %d failed' % (len(PASS), len(FAIL)))
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
