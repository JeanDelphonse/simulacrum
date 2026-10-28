"""Password reset end to end: request → email link → new-password page → sign in.

The emailed link used to point at POST-only /api/auth/reset-password/<token>,
so clicking it answered 405 Method Not Allowed and there was no page to choose
a new password. Runs on in-memory SQLite; the email send is captured, not sent.

    python test_password_reset.py
"""
import os
import sys

os.environ.setdefault('DATABASE_URL', 'sqlite:///:memory:')
os.environ.setdefault('SECRET_KEY', 'test-only')

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import create_app                                    # noqa: E402
from app.extensions import db, bcrypt                         # noqa: E402
from utils.id_gen import generate_id                          # noqa: E402

PASS, FAIL = [], []


def check(label, condition, detail=''):
    (PASS if condition else FAIL).append(label)
    mark = 'ok  ' if condition else 'FAIL'
    line = '  {} {}{}'.format(mark, label, ('  -- ' + str(detail)) if detail else '')
    print(line.encode(sys.stdout.encoding or 'ascii', 'replace')
              .decode(sys.stdout.encoding or 'ascii'))


def main():
    app = create_app('testing')
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'

    with app.app_context():
        db.create_all()
        from datetime import datetime, timedelta
        from app.models.user import User
        from app.models.audit_log import AuditLog
        from app.services import email_service

        sent = []
        email_service._send = lambda *a, **k: sent.append((a, k))

        def make(email, verified=True, deactivated=False):
            u = User(id=generate_id(), email=email, full_name='Pat Reset',
                     password_hash=bcrypt.generate_password_hash('old-password-1').decode('utf-8'),
                     email_verified=verified,
                     deleted_at=datetime.utcnow() if deactivated else None)
            db.session.add(u)
            db.session.commit()
            return u

        user = make('pat@example.com', verified=False)
        gone = make('gone@example.com', deactivated=True)
        client = app.test_client()

        # ── Request ─────────────────────────────────────────────────────────
        res = client.post('/api/auth/reset-password', json={'email': '  Pat@Example.com '})
        db.session.refresh(user)
        check('request accepted (email trimmed, case-insensitive)',
              res.status_code == 200 and user.password_reset_token, res.status_code)
        token = user.password_reset_token
        body = ' '.join(str(x) for x in sent[-1][0]) + ' ' + ' '.join(str(v) for v in sent[-1][1].values()) if sent else ''
        check('reset email links to the page, not the API',
              ('/reset-password/%s' % token) in body and ('/api/auth/reset-password/%s' % token) not in body)

        res = client.post('/api/auth/reset-password', json={'email': 'gone@example.com'})
        db.session.refresh(gone)
        check('deactivated accounts get no reset link (same 200 response)',
              res.status_code == 200 and not gone.password_reset_token)
        res = client.post('/api/auth/reset-password', json={'email': 'nobody@example.com'})
        check('unknown email still answers 200 (no enumeration)', res.status_code == 200)

        # ── The link ───────────────────────────────────────────────────────
        res = client.get('/api/auth/reset-password/%s' % token)
        check('old emailed API link no longer 405s — redirects to the page',
              res.status_code == 302 and res.headers['Location'].endswith('/reset-password/%s' % token),
              (res.status_code, res.headers.get('Location')))
        res = client.get('/reset-password/%s' % token)
        page = res.get_data(as_text=True)
        check('reset page renders the new-password form',
              res.status_code == 200 and 'Choose a new password' in page and 'newPasswordForm' in page)
        res = client.get('/reset-password/not-a-real-token')
        check('bad token shows the expired page with a way to request a new link',
              res.status_code == 200 and 'expired' in res.get_data(as_text=True))

        # ── Confirm ────────────────────────────────────────────────────────
        res = client.post('/api/auth/reset-password/%s' % token, json={'password': 'short'})
        check('passwords under 8 characters are rejected', res.status_code == 400, res.get_json())
        res = client.post('/api/auth/reset-password/%s' % token, json={'password': 'new-password-2'})
        db.session.refresh(user)
        check('valid reset succeeds', res.status_code == 200, res.get_json())
        check('new password is stored', bcrypt.check_password_hash(user.password_hash, 'new-password-2'))
        check('token is single-use (cleared)', user.password_reset_token is None)
        check('reset verifies the email so sign-in is not blocked', user.email_verified is True)
        check('reset is audit-logged',
              AuditLog.query.filter_by(action='user_password_reset', user_id=user.id).count() == 1)
        res = client.post('/api/auth/reset-password/%s' % token, json={'password': 'another-pass-3'})
        check('reusing the token fails', res.status_code == 400)

        user.password_reset_token = 'expired-token'
        user.password_reset_expires = datetime.utcnow() - timedelta(minutes=1)
        db.session.commit()
        res = client.post('/api/auth/reset-password/expired-token', json={'password': 'another-pass-3'})
        check('expired token fails', res.status_code == 400)
        check('expired token page shows the expired state',
              'expired' in client.get('/reset-password/expired-token').get_data(as_text=True))

        # ── Sign in with the new password ──────────────────────────────────
        res = client.post('/api/auth/login', json={'email': 'pat@example.com', 'password': 'new-password-2'})
        check('user can sign in with the new password', res.status_code == 200, res.get_json())

    print('\n%d passed, %d failed' % (len(PASS), len(FAIL)))
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
