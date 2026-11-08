"""Contact invites from /contacts: a short SimiSI invite per contact, with status.

    python test_contact_invites.py    (in-memory SQLite; SendGrid is faked, no network)
"""
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


def main():
    import requests
    network = []
    requests.post = requests.get = lambda *a, **k: network.append(a) or (_ for _ in ()).throw(AssertionError('network'))
    import sendgrid
    SENT = []

    class _FakeSG:
        def __init__(self, api_key=None, *a, **k):
            pass

        def send(self, message):
            SENT.append(message.get())
            return type('R', (), {'status_code': 202, 'headers': {'X-Message-Id': 'msg-%d' % len(SENT)}})()
    sendgrid.SendGridAPIClient = _FakeSG

    from app import create_app
    from app.extensions import db
    app = create_app('testing')
    app.config.update(SQLALCHEMY_DATABASE_URI='sqlite:///:memory:', WTF_CSRF_ENABLED=False, SENDGRID_API_KEY='')
    from app.services import email_service
    email_service.send_verification_email = lambda *a, **k: None   # registration sends one in a thread

    with app.app_context():
        from flask import g
        from app import models as _models  # noqa: F401
        db.create_all()
        from app.models.user import User
        from app.models.contact import Contact, ContactActivity
        from app.models.contact_invite import ContactInvite
        from app.models.audit_log import AuditLog
        from app.models.outreach_email import EmailSuppression
        from app.models.platform_settings import PlatformSetting
        from utils.id_gen import generate_id

        def user(uid, email, name='Jean Delphonse'):
            u = User(id=uid, email=email, full_name=name, password_hash='x', email_verified=True,
                     onboarding_completed_at=datetime.utcnow())
            db.session.add(u)
            db.session.commit()
            return u

        def contact(owner, first, email, **kw):
            c = Contact(id=generate_id(), user_id=owner.id, first_name=first, last_name='Test', email=email,
                        source='manual_entry', pipeline_stage='prospect', **kw)
            db.session.add(c)
            db.session.commit()
            return c

        owner = user('OWNERCI01', 'jean@x.com')
        other = user('OTHERCI01', 'other@x.com', 'Other Member')
        user('ALREADY01', 'already@x.com', 'Already Here')
        ana = contact(owner, 'Ana', 'Ana@Example.com')
        ben = contact(owner, 'Ben', 'ben@example.com')
        dnc = contact(owner, 'Dee', 'dee@example.com', do_not_contact=True)
        on_simisi = contact(owner, 'Al', 'already@x.com')
        opted = contact(owner, 'Opt', 'opt@example.com')
        db.session.add(EmailSuppression(email='opt@example.com', reason='unsubscribe'))
        theirs = contact(other, 'Zed', 'zed@example.com')
        db.session.commit()

        client = app.test_client()

        def as_user(uid):
            with client.session_transaction() as s:
                s['_user_id'] = uid
                s['_fresh'] = True
            g.pop('_login_user', None)

        def invite(cid):
            g.pop('_login_user', None)
            r = client.post('/api/contacts/%s/invite' % cid)
            return r.status_code, (r.get_json() or {})

        def listed():
            g.pop('_login_user', None)
            return {c['id']: c['simisi_invite'] for c in client.get('/api/contacts?per_page=200').get_json()['contacts']}

        print('\n1. Status on /contacts')
        as_user(owner.id)
        st = listed()
        check('a new contact shows the Invite button (not_invited)', st[ana.id] == {'status': 'not_invited'}, st[ana.id])
        check('an address that already has an account shows On SimiSI', st[on_simisi.id] == {'status': 'member'})
        check('Do Not Contact and opted-out addresses can\'t be invited',
              st[dnc.id]['status'] == 'blocked' and st[opted.id]['status'] == 'blocked', (st[dnc.id], st[opted.id]))
        check("another member's contact isn't listed", theirs.id not in st)

        print('\n2. Guards before anything is sent')
        code, d = invite(ana.id)
        check("no SimiSI postal address yet: invites are held", code == 503 and not SENT, (code, d))
        PlatformSetting.set('outreach_business_address', '1 Market St, Suite 100, San Francisco, CA 94105')
        code, d = invite(ana.id)
        check('no SendGrid key: refused, nothing sent', code == 503 and not SENT, (code, d))
        app.config['SENDGRID_API_KEY'] = 'test-key'
        check('Do Not Contact: refused', invite(dnc.id)[0] == 409 and not SENT)
        check('opted out: refused', invite(opted.id)[0] == 409 and not SENT)
        check('already on SimiSI: refused', invite(on_simisi.id)[0] == 409 and not SENT)
        check("someone else's contact: not found", invite(theirs.id)[0] == 404 and not SENT)
        me = contact(owner, 'Me', 'jean@x.com')
        check('your own address: refused', invite(me.id)[0] == 400 and not SENT)

        print('\n3. Sending')
        code, d = invite(ana.id)
        check('the invite is sent', code == 201 and d.get('simisi_invite', {}).get('status') == 'invited' and len(SENT) == 1,
              (code, d, len(SENT)))
        m = SENT[-1] if SENT else {}
        html = next((c['value'] for c in m.get('content', []) if c['type'] == 'text/html'), '')
        plain = next((c['value'] for c in m.get('content', []) if c['type'] == 'text/plain'), '')
        check('to the contact, from the member via SimiSI, replies to the STOP-aware address',
              m.get('personalizations', [{}])[0].get('to', [{}])[0].get('email') == 'ana@example.com'
              and m.get('from', {}).get('name') == 'Jean Delphonse via SimiSI'
              and m.get('reply_to', {}).get('email', '').startswith('reply-%s@' % ana.id), (m.get('from'), m.get('reply_to')))
        check('subject is short and friendly', m.get('subject') == 'Jean thinks you might like SimiSI', m.get('subject'))
        words = len(plain.split('\n\nSent by')[0].split())
        check('the message is brief (under 80 words before the footer)', words < 80, words)
        check('greets the contact and names the member', 'Hi Ana,' in plain and 'Jean Delphonse uses SimiSI' in plain)
        inv = ContactInvite.query.filter_by(user_id=owner.id, email='ana@example.com').first()
        import re as _re
        visible = _re.sub(r'<[^>]+>', ' ', html)
        check('the body shows the address as SimiSI.io', 'Take a look at SimiSI.io' in ' '.join(visible.split())
              and 'Visit SimiSI.io' in visible and 'Take a look at https://SimiSI.io' in plain, plain)
        check('no long invite link is shown, in either part', inv is not None and ('/join/%s' % inv.token) not in visible
              and '/join/' not in plain)
        check('the HTML links still go through the personal invite link (for Joined)',
              inv is not None and html.count('href="http://localhost/join/%s"' % inv.token) == 2, html.count('/join/'))
        check("unsubscribe link, one-click headers and SimiSI's address in the footer",
              'Unsubscribe' in html and m.get('headers', {}).get('List-Unsubscribe-Post') == 'List-Unsubscribe=One-Click'
              and '1 Market St, Suite 100' in html and '1 Market St, Suite 100' in plain, m.get('headers'))
        check('no account-holder footer (they have no account)', 'because you have an account' not in html)
        check('no income promises in the copy', not any(w in plain.lower() for w in ('guarantee', 'passive income', 'earn $')))
        st = listed()
        check('the row now reads Invited', st[ana.id]['status'] == 'invited' and st[ana.id]['sent_at'])
        code, d = invite(ana.id)
        check('a second invite to the same person is refused', code == 409 and 'Already invited' in d.get('error', '')
              and len(SENT) == 1, (code, d))
        check('the contact timeline records it', ContactActivity.query.filter_by(contact_id=ana.id,
                                                                              activity_type='simisi_invite').count() == 1)
        rows = [a.extra for a in AuditLog.query.filter_by(action='outreach_send').all()]
        check('every attempt leaves an outreach_send audit row on the contact_invite path',
              any(r['path'] == 'contact_invite' and r['status'] == 'sent' for r in rows)
              and any(r['path'] == 'contact_invite' and r['reason'] == 'no_business_address' for r in rows), rows[-1:])

        print('\n4. Daily cap (Admin setting)')
        PlatformSetting.set('contact_invite_daily_cap', '1')
        code, d = invite(ben.id)
        check('over the daily cap: refused until tomorrow', code == 429 and len(SENT) == 1, (code, d))
        PlatformSetting.set('contact_invite_daily_cap', '20')
        check('the cap is on Admin -> Compliance', 'contact_invite_daily_cap' in __import__(
            'app.services.send_limits', fromlist=['DEFAULTS']).DEFAULTS)
        code, d = invite(ben.id)
        check('under the cap it sends', code == 201 and len(SENT) == 2)

        print('\n5. Joining')
        anon = app.test_client()
        g.pop('_login_user', None)
        r = anon.get('/join/%s' % inv.token)
        check('the invite link leads to sign-up', r.status_code in (301, 302) and '/register' in r.headers.get('Location', ''))
        g.pop('_login_user', None)
        r = anon.post('/api/auth/register', json={'email': 'ana.new@example.org', 'password': 'Sup3r-secret-pw!',
                                                  'full_name': 'Ana Ruiz'})
        check('Ana registers (with a different address) through the link', r.status_code in (200, 201), r.get_data(as_text=True)[:200])
        inv = ContactInvite.query.get(inv.id)
        new = User.query.filter_by(email='ana.new@example.org').first()
        check('her invite reads Joined, linked to the new account', inv.status == 'joined' and inv.joined_at
              and new is not None and inv.joined_user_id == new.id, (inv.status, inv.joined_user_id))
        as_user(owner.id)
        check('the row on /contacts now reads Joined', listed()[ana.id]['status'] == 'joined')
        g.pop('_login_user', None)
        r = app.test_client().post('/api/auth/register', json={'email': 'ben@example.com', 'password': 'Sup3r-secret-pw!',
                                                               'full_name': 'Ben Test'})
        ben_inv = ContactInvite.query.filter_by(user_id=owner.id, email='ben@example.com').first()
        check('Ben signs up with the invited address, no link: also Joined', r.status_code in (200, 201)
              and ben_inv.status == 'joined', (r.status_code, ben_inv.status))
        g.pop('_login_user', None)
        r = app.test_client().get('/join/not-a-real-token')
        check('an unknown token still just leads to sign-up', r.status_code in (301, 302))

        print('\n6. Page')
        tpl = open('app/templates/contacts/list.html', encoding='utf-8').read()
        check('/contacts has the SimiSI invite column, button and statuses',
              '<th class="invite-col">SimiSI invite</th>' in tpl and 'inviteContact(' in tpl
              and '>Invited</span>' in tpl and '>Joined</span>' in tpl and tpl.count('colspan="8"') == 3)
        check('no network calls', not network, network)

    print('\n%d passed, %d failed' % (len(PASS), len(FAIL)))
    for label in FAIL:
        print('  FAILED: ' + label)
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
