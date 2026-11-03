"""Org invite accept: the member is always the signed-in user.

Before the fix, POST /api/corporate/invite/<token>/accept took user_id from the
request body with no login, so anyone holding a token could attach any account
to an organization.

    python test_org_invite_security.py
"""
import os
import sys
from datetime import datetime

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
    from app import create_app
    from app.extensions import db
    from utils.id_gen import generate_id
    app = create_app('testing')
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
    with app.app_context():
        db.create_all()
        from flask import g
        from app.models.user import User
        from app.models.profile import UserProfile
        from app.models.corporate import CorporateAccount, CorporateEmployee

        def user(email):
            u = User(id=generate_id(), email=email, full_name=email.split('@')[0], password_hash='x',
                     email_verified=True, onboarding_completed_at=datetime.utcnow())
            db.session.add(u)
            db.session.flush()
            db.session.add(UserProfile(user_id=u.id, username=email.split('@')[0]))
            db.session.commit()
            return u

        victim, attacker, invitee = user('victim@x.com'), user('attacker@x.com'), user('invitee@x.com')
        V, A, I = victim.id, attacker.id, invitee.id
        org = CorporateAccount(id=generate_id(), org_name='Acme', contact_name='HR', contact_email='hr@acme.com',
                               status=CorporateAccount.STATUS_ACTIVE)
        db.session.add(org)
        db.session.flush()
        emp = CorporateEmployee(id=generate_id(), org_id=org.id, email='invitee@x.com', invite_token='tok123')
        db.session.add(emp)
        db.session.commit()
        emp_id = emp.id

        client = app.test_client()

        def login(uid):
            with client.session_transaction() as s:
                s['_user_id'], s['_fresh'] = uid, True
            g.pop('_login_user', None)

        res = client.post('/api/corporate/invite/tok123/accept', json={'user_id': V})
        e = CorporateEmployee.query.get(emp_id)
        check('without signing in, the invite can\'t be accepted', res.status_code in (302, 401) and e.user_id is None
              and e.status == 'invited', res.status_code)

        login(A)
        res = client.post('/api/corporate/invite/tok123/accept', json={'user_id': V})
        db.session.expire_all()
        e = CorporateEmployee.query.get(emp_id)
        check('a body user_id is ignored: the victim is never linked',
              e.user_id != V and UserProfile.query.filter_by(user_id=V).first().org_id is None)
        check('the signed-in user is the one linked', res.status_code == 200 and e.user_id == A, res.get_json())
        check('the token is single-use', client.post('/api/corporate/invite/tok123/accept', json={}).status_code in (404, 409))

        emp2 = CorporateEmployee(id=generate_id(), org_id=org.id, email='invitee2@x.com', invite_token='tok456')
        db.session.add(emp2)
        db.session.commit()
        login(I)
        res = client.post('/api/corporate/invite/tok456/accept', json={})
        db.session.expire_all()
        check('the normal flow still works (no body needed)', res.status_code == 200
              and CorporateEmployee.query.get(emp2.id).user_id == I
              and UserProfile.query.filter_by(user_id=I).first().org_id == org.id)
        page = client.get('/corporate/invite/tok456').get_data(as_text=True)
        check('the invite page no longer sends a user_id', 'user_id' not in page)

    print('\n%d passed, %d failed' % (len(PASS), len(FAIL)))
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
