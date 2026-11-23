"""Security review fixes (2026-11): one or more checks per fix.

Run: python test_security_fixes.py
"""
import base64
import io
import json
import os
import sys
import time
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

PASS, FAIL = [], []
NEW_PY = ('app/services/webhook_auth.py', 'utils/rate_limit.py')


def check(label, condition, detail=''):
    (PASS if condition else FAIL).append(label)
    print(('  ok   ' if condition else '  FAIL ') + label + ('' if condition else '  -> %r' % (detail,)))


def read(path):
    return io.open(os.path.join(ROOT, path), encoding='utf-8').read()


def main():
    import requests
    requests.post = requests.get = lambda *a, **k: (_ for _ in ()).throw(AssertionError('network'))

    print('\n0. New modules are Python 3.9 safe')
    check('every new module starts with the __future__ import',
          all('from __future__ import annotations' in read(f) for f in NEW_PY))

    import tempfile
    from cryptography.fernet import Fernet
    from app import create_app
    from app.extensions import db
    from utils.id_gen import generate_id
    upload = tempfile.mkdtemp(prefix='secfix_')
    app = create_app('testing')
    app.config.update(SQLALCHEMY_DATABASE_URI='sqlite:///:memory:', UPLOAD_FOLDER=upload, SERVER_NAME=None,
                      ENCRYPTION_KEY=Fernet.generate_key().decode())
    from app.services import email_service
    email_service._send = lambda *a, **k: None

    with app.app_context():
        db.create_all()
        from flask import g
        from app.models.user import User
        from app.models.simulation import Simulation
        from app.models.agent_action import AgentAction
        from app.models.artifact import ArtifactVersion
        from app.models.profile import UserProfile
        now = datetime.utcnow()

        def user(email, name, admin=False, verified=True):
            u = User(id=generate_id(), email=email, full_name=name, password_hash='x', is_admin=admin,
                     email_verified=verified, onboarding_completed_at=now)
            db.session.add(u)
            db.session.commit()
            return u.id

        A_ID = user('alice@x.com', 'Alice Owner')
        B_ID = user('bob@x.com', 'Bob Other')
        ADM = user('admin@x.com', 'Admin', admin=True)
        sim_a = Simulation(id=generate_id(), user_id=A_ID, name='A sim')
        sim_b = Simulation(id=generate_id(), user_id=B_ID, name='B sim')
        db.session.add_all([sim_a, sim_b])
        db.session.flush()
        act_b = AgentAction(id=generate_id(), simulation_id=sim_b.id, layer_number=3, action_type='sales_page',
                            artifact='Bob secret plan', status='complete')
        act_a = AgentAction(id=generate_id(), simulation_id=sim_a.id, layer_number=3, action_type='sales_page',
                            artifact='Alice plan', status='complete')
        db.session.add_all([act_a, act_b])
        db.session.flush()
        av_b = ArtifactVersion(id=generate_id(), action_id=act_b.id, simulation_id=sim_b.id, layer_number=3,
                               action_type='sales_page', content='<h1>Bob private</h1>', is_current=True)
        db.session.add(av_b)
        db.session.commit()

        client = app.test_client()
        for verb in ('get', 'post', 'put', 'delete', 'patch'):
            orig = getattr(client, verb)
            setattr(client, verb, (lambda o: lambda *a, **k: (g.pop('_login_user', None), o(*a, **k))[1])(orig))

        def login(uid):
            with client.session_transaction() as s:
                s.clear()
                s['_user_id'], s['_fresh'] = uid, True

        print('\n1. Markdown on bio and artifact pages is sanitized')
        bio = read('app/templates/public/bio_page.html')
        check('bio page sanitizes marked output with DOMPurify', 'DOMPurify.sanitize(marked.parse(raw)' in bio
              and 'purify.min.js' in bio and 'el.innerHTML = marked.parse(raw);' not in bio)
        check('artifact view sanitizes too', 'DOMPurify.sanitize(marked.parse(text))' in
              read('app/templates/simulations/artifact_view.html'))

        print('\n2. Publishing: own simulation only, hosted HTML sandboxed')
        login(B_ID)
        r = client.post('/api/publishing/deploy', json={'simulation_id': sim_a.id, 'action_type': 'sales_page',
                                                         'html_content': '<script>x</script>'})
        check("a user can't publish into someone else's simulation", r.status_code == 404, r.status_code)
        login(A_ID)
        r = client.post('/api/publishing/deploy', json={'simulation_id': sim_a.id, 'action_type': 'report',
                                                         'artifact_version_id': av_b.id})
        check("a user can't publish another user's artifact version", r.status_code == 404, r.status_code)
        r = client.post('/api/publishing/deploy', json={'simulation_id': sim_a.id, 'action_type': 'report',
                                                         'action_id': act_b.id})
        check("... or another user's action", r.status_code == 404, r.status_code)
        check('the artifact was not made public', db.session.get(ArtifactVersion, av_b.id).public_url is None)
        from app.models.published_page import PublishedPage
        db.session.add(PublishedPage(id=generate_id(), slug='alice-page', user_id=A_ID, simulation_id=sim_a.id,
                                     action_type='sales_page', html_content='<script>alert(1)</script>',
                                     status='live'))
        db.session.commit()
        r = client.get('/p/alice-page')
        check('hosted pages carry a sandbox CSP (no access to SimiSI cookies or APIs)', r.status_code == 200
              and 'sandbox' in r.headers.get('Content-Security-Policy', '')
              and 'allow-same-origin' not in r.headers.get('Content-Security-Policy', ''),
              r.headers.get('Content-Security-Policy'))

        print('\n3. Webhooks are authenticated')
        from app.services import webhook_auth as wa
        r = client.post('/api/webhooks/sendgrid', json=[{'event': 'spamreport', 'email': 'victim@x.com'}])
        from app.models.outreach_email import EmailSuppression
        check('SendGrid events with nothing configured are refused (fail closed)', r.status_code == 403
              and EmailSuppression.query.filter_by(email='victim@x.com').first() is None, r.status_code)
        app.config['SENDGRID_WEBHOOK_TOKEN'] = 'tok-123456789'
        r = client.post('/api/webhooks/sendgrid?k=wrong', json=[{'event': 'spamreport', 'email': 'victim@x.com'}])
        check('... a wrong token is refused', r.status_code == 403)
        r = client.post('/api/webhooks/sendgrid?k=tok-123456789', json=[{'event': 'delivered', 'email': 'a@x.com'}])
        check('... the right token is accepted', r.status_code == 200, r.status_code)
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        priv = ec.generate_private_key(ec.SECP256R1())
        pub = base64.b64encode(priv.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)).decode()
        app.config.update(SENDGRID_WEBHOOK_PUBLIC_KEY=pub, SENDGRID_WEBHOOK_TOKEN=None)
        body = json.dumps([{'event': 'delivered', 'email': 'a@x.com'}]).encode()
        ts = str(int(time.time()))
        sig = base64.b64encode(priv.sign(ts.encode() + body, ec.ECDSA(hashes.SHA256()))).decode()
        r = client.post('/api/webhooks/sendgrid', data=body, content_type='application/json',
                        headers={wa.SIGNATURE_HEADER: sig, wa.TIMESTAMP_HEADER: ts})
        check('a correctly signed SendGrid event is accepted', r.status_code == 200, r.status_code)
        r = client.post('/api/webhooks/sendgrid', data=body.replace(b'a@x.com', b'b@x.com'),
                        content_type='application/json', headers={wa.SIGNATURE_HEADER: sig, wa.TIMESTAMP_HEADER: ts})
        check('a tampered body fails the signature', r.status_code == 403)
        r = client.post('/api/webhooks/sendgrid/inbound', data={'to': 'reply-ABCDEFGHI@x', 'text': 'STOP'})
        check('Inbound Parse without its token is refused', r.status_code == 403)
        for prov in ('cal', 'kajabi', 'convertkit', 'apollo'):
            r = client.post('/webhooks/%s/%s' % (prov, A_ID), json={'event': 'purchase.completed', 'amount_cents': 999})
            r2 = client.post('/webhooks/%s/%s?k=%s' % (prov, A_ID, wa.user_webhook_key(prov, A_ID)), json={})
            check('%s webhook: refused without the key, accepted with it' % prov, r.status_code == 403
                  and r2.status_code == 200, (r.status_code, r2.status_code))
        check("a user's key doesn't work for another user",
              wa.user_webhook_key('cal', A_ID) != wa.user_webhook_key('cal', B_ID))
        with app.test_request_context():
            check('registered Cal/Apollo URLs carry the key', '?k=' in wa.user_webhook_url('cal', A_ID))
        app.config['STRIPE_CONNECT_WEBHOOK_SECRET'] = None
        r = client.post('/webhooks/stripe/connect', json={'type': 'payment_intent.succeeded', 'account': 'acct_x'})
        check('Stripe Connect without its secret refuses unsigned events', r.status_code == 503, r.status_code)
        from app.models.integration import UserIntegration
        from app.services import stripe_connect_service as scs
        db.session.add(UserIntegration(user_id=B_ID, provider='stripe', provider_account_id='acct_bob'))
        db.session.commit()
        from app.models.income import LayerIncomeRecord
        n0 = LayerIncomeRecord.query.count()
        scs.attribute_income_from_stripe_event({'id': 'pi_1', 'amount': 5000, 'metadata': {
            'simulacrum_simulation_id': sim_a.id, 'simulacrum_user_id': A_ID}}, 'acct_bob')
        check("Bob's Stripe account can't record income on Alice's simulation", LayerIncomeRecord.query.count() == n0)
        app.config['DIGEST_CRON_SECRET'] = None
        r = client.post('/api/notifications/digest/%s' % A_ID)
        check('the digest endpoint is refused when its secret is not set', r.status_code == 401)

        print('\n4. /avatars serves only profile and hero images')
        for rel in ('avatars/%s/avatar.png' % A_ID, 'voice/%s/voice_sample_1.mp3' % A_ID, 'abc_resume.pdf',
                    'careers/x_cv.pdf'):
            p = os.path.join(upload, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            open(p, 'wb').write(b'x')
        check('an avatar is served', client.get('/avatars/avatars/%s/avatar.png' % A_ID).status_code == 200)
        check('voice samples are not', client.get('/avatars/voice/%s/voice_sample_1.mp3' % A_ID).status_code == 404)
        check('resumes are not', client.get('/avatars/abc_resume.pdf').status_code == 404
              and client.get('/avatars/careers/x_cv.pdf').status_code == 404)

        print('\n5. bio-page.json respects publish status and private mode')
        from app.models.bio_page import BioPage
        db.session.add(UserProfile(user_id=A_ID, username='alice', display_name='Alice Owner', is_published=True,
                                   bio='Secret career story'))
        bp = BioPage(user_id=A_ID, slug='alice', status=BioPage.STATUS_DRAFT)
        db.session.add(bp)
        db.session.commit()
        login(B_ID)
        check('a draft page is not readable by others', client.get('/u/alice/bio-page.json').status_code == 404)
        bp.status = BioPage.STATUS_PUBLISHED
        db.session.commit()
        d = client.get('/u/alice/bio-page.json').get_json()
        check('others get only the card fields (no user_id, no career)', d and 'user_id' not in d['bio_page']
              and 'about_text' not in d['context'] and d['context']['full_name'] == 'Alice Owner', d)
        prof = UserProfile.query.filter_by(user_id=A_ID).first()
        prof.privacy_mode = 'private'
        db.session.commit()
        d = client.get('/u/alice/bio-page.json').get_json()
        check('a private page gives strangers name and photo only', d and not d['context'].get('professional_title'),
              d)
        login(A_ID)
        d = client.get('/u/alice/bio-page.json').get_json()
        check('the owner still gets the full preview data', d and 'about_text' in d['context'])

        print('\n6. Voice debug-key is admin only')
        login(B_ID)
        check('a member gets 404', client.get('/api/voice/debug-key').status_code == 404)

        print('\n7. Output escaping and safe links')
        check('contacts list escapes names, emails and companies',
              '${_invEsc(c.display_name)}' in read('app/templates/contacts/list.html')
              and '${c.display_name}' not in read('app/templates/contacts/list.html'))
        check('feed escapes actor names', "esc(ev.actor_name)" in read('app/templates/social/feed.html'))
        check('platform chat escapes the owner name', "escHtml(ownerName)" in read('app/templates/social/my_chats.html'))
        f = app.jinja_env.filters['safe_href']
        check('safe_href blocks javascript:, data: and //', f('javascript:alert(1)') == '#' and f('data:x') == '#'
              and f('//evil.com') == '#' and f('https://ok.com/a') == 'https://ok.com/a'
              and f('linkedin.com/in/x') == 'https://linkedin.com/in/x')
        check('bio and profile pages use safe_href on member links', read('app/templates/public/bio_page.html')
              .count('|safe_href') >= 15 and '|safe_href' in read('app/templates/public/profile.html'))
        check('embed card escapes the name', 'esc(ctx.full_name' in read('app/templates/public/embed_card.js'))

        print('\n8. Cross-site requests, cookies, headers')
        login(A_ID)
        r = client.post('/api/settings/security/delete-account', json={'confirm_email': 'alice@x.com'},
                        headers={'Origin': 'https://evil.example'})
        check('a cross-site POST is refused', r.status_code == 403 and db.session.get(User, A_ID).deleted_at is None,
              r.status_code)
        r = client.post('/api/bio-chat/nope/session', json={}, headers={'Origin': 'http://localhost'})
        check('a same-site POST passes the guard', r.status_code != 403, r.status_code)
        r = client.post('/api/bio-chat/nope/session', json={}, headers={'Origin': 'null',
                                                                         'Sec-Fetch-Site': 'same-origin'})
        check('a same-origin form on a no-referrer page (Origin: null) passes', r.status_code != 403, r.status_code)
        r = client.post('/api/settings/security/delete-account', json={'confirm_email': 'alice@x.com'},
                        headers={'Origin': 'null'})
        check('Origin: null without Sec-Fetch-Site is still refused', r.status_code == 403, r.status_code)
        r = client.post('/api/settings/security/delete-account', json={'confirm_email': 'alice@x.com'},
                        headers={'Sec-Fetch-Site': 'cross-site', 'Origin': 'https://evil.example'})
        check('Sec-Fetch-Site: cross-site is refused', r.status_code == 403
              and db.session.get(User, A_ID).deleted_at is None, r.status_code)
        r = client.post('/api/webhooks/sendgrid', json=[], headers={'Origin': 'https://sendgrid.example'})
        check('webhooks are exempt from the origin check (they authenticate themselves)',
              'Cross-site' not in r.get_data(as_text=True))
        r = client.get('/login')
        check('pages carry X-Frame-Options and nosniff', r.headers.get('X-Frame-Options') == 'SAMEORIGIN'
              and r.headers.get('X-Content-Type-Options') == 'nosniff')
        check('session and remember cookies are SameSite=Lax; remember lasts 30 days',
              app.config['SESSION_COOKIE_SAMESITE'] == 'Lax' and app.config['REMEMBER_COOKIE_SAMESITE'] == 'Lax'
              and app.config['REMEMBER_COOKIE_DURATION'] == timedelta(days=30))
        from config import ProductionConfig
        check('production cookies are Secure', ProductionConfig.SESSION_COOKIE_SECURE
              and ProductionConfig.REMEMBER_COOKIE_SECURE)
        old = os.environ.pop('SECRET_KEY', None)
        os.environ['ENCRYPTION_KEY'] = 'x'
        try:
            ProductionConfig.init_app(app)
            refused = False
        except RuntimeError:
            refused = True
        finally:
            if old is not None:
                os.environ['SECRET_KEY'] = old
        check('production refuses to start without a real SECRET_KEY', refused)

        print('\n9. Google sign-in cannot be pre-hijacked')
        V_ID = user('victim@x.com', 'Victim Person', verified=False)
        db.session.get(User, V_ID).password_hash = 'attacker-chosen-hash'
        db.session.commit()
        import app.blueprints.auth.routes as ar

        class _R(object):
            def __init__(self, d):
                self._d = d

            def raise_for_status(self):
                pass

            def json(self):
                return self._d
        prof_data = {'sub': 'g-123', 'email': 'victim@x.com', 'name': 'Victim Person', 'email_verified': True}
        requests.post = lambda *a, **k: _R({'access_token': 't'})
        requests.get = lambda *a, **k: _R(prof_data)
        with client.session_transaction() as s:
            s.clear()
            s['google_oauth_state'] = 'st'
        client.get('/api/auth/google/callback?code=c&state=st')
        v = db.session.get(User, V_ID)
        check("linking Google to an unverified account removes the earlier password", v.password_hash is None
              and v.google_id == 'g-123' and v.email_verified, (v.password_hash, v.google_id))
        prof_data = {'sub': 'g-999', 'email': 'other@x.com', 'name': 'O', 'email_verified': False}
        with client.session_transaction() as s:
            s['google_oauth_state'] = 'st2'
        r = client.get('/api/auth/google/callback?code=c&state=st2')
        check("an email Google hasn't verified is refused", r.status_code == 400
              and User.query.filter_by(email='other@x.com').first() is None)
        requests.post = requests.get = lambda *a, **k: (_ for _ in ()).throw(AssertionError('network'))

        print('\n10. Remember-me cookies die with a password change or sign-out-all')
        from app.blueprints.auth.routes import load_user
        ua = db.session.get(User, A_ID)
        tagged = ua.get_id()
        check('the session id carries a tag', tagged.startswith(A_ID + ':') and len(tagged) > len(A_ID) + 10)
        def restore(raw):
            with app.test_request_context():
                g._remember_restore = True
                return load_user(raw)
        check('a remember-cookie restore with the current tag loads', restore(tagged) is not None)
        check('... and gets its own revocable session record', __import__('app.models.profile', fromlist=['x'])
              .UserSession.query.filter_by(user_id=A_ID, revoked_at=None).count() >= 1)
        check('... a cookie without a tag (issued before this fix) does not', restore(A_ID) is None)
        tagged = db.session.get(User, A_ID).get_id()
        ua.password_hash = 'changed'
        db.session.commit()
        check('... after a password change the old cookie is refused', restore(tagged) is None)
        tagged = db.session.get(User, A_ID).get_id()
        from app.models.profile import UserSession
        UserSession.query.filter_by(user_id=A_ID).update({'revoked_at': datetime.utcnow()})
        db.session.commit()
        check('... after "sign out all sessions" the old cookie is refused', restore(tagged) is None)
        with app.test_request_context():
            check('an ordinary session is unaffected', load_user(A_ID) is not None)
        # End to end through Flask-Login: sign in, keep only the remember cookie.
        from app.extensions import bcrypt
        R_ID = user('remember@x.com', 'Remy Member')
        ru = db.session.get(User, R_ID)
        ru.password_hash = bcrypt.generate_password_hash('Pw-12345!', rounds=4).decode()
        db.session.commit()
        rc = app.test_client()
        rc.post('/api/auth/login', json={'email': 'remember@x.com', 'password': 'Pw-12345!'})

        def probe():
            g.pop('_login_user', None)
            return rc.get('/api/settings/security/sessions').status_code
        rc.delete_cookie('session')
        check('end to end: the remember cookie alone signs the member back in', probe() == 200)
        rc.delete_cookie('session')
        ru = db.session.get(User, R_ID)
        ru.password_hash = bcrypt.generate_password_hash('New-12345!', rounds=4).decode()
        db.session.commit()
        check('end to end: after a password change it no longer does', probe() in (302, 401))
        ua.password_hash = 'x'
        db.session.commit()

        print('\n11. Rate limits (bio chat, login, reset)')
        from utils import rate_limit
        app.config['RATE_LIMITS_ENABLED'] = True
        rate_limit.reset()
        from app.extensions import bcrypt
        ua = db.session.get(User, A_ID)
        ua.password_hash = bcrypt.generate_password_hash('Right-pass-1', rounds=4).decode()
        db.session.commit()
        codes = [client.post('/api/auth/login', json={'email': 'alice@x.com', 'password': 'wrong'}).status_code
                 for _ in range(12)]
        check('login is throttled per address', codes[-1] == 429 and codes[0] == 401, codes)
        rate_limit.reset()
        codes = [client.post('/api/auth/reset-password', json={'email': 'alice@x.com'}).status_code for _ in range(5)]
        check('password reset never errors (no enumeration) while throttled', set(codes) == {200}, codes)
        from app.blueprints.bio_chat import routes as bcr
        check('bio chat caps messages per session', bcr.MAX_SESSION_MESSAGES == 40)
        src = read('app/blueprints/bio_chat/routes.py')
        check('bio chat throttles session starts and messages per IP', "'biochat:start:'" in src
              and "'biochat:msg:'" in src)
        check('the rate-card email goes once per session, contacts masked', "'ratecard:s:'" in src
              and 'mask_text(rate_card)' in src)
        app.config['RATE_LIMITS_ENABLED'] = False
        rate_limit.reset()

        print('\n12. Bundles, signing and revoked collaborators')
        from app.models.collaboration import Collaboration
        db.session.add(Collaboration(simulation_id=sim_a.id, invitee_email='bob@x.com', permission_level='editor',
                                     share_token='tok-abc', expires_at=now + timedelta(days=5),
                                     accepted_at=now, revoked_at=now))
        db.session.commit()
        login(B_ID)
        check('a revoked collaborator cannot read the simulation',
              client.get('/api/simulations/%s' % sim_a.id).status_code in (403, 404))
        check('... or open its page', client.get('/simulations/%s' % sim_a.id).status_code in (403, 404))
        login(A_ID)
        sim_a.status = 'complete'
        db.session.commit()
        r = client.post('/api/simulations/%s/bundles' % sim_a.id if False else '/api/artifacts/%s/bundles' % sim_a.id,
                        json={'bundle_type': 'custom', 'artifact_ids': [act_b.id, act_a.id]})
        from app.models.artifact import ArtifactBundle
        b = ArtifactBundle.query.filter_by(simulation_id=sim_a.id).first()
        check("a bundle can't include another user's artifacts", b is None or act_b.id not in (b.artifact_ids or []),
              (r.status_code, b.artifact_ids if b else None))
        r = client.post('/api/signing/send', json={'simulation_id': sim_b.id, 'recipient_email': 'me@x.com',
                                                    'action_type': 'x', 'artifact_version_id': av_b.id})
        check("signing refuses another user's simulation", r.status_code == 404, r.status_code)

        print('\n13. Smaller fixes')
        check('password reset email links to a page that exists',
              "url_for('pages.reset_password_page'" in read('app/services/email_service.py'))
        r = client.get('/reset-password/sometoken')
        check('the reset page loads and keeps the token out of Referer', r.status_code == 200
              and r.headers.get('Referrer-Policy') == 'no-referrer')
        login(A_ID)
        r = client.post('/api/auth/register', json={'email': 'new@x.com', 'password': 'a', 'full_name': 'New Person'})
        check('registration enforces the password rule', r.status_code == 400 and 'Password' in r.get_json()['error'])
        check('the dispute handler imports AuditLog', 'from app.models.audit_log import AuditLog' in
              read('app/blueprints/payments/routes.py'))
        check('login redirect rejects backslash tricks', "(?![\\/\\\\])" in read('app/templates/auth/login.html'))
        from app.services import mail_sync
        check('IMAP refuses internal hosts and odd ports', not mail_sync._public_host('127.0.0.1')
              and mail_sync.IMAP_PORTS == (993, 143))
        from app.services import peer_service as ps
        from app.models.peer import PeerInvite
        inv = PeerInvite(owner_user_id=A_ID, direction=ps.DIRECTION_INVITE_OUT, status=ps.INVITE_PENDING,
                         invite_email='carol@x.com')
        db.session.add(inv)
        db.session.commit()
        try:
            ps.accept_invite(inv.id, B_ID)
            took = True
        except ps.PeerError:
            took = False
        check('a peer invite to an email can only be accepted by that account', not took)
        check('notification emails escape the body', '_esc(notif.body' in read('app/services/notification_service.py'))
        from utils.validators import csv_cell
        check('CSV exports neutralise formulas', csv_cell('=HYPERLINK("x")').startswith("'") and csv_cell('Ann') == 'Ann')
        src = read('app/blueprints/bio_privacy/routes.py')
        check('bio-privacy one-tap approval needs a POST and a pending request', "methods=['GET', 'POST']" in src
              and "result='confirm'" in src and 'STATUS_PENDING' in src)

    print('\n%d passed, %d failed' % (len(PASS), len(FAIL)))
    for label in FAIL:
        print('  FAILED: ' + label)
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
