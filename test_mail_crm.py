"""PRD-SIM-MAIL-001 phase 1: Email-to-CRM contact extraction + the admin switch.

IMAP, Google and Claude are faked; requests is blocked so a stray call fails.

    python test_mail_crm.py
"""
import imaplib
import json
import os
import re
import sys
from datetime import datetime, timedelta
from email.message import EmailMessage
from types import SimpleNamespace

os.environ.setdefault('DATABASE_URL', 'sqlite:///:memory:')
os.environ.setdefault('SECRET_KEY', 'test-only')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PASS, FAIL = [], []


def check(label, condition, detail=''):
    (PASS if condition else FAIL).append(label)
    line = '  {} {}{}'.format('ok  ' if condition else 'FAIL', label, ('  -- ' + str(detail)) if detail else '')
    enc = sys.stdout.encoding or 'ascii'
    print(line.encode(enc, 'replace').decode(enc))


SIGNATURE = ['Best,', 'Priya Raman', 'VP Engineering | Northwind Analytics', 'm: (415) 555-0134',
             'linkedin.com/in/priyaraman', 'San Francisco, CA']


def msg(frm, to, subject='Hi', body='Hello', cc=None, headers=None, date=None):
    m = EmailMessage()
    m['From'], m['To'], m['Subject'] = frm, to, subject
    if cc:
        m['Cc'] = cc
    m['Date'] = (date or datetime.utcnow()).strftime('%a, %d %b %Y %H:%M:%S +0000')
    m['Message-ID'] = '<%s@test>' % abs(hash((frm, to, subject, body)))
    for k, v in (headers or {}).items():
        m[k] = v
    m.set_content(body)
    return m.as_bytes()


class FakeIMAP:
    MAILBOXES = {}
    PASSWORD = 'app-pass-123'
    selected_log = []

    def __init__(self, host, port):
        self.host, self.folder = host, None

    def login(self, user, pw):
        if pw != self.PASSWORD:
            raise imaplib.IMAP4.error('AUTHENTICATIONFAILED')

    def logout(self):
        pass

    def list(self):
        return 'OK', [b'(\\HasNoChildren) "/" "INBOX"', b'(\\HasNoChildren \\Sent) "/" "Sent"',
                      b'(\\HasNoChildren \\Junk) "/" "Spam"']

    def select(self, folder, readonly=True):
        self.folder = folder.strip('"')
        FakeIMAP.selected_log.append(self.folder)
        return 'OK', [str(len(self.MAILBOXES.get(self.folder, {}))).encode()]

    def response(self, code):
        return 'OK', [b'7']

    def uid(self, cmd, *args):
        box = self.MAILBOXES.get(self.folder, {})
        if cmd == 'SEARCH':
            crit = list(args[1:])
            uids = sorted(box)
            if 'UID' in crit:
                lo, hi = crit[crit.index('UID') + 1].split(':')
                lo = int(lo)
                hi = max(box) if hi == '*' and box else (int(hi) if hi != '*' else 0)
                uids = [u for u in uids if lo <= u <= hi] or ([max(box)] if args[-1].endswith(':*') and box else [])
            return 'OK', [' '.join(str(u) for u in uids).encode()]
        if cmd == 'FETCH':
            spec = args[1]
            out = []
            for u in [int(x) for x in args[0].split(',')]:
                raw = box[u]
                if 'HEADER.FIELDS' in spec:
                    part = raw.split(b'\n\n', 1)[0] + b'\n\n'
                else:
                    part = raw
                out.append((b'%d (UID %d BODY[] {%d}' % (u, u, len(part)), part))
                out.append(b')')
            return 'OK', out
        return 'NO', []


class FakeAI:
    def __init__(self):
        self.calls = 0
        self.messages = self

    def create(self, **kw):
        self.calls += 1
        sig = kw['messages'][0]['content']
        data = {'first_name': None, 'last_name': None, 'job_title': None, 'company': None,
                'city': None, 'region': None, 'country': None}
        if 'Priya Raman' in sig:
            data.update(first_name='Priya', last_name='Raman', job_title='VP Engineering',
                        company='Northwind Analytics', city='San Francisco', region='CA', country='United States')
        return SimpleNamespace(content=[SimpleNamespace(text=json.dumps(data))])


class FakeResp:
    def __init__(self, data, status=200):
        self._d, self.status_code = data, status

    def json(self):
        return self._d


class FakeGoogle:
    def __init__(self):
        self.calls = []

    def post(self, url, data=None, params=None, timeout=None):
        self.calls.append(url)
        if url.endswith('/token'):
            return FakeResp({'access_token': 'at', 'refresh_token': 'rt',
                             'scope': 'openid email https://www.googleapis.com/auth/contacts.readonly '
                                      'https://www.googleapis.com/auth/contacts.other.readonly'})
        return FakeResp({})

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append(url)
        if 'userinfo' in url:
            return FakeResp({'email': 'dana@gmail.com'})
        if 'otherContacts' in url:
            return FakeResp({'otherContacts': [
                {'resourceName': 'otherContacts/1', 'names': [{'displayName': 'Leo Park', 'givenName': 'Leo', 'familyName': 'Park'}],
                 'emailAddresses': [{'value': 'leo@parkdesign.co'}], 'phoneNumbers': [{'value': '+1 212 555 0199'}]},
                {'resourceName': 'otherContacts/2', 'names': [{'displayName': 'Acme Updates'}],
                 'emailAddresses': [{'value': 'no-reply@acme.com'}]}], 'nextSyncToken': 's1'})
        if 'connections' in url:
            return FakeResp({'connections': [
                {'resourceName': 'people/c9', 'names': [{'displayName': 'Mia Chen', 'givenName': 'Mia', 'familyName': 'Chen'}],
                 'emailAddresses': [{'value': 'mia@harbor.vc'}],
                 'organizations': [{'name': 'Harbor Ventures', 'title': 'General Partner'}]}], 'nextSyncToken': 's2'})
        return FakeResp({}, 404)


def main():
    import requests
    network = []
    requests.post = requests.get = (lambda *a, **k: network.append(a[0] if a else k)
                                    or (_ for _ in ()).throw(AssertionError('network')))

    from app import create_app
    from app.extensions import db
    from utils.id_gen import generate_id
    app = create_app('testing')
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
    from cryptography.fernet import Fernet
    app.config['ENCRYPTION_KEY'] = Fernet.generate_key().decode()
    app.config['GOOGLE_CLIENT_ID'], app.config['GOOGLE_CLIENT_SECRET'] = 'gid', 'gsecret'

    with app.app_context():
        db.create_all()
        from flask import g
        from app.models.user import User
        from app.models.contact import Contact
        from app.models.mail_crm import (ContactDncFlag, ContactFieldSource, EmailAccount, FeatureChange,
                                         ReviewItem, SenderSignal, SyncRun, UserFeatureOverride, email_hash)
        from app.models.platform_settings import PlatformSetting
        from app.models.action_step import ActionStep
        from app.models.agent_action import AgentAction
        from app.models.simulation import Simulation
        from app.services import (mail_contacts, mail_extract, mail_feature, mail_scoring, mail_sync)
        from app.services.outreach_email_service import send_outreach_email

        mail_sync.IMAP_FACTORY = FakeIMAP
        ai = FakeAI()
        mail_sync.AI_CLIENT = ai
        from app.services import contact_lookup  # noqa: F401
        mail_contacts.domain_has_mx = lambda d: d not in ('nomail.example',)   # no DNS in tests

        now = datetime.utcnow()
        dana = User(id=generate_id(), email='dana@northbeam.io', full_name='Dana Okafor', password_hash='x',
                    email_verified=True, onboarding_completed_at=now)
        admin = User(id=generate_id(), email='admin@simisi.io', full_name='Admin', password_hash='x', is_admin=True,
                     email_verified=True, onboarding_completed_at=now)
        other = User(id=generate_id(), email='o@x.com', full_name='Other', password_hash='x',
                     email_verified=True, onboarding_completed_at=now)
        db.session.add_all([dana, admin, other])
        db.session.commit()
        D, A, O = dana.id, admin.id, other.id

        client = app.test_client()
        for verb in ('get', 'post', 'put', 'delete'):
            orig = getattr(client, verb)
            setattr(client, verb, (lambda o: lambda *a, **k: (g.pop('_login_user', None), o(*a, **k))[1])(orig))

        def login(uid):
            with client.session_transaction() as s:
                s['_user_id'], s['_fresh'] = uid, True
            g.pop('_login_user', None)

        L = mail_feature.setting_list
        P = dict(automated_patterns=L('mail_automated_patterns'), marketing_domains=L('mail_marketing_domains'),
                 role_prefixes=L('mail_role_prefixes'))

        print('\n1. Real-correspondent scoring')
        F = mail_scoring.SenderFacts
        v = mail_scoring.score_sender(F('sam@acme.com', 'Sam Lee', user_sent_to=True, received_count=3), **P)
        check('someone the user wrote to: import, Warm', v.decision == 'import' and v.tier == 'warm' and v.score >= 90, v)
        v = mail_scoring.score_sender(F('noreply@shop.com', 'Shop'), **P)
        check('noreply address: never imported', v.decision == 'skip' and 'Automated' in v.reasons[0])
        v = mail_scoring.score_sender(F('news@brand.com', 'Brand', list_headers=True), **P)
        check('mailing-list mail never answered: skipped', v.decision == 'skip')
        v = mail_scoring.score_sender(F('pat@brand.com', 'Pat Diaz', list_headers=True, user_sent_to=True), **P)
        check('…unless the user wrote to them', v.decision == 'import')
        v = mail_scoring.score_sender(F('x@em.mailchimp.com', 'X Y'), **P)
        check('marketing-platform domain: skipped', v.decision == 'skip')
        v = mail_scoring.score_sender(F('in@spam.com', 'A B', in_spam_folder=True), **P)
        check('spam folder: skipped', v.decision == 'skip')
        v = mail_scoring.score_sender(F('alex.kim@corp.com', 'Alex Kim', received_count=1), **P)
        check('one unanswered inbound from a real-looking person: review queue', v.decision == 'review', v)
        v = mail_scoring.score_sender(F('alex.kim@corp.com', 'Alex Kim', received_count=1, signature_person=True), **P)
        check('…one message with a signature is still only a review', v.decision == 'review', v)
        v = mail_scoring.score_sender(F('alex.kim@corp.com', 'Alex Kim', received_count=2, signature_person=True), **P)
        check('…two messages with a person\'s signature: imported, Known', v.decision == 'import' and v.tier == 'known', v)
        v = mail_scoring.score_sender(F('deals@corp.com', 'Corp Deals Team', received_count=1, max_recipients=40), **P)
        check('brand name sent to many: skipped', v.decision == 'skip', v)
        v = mail_scoring.score_sender(F('alex.kim@corp.com', 'Alex Kim', received_count=4, signature_person=True,
                                        auth_failed=True), **P)
        check('failed authentication is a strong negative', v.decision != 'import', v)
        v = mail_scoring.score_sender(F('sales@corp.com', 'Corp', user_sent_to=True), **P)
        check('role address: imported but tier "role"', v.tier == 'role')
        PlatformSetting.set('mail_score_import', '95')
        db.session.commit()
        check('thresholds are admin settings', mail_feature.setting('mail_score_import') == 95)
        PlatformSetting.set('mail_score_import', '70')
        db.session.commit()

        print('\n2. Field extraction')
        check('"Last, First" display names', mail_extract.split_name('Raman, Priya', 'p@x.com')[:2] == ('Priya', 'Raman'))
        check('suffixes dropped', mail_extract.split_name('John Smith Jr.', 'j@x.com')[:2] == ('John', 'Smith'))
        f, l, src, conf = mail_extract.split_name('', 'maria.lopez@x.com')
        check('name guessed from the address is low confidence', (f, l) == ('Maria', 'Lopez') and conf < 0.5)
        body = 'Thanks!\n\n' + '\n'.join(SIGNATURE) + '\n\nOn Tue, Jan 2, 2026 Dana wrote:\n> earlier text\n> Dana Okafor'
        win = mail_extract.signature_window(body, 15)
        check('quoted replies are stripped from the signature window', 'earlier text' not in ' '.join(win)
              and 'Priya Raman' in win)
        rules = mail_extract.rule_fields(win)
        check('phone in international format with its label', rules['phones'] == [{'e164': '+14155550134', 'label': 'mobile'}])
        check('LinkedIn profile link', rules['linkedin_url'] == 'https://linkedin.com/in/priyaraman')
        check('company from domain, skipped for free-mail', mail_extract.company_from_domain('northwind-analytics.com', [])
              == 'Northwind Analytics' and mail_extract.company_from_domain('gmail.com', ['gmail.com']) is None)
        check('department inferred from title', mail_extract.infer_department('VP Engineering') == 'Engineering')
        check('recruiter inferred from title', mail_extract.infer_role('Senior Technical Recruiter', 'x.com', set(), []) == 'recruiter')

        print('\n3. Feature switch')
        check('phase 1 default: allowlist only', mail_feature.mode('email_to_crm') == 'allowlist')
        check('a user not on the allowlist is off', not mail_feature.is_enabled(D))
        login(D)
        res = client.post('/api/mail/accounts/imap', json={'address': 'dana@northbeam.io', 'password': 'x',
                                                         'host': 'imap.northbeam.io'})
        check('connecting is refused while not allowed', res.status_code == 403)
        try:
            mail_feature.set_user([D], 'allowed', '', A)
            check('a reason is required', False)
        except ValueError:
            check('a reason is required', True)
        mail_feature.set_user([D], 'allowed', 'Beta tester', A)
        check('allowlisted user is on', mail_feature.is_enabled(D) and mail_feature.is_enabled(D, 'imap'))
        orig_mode = mail_feature.mode
        import app.models.mail_crm as mc
        real_q = mc.FeatureFlag.query
        class Boom:
            def filter_by(self, **k): raise RuntimeError('db down')
        mc.FeatureFlag.query = Boom()
        check('fails closed when the switch can\'t be read', not mail_feature.is_enabled(D))
        mc.FeatureFlag.query = real_q

        print('\n4. Connecting IMAP')
        res = client.post('/api/mail/accounts/imap', json={'address': 'dana@gmail.com', 'password': 'x'})
        check('Gmail addresses are sent to "Connect Gmail" (no IMAP body reading for Gmail)',
              res.status_code == 400 and 'Connect Gmail' in res.get_json()['message'])
        res = client.post('/api/mail/accounts/imap', json={'address': 'dana@northbeam.io', 'password': 'wrong',
                                                         'host': 'imap.northbeam.io'})
        check('a bad app password is refused at connect time', res.status_code == 400
              and 'app password' in res.get_json()['message'])
        FakeIMAP.MAILBOXES = {
            'Sent': {1: msg('Dana Okafor <dana@northbeam.io>', 'Sam Lee <sam@acmecorp.com>', 'Proposal',
                            cc='Ravi Patel <ravi@acmecorp.com>'),
                     2: msg('Dana Okafor <dana@northbeam.io>', 'Recruiting <jobs@talentco.com>', 'Re: role')},
            'INBOX': {
                10: msg('Priya Raman <priya@northwind-analytics.com>', 'dana@northbeam.io', 'Intro',
                        body='Great to meet.\n\n' + '\n'.join(SIGNATURE)),
                11: msg('Priya Raman <priya@northwind-analytics.com>', 'dana@northbeam.io', 'Follow up',
                        body='One more thing.\n\n' + '\n'.join(SIGNATURE)),
                12: msg('Weekly Digest <digest@newsco.com>', 'dana@northbeam.io', 'News',
                        headers={'List-Unsubscribe': '<mailto:u@newsco.com>'}),
                13: msg('Acme <no-reply@acme.com>', 'dana@northbeam.io', 'Your receipt'),
                14: msg('Promo <hello@em.mailchimp.com>', 'dana@northbeam.io', 'Sale'),
                15: msg('Alex Kim <alex.kim@corpco.com>', 'dana@northbeam.io', 'Question', body='Quick question for you.'),
                16: msg('Sam Lee <sam@acmecorp.com>', 'dana@northbeam.io', 'Re: Proposal', body='Looks good.'),
            },
            'Spam': {30: msg('Win <winner@lotto.biz>', 'dana@northbeam.io', 'You won')},
        }
        res = client.post('/api/mail/accounts/imap', json={'address': 'dana@northbeam.io', 'password': FakeIMAP.PASSWORD,
                                                         'host': 'imap.northbeam.io'})
        check('connects with an app password', res.status_code == 200 and len(res.get_json()['accounts']) == 1, res.get_json())
        acc = EmailAccount.query.filter_by(user_id=D).first()
        check('the app password is stored encrypted', acc.token_enc and FakeIMAP.PASSWORD not in acc.token_enc)
        acc_id = acc.id

        print('\n5. Sync: batches, scoring, extraction')
        PlatformSetting.set('mail_batch_messages', '3')
        db.session.commit()
        batches = 0
        while batches < 12:
            acc = EmailAccount.query.get(acc_id)
            if acc.initial_sync_done:
                break
            mail_sync.run_batch(acc)
            batches += 1
        acc = EmailAccount.query.get(acc_id)
        check('first sync completes in resumable batches', acc.initial_sync_done and batches >= 3, batches)
        check('the spam folder is never opened', 'Spam' not in FakeIMAP.selected_log)
        by = {c.email: c for c in Contact.query.filter_by(user_id=D).all()}
        check('people the user wrote to are imported as Warm',
              by.get('sam@acmecorp.com') is not None and by['sam@acmecorp.com'].tier == 'warm'
              and by.get('ravi@acmecorp.com') is not None)
        check('newsletter, noreply and marketing-platform senders are not imported',
              not any(e in by for e in ('digest@newsco.com', 'no-reply@acme.com', 'hello@em.mailchimp.com')))
        check('the spam sender is not imported', 'winner@lotto.biz' not in by)
        check('the user\'s own address is not a contact', 'dana@northbeam.io' not in by)
        pr = by.get('priya@northwind-analytics.com')
        check('a person with a signature is imported', pr is not None, sorted(by))
        if pr:
            check('fields from the signature: name, title, company, phone, LinkedIn, location',
                  (pr.first_name, pr.last_name, pr.job_title, pr.company_name, pr.phone, pr.linkedin_url, pr.city,
                   pr.state_region) == ('Priya', 'Raman', 'VP Engineering', 'Northwind Analytics', '+14155550134',
                                        'https://linkedin.com/in/priyaraman', 'San Francisco', 'CA'),
                  (pr.first_name, pr.job_title, pr.company_name, pr.phone, pr.city))
            src = {s.field: s for s in ContactFieldSource.query.filter_by(contact_id=pr.id).all()}
            check('every field records its source and confidence',
                  src['job_title'].source == 'signature' and src['phone'].confidence >= 0.9
                  and src['email'].source == 'header' and src['phone'].message_id)
            check('department and expertise are labelled Inferred',
                  src.get('department') and src['department'].source == 'inferred' and pr.department == 'Engineering')
            check('campaign-ready: a person with name, company, valid domain', pr.campaign_ready is True, pr.not_ready_reason)
        check('Claude ran once per sender, not per message (two messages, same signature)', ai.calls == 1, ai.calls)
        rv = ReviewItem.query.filter_by(user_id=D, status='pending').all()
        check('a mid-score stranger waits in the review queue',
              [i.payload.get('email') for i in rv] == ['alex.kim@corpco.com'], [i.payload.get('email') for i in rv])
        check('the recruiter role address is imported as "role" and not campaign-ready',
              by.get('jobs@talentco.com') is not None and by['jobs@talentco.com'].tier == 'role'
              and by['jobs@talentco.com'].campaign_ready is False)
        skipped = SenderSignal.query.filter_by(account_id=acc_id, decision='skip').all()
        check('skipped senders are kept only as a hash and score', skipped and all(
            len(s.email_hash) == 64 for s in skipped) and email_hash('digest@newsco.com') in {s.email_hash for s in skipped})
        dump = ''
        for table in db.metadata.sorted_tables:
            for row in db.session.execute(table.select()).fetchall():
                dump += ' '.join(str(x) for x in row) + '\n'
        check('no email body or signature text is stored anywhere',
              'Great to meet' not in dump and 'One more thing' not in dump and 'm: (415) 555-0134' not in dump
              and 'Quick question for you' not in dump and 'digest@newsco.com' not in dump)
        runs = SyncRun.query.filter_by(account_id=acc_id).all()
        check('every sync batch is logged', len(runs) == batches and sum(r.messages_scanned for r in runs) >= 9)

        print('\n6. Locked user edits, ongoing sync')
        login(D)
        client.put('/api/contacts/%s' % pr.id, json={'job_title': 'CTO'})
        FakeIMAP.MAILBOXES['INBOX'][17] = msg('Priya Raman <priya@northwind-analytics.com>', 'dana@northbeam.io', 'Again',
                                              body='Hi.\n\n' + '\n'.join(SIGNATURE).replace('m: (415) 555-0134', 'm: (415) 555-0199'))
        acc = EmailAccount.query.get(acc_id)
        acc.last_sync_at = datetime.utcnow() - timedelta(hours=1)
        db.session.commit()
        mail_sync.run_due(budget_seconds=30)
        pr = Contact.query.get(pr.id)
        check('ongoing sync picks up new mail; a changed signature is re-read', pr.phone == '+14155550199' and ai.calls == 2,
              (pr.phone, ai.calls))
        check('a field the user edited is locked and never overwritten', pr.job_title == 'CTO')

        print('\n7. Review queue')
        ids = [i.id for i in ReviewItem.query.filter_by(user_id=D, status='pending').all()]
        res = client.post('/api/mail/review/decide', json={'ids': ids, 'decision': 'approve'})
        check('approving imports the person', res.status_code == 200 and res.get_json()['imported'] == 1
              and Contact.query.filter_by(user_id=D, email='alex.kim@corpco.com').first() is not None)

        print('\n8. Gmail step 1 (contacts only)')
        mail_sync.HTTP = FakeGoogle()
        with client.session_transaction() as s:
            s['mail_gmail_state'] = 'st8'
        res = client.get('/mail/connect/gmail/callback?state=st8&code=abc')
        gacc = EmailAccount.query.filter_by(user_id=D, provider='gmail').first()
        check('Gmail connects with contacts-only access', res.status_code == 302 and gacc is not None
              and gacc.access_level == 'contacts')
        for _ in range(3):
            gacc = EmailAccount.query.get(gacc.id)
            if gacc.initial_sync_done:
                break
            mail_sync.run_batch(gacc)
        leo = Contact.query.filter_by(user_id=D, email='leo@parkdesign.co').first()
        mia = Contact.query.filter_by(user_id=D, email='mia@harbor.vc').first()
        check('Other contacts are imported with phones', leo is not None and leo.phone == '+12125550199')
        check('saved contacts carry company and title from the address book', mia is not None
              and mia.company_name == 'Harbor Ventures' and mia.role_to_user == 'investor')
        check('automated Gmail contacts are still filtered', Contact.query.filter_by(email='no-reply@acme.com').first() is None)
        check('no Gmail message endpoint is ever called', not any('gmail.googleapis.com' in c for c in mail_sync.HTTP.calls))

        print('\n9. Admin switch: off marks imported contacts Do Not Contact')
        sim = Simulation(id=generate_id(), user_id=D, name='S', expertise_zone='Ops', status='complete')
        db.session.add(sim)
        db.session.flush()
        act = AgentAction(id=generate_id(), simulation_id=sim.id, layer_number=1, action_type='consulting_outreach',
                          status='complete', artifact='x', created_by=D, completed_at=now)
        db.session.add(act)
        db.session.flush()
        sam = Contact.query.filter_by(user_id=D, email='sam@acmecorp.com').first()
        step = ActionStep(id=generate_id(), agent_action_id=act.id, simulation_id=sim.id, step_number=1, total_steps=2,
                          action_type='consulting_outreach', step_type='email_followup', scheduled_for=now + timedelta(days=1),
                          condition_ref=sam.id)
        db.session.add(step)
        manual = Contact(id=generate_id(), user_id=D, first_name='Hand', last_name='Typed', email='hand@typed.com',
                         source='manual_entry')
        db.session.add(manual)
        db.session.commit()
        mail_feature.mark_dnc([pr.id], 'user', None, D)       # the user's own DNC on Priya
        db.session.commit()
        login(A)
        res = client.post('/api/admin/email-crm/switch', json={'feature_key': 'email_to_crm', 'mode': 'off',
                                                             'connection_action': 'pause', 'reason': ''})
        check('turning it off needs a reason', res.status_code == 400)
        res = client.post('/api/admin/email-crm/switch', json={'feature_key': 'email_to_crm', 'mode': 'off',
                                                             'connection_action': 'pause', 'reason': 'Incident review'})
        ch = res.get_json()['change']
        imported = Contact.query.filter_by(user_id=D, source='email_import').all()
        check('every imported contact is marked Do Not Contact', imported and all(c.do_not_contact for c in imported)
              and ch['contacts_marked'] >= len(imported) - 1, ch)
        check('a contact that never came through email is untouched', not Contact.query.get(manual.id).do_not_contact)
        check('connections are paused by the admin', all(a.status == 'admin_paused' for a in
              EmailAccount.query.filter_by(user_id=D).all()))
        check('scheduled sends to those contacts are cancelled', ActionStep.query.get(step.id).status == 'cancelled')
        check('the change is logged with admin, reason, scope and count', ch['changed_by'] == A
              and ch['reason'] == 'Incident review' and ch['scope'] == 'global' and ch['accounts_affected'] == 2)
        check('no batch runs while it is off', mail_sync.run_due(budget_seconds=5)['batches'] == 0)
        r = send_outreach_email(sim.id, sam.id, 'Hi', '<p>x</p>', 'simi@simulacrumai.io', 'Dana', action_id=act.id)
        check('the send pipeline refuses DNC contacts at send time', r == {'status': 'skipped', 'reason': 'do_not_contact'})
        login(D)
        res = client.put('/api/contacts/%s' % sam.id, json={'do_not_contact': False})
        check('the user can\'t lift the switch\'s Do Not Contact', res.status_code == 403
              and Contact.query.get(sam.id).do_not_contact)
        page = client.get('/settings/email-import').get_data(as_text=True)
        check('the user is told imported contacts are now Do Not Contact', 'Do Not Contact' in page
              and "isn't available" in page)

        login(A)
        client.post('/api/admin/email-crm/switch', json={'feature_key': 'email_to_crm', 'mode': 'on',
                                                       'connection_action': 'pause', 'reason': 'Resolved'})
        check('turning it back on lifts nothing', Contact.query.get(sam.id).do_not_contact)
        check('admin-paused connections resume', all(a.status == 'active' for a in EmailAccount.query.filter_by(user_id=D).all()))
        res = client.post('/api/admin/email-crm/changes/%s/lift' % ch['id'], json={'reason': 'Reviewed, safe'})
        check('lifting the switch\'s flags clears those contacts', res.status_code == 200
              and not Contact.query.get(sam.id).do_not_contact)
        check('…but a contact the user had marked Do Not Contact stays DNC', Contact.query.get(pr.id).do_not_contact
              and 'user' in mail_feature.active_dnc_reasons(pr.id))

        print('\n10. Per-user block and global precedence')
        mail_feature.set_user([D], 'blocked', 'Abuse report', A)
        check('blocking a user turns it off for them and marks their contacts', not mail_feature.is_enabled(D)
              and Contact.query.get(sam.id).do_not_contact)
        mail_feature.set_user([O], 'allowed', 'Beta', A)
        mail_feature.set_global('email_to_crm', 'off', 'pause', 'Pause all', A)
        check('a global off wins over a per-user Allowed', not mail_feature.is_enabled(O))
        mail_feature.set_global('email_to_crm', 'on', 'pause', 'Back on', A)
        mail_feature.set_global('email_to_crm.imap', 'off', 'revoke', 'IMAP incident', A)
        check('a provider switch stops one provider only', not mail_feature.is_enabled(O, 'imap')
              and mail_feature.is_enabled(O, 'gmail'))

        print('\n11. Pages, ownership, disconnect')
        login(O)
        check('users can\'t see another user\'s contact sources', client.get('/api/mail/contacts/%s/sources' % sam.id).status_code == 404)
        check('non-admins can\'t use the switch', client.post('/api/admin/email-crm/switch', json={
            'feature_key': 'email_to_crm', 'mode': 'off', 'reason': 'x y z'}).status_code == 403)
        login(A)
        check('admin page renders', 'Email-to-CRM for all users' in client.get('/admin/email-crm').get_data(as_text=True))
        mail_feature.set_user([D], 'allowed', 'Unblock', A)
        mail_feature.set_global('email_to_crm.imap', 'on', 'pause', 'IMAP ok', A)
        login(D)
        src = client.get('/api/mail/contacts/%s/sources' % pr.id).get_json()
        check('contact sources endpoint lists fields, accounts and DNC reasons',
              any(f['field'] == 'job_title' and f['locked'] for f in src['fields']) and src['accounts']
              and 'user' in src['dnc_reasons'])
        n_before = Contact.query.filter_by(user_id=D).count()
        gacc = EmailAccount.query.filter_by(user_id=D, provider='gmail').first()
        res = client.post('/api/mail/accounts/%s/disconnect' % gacc.id, json={'delete_contacts': True})
        gacc = EmailAccount.query.get(gacc.id)
        check('disconnect deletes the token now and removes contacts only from that account',
              res.status_code == 200 and gacc.token_enc is None and gacc.status == 'disconnected'
              and res.get_json()['contacts_deleted'] == 2 and Contact.query.filter_by(user_id=D).count() == n_before - 2,
              res.get_json())
        client.delete('/api/contacts/%s' % sam.id)
        check('a deleted person leaves only a hashed suppression entry', mail_feature  # noqa
              and mc.UserSuppression.query.filter_by(user_id=D, email_hash=email_hash('sam@acmecorp.com')).first() is not None)
        check('no network calls at any point', not network, network)

    print('\n%d passed, %d failed' % (len(PASS), len(FAIL)))
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
