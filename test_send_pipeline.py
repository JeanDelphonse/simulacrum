"""EXEC_PLAN P5 — the safe send pipeline: confirmed draft batches only.

SendGrid is faked; requests is blocked so a stray call fails.

    python test_send_pipeline.py
"""
import os
import sys
from datetime import datetime

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


def main():
    import requests
    network = []
    requests.post = requests.get = requests.delete = (
        lambda *a, **k: network.append(a[0] if a else k) or (_ for _ in ()).throw(AssertionError('network')))

    sent = []

    class FakeResponse:
        def __init__(self, n):
            self.headers = {'X-Message-Id': 'msg-%d' % n}

    class FakeSendGrid:
        def __init__(self, api_key):
            self.api_key = api_key

        def send(self, message):
            sent.append((self.api_key, message.get()))
            return FakeResponse(len(sent))

    import sendgrid
    sendgrid.SendGridAPIClient = FakeSendGrid

    app = create_app('testing')
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
    app.config['SENDGRID_API_KEY'] = 'SG.platform-key'
    app.config['MAIL_DEFAULT_SENDER'] = 'simi@simulacrumai.io'
    from cryptography.fernet import Fernet
    app.config['ENCRYPTION_KEY'] = Fernet.generate_key().decode()

    with app.app_context():
        db.create_all()
        from flask import g
        from app.models.user import User
        from app.models.simulation import Simulation
        from app.models.agent_action import AgentAction
        from app.models.artifact import ArtifactVersion
        from app.models.artifact_exec import ArtifactUnit, ArtifactAction
        from app.models.agent_plan import ActionApproval
        from app.models.contact import Contact
        from app.models.outreach_email import EmailLog
        from app.models.observability import GuardrailRule
        from app.models.platform_settings import PlatformSetting
        from app.services import agent_plans, recipient_ledger, guardrail_service as gr

        owner = User(id=generate_id(), email='o@x.com', full_name='Dana Owner', password_hash='x',
                     email_verified=True, onboarding_completed_at=datetime.utcnow())
        other = User(id=generate_id(), email='x@x.com', full_name='X', password_hash='x',
                     email_verified=True, onboarding_completed_at=datetime.utcnow())
        db.session.add_all([owner, other])
        db.session.flush()
        sim = Simulation(id=generate_id(), user_id=owner.id, name='S', expertise_zone='Ops', status='complete')
        db.session.add(sim)
        db.session.flush()
        ref = AgentAction(id=generate_id(), simulation_id=sim.id, layer_number=1, action_type='referral_network',
                          status='complete', artifact='Hi [First Name], could you introduce me at [Company]?',
                          created_by=owner.id, completed_at=datetime.utcnow())
        db.session.add(ref)
        db.session.flush()
        db.session.add(ArtifactVersion(id=generate_id(), action_id=ref.id, simulation_id=sim.id, layer_number=1,
                                       action_type='referral_network', version_number=1, content=ref.artifact,
                                       is_current=True))

        def contact(first, email, company='Co', **kw):
            c = Contact(id=generate_id(), user_id=owner.id, first_name=first, last_name='X', email=email,
                        company_name=company, source='manual', **kw)
            db.session.add(c)
            db.session.flush()
            return c

        ann, bo, cy, dee, eve = (contact('Ann', 'ann@a.com', 'Alpha'), contact('Bo', 'bo@b.com', 'Beta'),
                                 contact('Cy', 'cy@c.com', 'Gamma'), contact('Dee', 'dee@d.com', ''),
                                 contact('Eve', 'eve@e.com', 'Epsilon'))
        db.session.commit()
        ids = {k: v.id for k, v in dict(ref=ref, ann=ann, bo=bo, cy=cy, dee=dee, eve=eve, owner=owner,
                                        other=other, sim=sim).items()}

        client = app.test_client()
        _post, _get, _del = client.post, client.get, client.delete
        client.post = lambda *a, **k: (g.pop('_login_user', None), _post(*a, **k))[1]
        client.get = lambda *a, **k: (g.pop('_login_user', None), _get(*a, **k))[1]
        client.delete = lambda *a, **k: (g.pop('_login_user', None), _del(*a, **k))[1]

        def login(uid):
            with client.session_transaction() as sess:
                sess['_user_id'] = uid
                sess['_fresh'] = True
            g.pop('_login_user', None)

        base = '/api/artifacts/%s/send-drafts' % ids['ref']

        def prepare(contact_ids, subject='Intro for [First Name]', body=None):
            return client.post(base, json={'contact_ids': contact_ids, 'subject': subject,
                                           'body': body or 'Hi [First Name], could you introduce me at [Company]?'}
                               ).get_json()

        def batch_of(batch_id):
            return next(b for b in client.get(base).get_json()['batches'] if b['batch_id'] == batch_id)

        def send(batch_id, confirm_hash, confirm=True):
            return client.post(base + '/send', json={'batch_id': batch_id, 'confirm_hash': confirm_hash,
                                                     'confirm': confirm})

        login(ids['owner'])
        print('\n1. Confirmation is required and bound to the previewed drafts')
        d = prepare([ids['ann'], ids['bo'], ids['cy'], ids['dee']])
        b = batch_of(d['batch_id'])
        listing = client.get(base).get_json()
        check('drafts view returns the batch hash and the sending account',
              b['count'] == 4 and len(b['hash']) == 64 and listing['sender']['ready']
              and listing['sender']['provider'] == 'simisi_sendgrid', listing['sender'])
        check('default sender is SimiSI with the user\'s name',
              listing['sender']['from_email'] == 'simi@simulacrumai.io'
              and listing['sender']['from_name'] == 'Dana Owner')
        res = send(d['batch_id'], b['hash'], confirm=False)
        check('no send without confirm=true', res.status_code == 400 and not sent)
        res = send(d['batch_id'], 'f' * 64)
        check('no send with a stale or wrong hash', res.status_code == 400 and 'changed' in res.get_json()['message']
              and not sent)
        unit = ArtifactUnit.query.filter_by(batch_id=d['batch_id'], contact_id=ids['cy']).first()
        unit.body_ref = unit.body_ref + ' P.S. edited after preview'
        db.session.commit()
        res = send(d['batch_id'], b['hash'])
        check('editing a draft after the preview invalidates the confirmation',
              res.status_code == 400 and not sent)
        b = batch_of(d['batch_id'])

        login(ids['other'])
        check('strangers cannot send', send(d['batch_id'], b['hash']).status_code == 403 and not sent)
        login(ids['owner'])

        print('\n2. A confirmed batch is sent, re-checked per recipient')
        cy_c = Contact.query.get(ids['cy'])
        cy_c.do_not_contact = True                               # became ineligible after the preview
        db.session.commit()
        res = send(d['batch_id'], b['hash'])
        r = res.get_json() or {}
        by = {x['contact_id']: x for x in r.get('results', [])}
        check('the batch sends', res.status_code == 200 and r.get('sent') == 2 and r.get('total') == 4, r)
        check('eligible recipients got their own merged email',
              by[ids['ann']]['status'] == 'sent' and by[ids['bo']]['status'] == 'sent')
        check('a recipient marked do-not-contact after the preview is skipped',
              by[ids['cy']]['status'] == 'skipped' and by[ids['cy']]['error'] == 'Do not contact', by[ids['cy']])
        check('a draft with an unresolved merge field is never sent',
              by[ids['dee']]['status'] == 'skipped' and '[Company]' in (by[ids['dee']]['error'] or ''),
              by[ids['dee']])
        msgs = {m['personalizations'][0]['to'][0]['email']: (key, m) for key, m in sent}
        ann_key, ann_msg = msgs.get('ann@a.com', (None, {}))
        check('sent through SimiSI\'s SendGrid', ann_key == 'SG.platform-key' and len(sent) == 2)
        check('from the platform sender with the user\'s name',
              ann_msg.get('from') == {'email': 'simi@simulacrumai.io', 'name': 'Dana Owner'}, ann_msg.get('from'))
        html = ann_msg.get('content', [{}])[0].get('value', '')
        check('body is the merged draft', 'Hi Ann' in html and 'at Alpha' in html, html[:120])
        check('every email carries an unsubscribe link', 'Unsubscribe' in html and '/unsubscribe' in html, html[-300:])
        check('…and List-Unsubscribe headers',
              'List-Unsubscribe' in ann_msg.get('headers', {})
              and ann_msg['headers'].get('List-Unsubscribe-Post') == 'List-Unsubscribe=One-Click')
        units = {u.contact_id: u for u in ArtifactUnit.query.filter_by(batch_id=d['batch_id']).all()}
        check('units record sent / skipped with time and email log',
              units[ids['ann']].status == 'sent' and units[ids['ann']].sent_at is not None
              and units[ids['ann']].email_log_id is not None and units[ids['cy']].status == 'skipped')
        check('emails logged against this artifact',
              EmailLog.query.filter_by(action_id=ids['ref']).count() == 2)
        check('the confirmation is recorded as a hard-gate approval',
              ActionApproval.query.filter_by(target_type='send_batch', target_id=d['batch_id'],
                                             required_reason='hard_gate').count() == 1)
        check('send logged in the artifact action log',
              ArtifactAction.query.filter_by(artifact_id=ids['ref'], verb='send').count() == 1)
        check('recipients are now claimed for this agent',
              {'ann@a.com', 'bo@b.com'} <= recipient_ledger.used_keys(ids['sim'], 'referral_network'))
        res = send(d['batch_id'], b['hash'])
        check('a batch cannot be sent twice', res.status_code == 400 and len(sent) == 2)
        d2 = prepare([ids['ann'], ids['eve']])
        check('already-contacted people are not drafted again',
              [x['contact_id'] for x in d2['drafts']] == [ids['eve']], d2)

        print('\n3. Limits, autonomy and guardrails')
        b2 = batch_of(d2['batch_id'])
        PlatformSetting.set('exec_send_max_per_day', '2')
        db.session.commit()
        res = send(d2['batch_id'], b2['hash'])
        check('the daily send limit is enforced', res.status_code == 400 and 'limit' in res.get_json()['message']
              and len(sent) == 2, res.get_json())
        PlatformSetting.set('exec_send_max_per_day', '50')
        db.session.commit()
        agent_plans.set_level(ids['owner'], 'referral_network', 'L0')
        res = send(d2['batch_id'], b2['hash'])
        check('an agent set to L0 (generate-only) cannot send', res.status_code == 400 and len(sent) == 2)
        agent_plans.set_level(ids['owner'], 'referral_network', 'L1')

        rule_def = {'mode': 'deny', 'tools': ['send_email']}
        db.session.add(GuardrailRule(id=generate_id(), **{k: v for k, v in gr.validate_rule(dict(
            name='No sends', rule_type='tool_allowlist', scope='global', scope_ref=None, action='block',
            severity='high', definition=rule_def)).items() if k != 'definition'}, definition_json=rule_def))
        db.session.commit()
        res = send(d2['batch_id'], b2['hash'])
        r = res.get_json() or {}
        check('a blocking guardrail stops the send at the tool boundary',
              res.status_code == 200 and r.get('sent') == 0 and r.get('blocked') and len(sent) == 2, r)
        GuardrailRule.query.delete()
        db.session.commit()

        print('\n4. "Your own account" (admin setting exec_send_provider)')
        PlatformSetting.set('exec_send_provider', 'own_sendgrid')
        db.session.commit()
        d3 = prepare([ids['eve']])
        info = client.get('/api/send-account').get_json()
        check('without a connected account, sending is not ready',
              info['provider'] == 'own_sendgrid' and not info['ready'] and 'connect' in info['message'].lower(), info)
        res = send(d3['batch_id'], batch_of(d3['batch_id'])['hash'])
        check('…and a send is refused', res.status_code == 400 and len(sent) == 2)
        res = client.post('/api/send-account', json={'api_key': 'not-a-key', 'from_email': 'dana@dana.com'})
        check('a malformed API key is refused', res.status_code == 400)
        res = client.post('/api/send-account', json={'api_key': 'SG.user-own-key-123456789',
                                                     'from_email': 'Dana@Dana.com', 'from_name': 'Dana'})
        info = res.get_json() or {}
        check('connecting the account makes sending ready',
              res.status_code == 200 and info['ready'] and info['from_email'] == 'dana@dana.com', info)
        from app.models.integration import UserIntegration
        rec = UserIntegration.query.filter_by(user_id=ids['owner'], provider='sendgrid').first()
        check('the key is stored encrypted', rec and 'user-own-key' not in rec.access_token_enc)
        res = send(d3['batch_id'], batch_of(d3['batch_id'])['hash'])
        key, msg = sent[-1] if sent else (None, {})
        check('sent through the user\'s own SendGrid, from their verified sender',
              res.status_code == 200 and res.get_json()['sent'] == 1 and key == 'SG.user-own-key-123456789'
              and msg.get('from') == {'email': 'dana@dana.com', 'name': 'Dana'}, (key, msg.get('from')))
        res = client.delete('/api/send-account')
        check('the account can be disconnected', res.status_code == 200 and not res.get_json()['ready']
              and UserIntegration.query.filter_by(user_id=ids['owner'], provider='sendgrid').count() == 0)
        PlatformSetting.set('exec_send_provider', 'simisi_sendgrid')
        db.session.commit()

        print('\n5. Admin setting')
        admin = User(id=generate_id(), email='a@x.com', full_name='Admin', password_hash='x', is_admin=True,
                     email_verified=True, onboarding_completed_at=datetime.utcnow())
        db.session.add(admin)
        db.session.commit()
        login(admin.id)
        res = client.put('/api/admin/settings/exec_send_provider', json={'value': 'own_sendgrid'})
        check('admin can switch the sending account', res.status_code == 200
              and PlatformSetting.get('exec_send_provider') == 'own_sendgrid', res.status_code)
        page = client.get('/admin').get_data(as_text=True)
        check('the admin page shows the setting', 'execSendProvider' in page and 'Your own account' in page)
        check('no network calls at any point', not network, network)

    print('\n%d passed, %d failed' % (len(PASS), len(FAIL)))
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
