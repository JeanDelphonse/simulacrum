"""Co-founder invite card: invitees who sign up through the link accept from the
dashboard or /founder (they never get back to the join page)."""
import os
import sys
from datetime import datetime

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
    from datetime import timedelta
    from app import create_app
    from app.extensions import db
    from utils.id_gen import generate_id
    app = create_app('testing')
    app.config.update(SQLALCHEMY_DATABASE_URI='sqlite:///:memory:', WTF_CSRF_ENABLED=False,
                      UPLOAD_FOLDER=tempfile.mkdtemp(prefix='invcard_'))
    from app.services import email_service
    email_service._send = lambda *a, **k: None

    with app.app_context():
        db.create_all()
        from flask import g
        from app.models.founder import VentureMember
        from app.models.user import User
        from app.services import founder_alignment_service as fas
        from app.services import founder_feature, founder_ventures
        now = datetime.utcnow()

        def user(email, name, admin=False, verified=True):
            u = User(id=generate_id(), email=email, full_name=name, password_hash='x', is_admin=admin,
                     email_verified=verified, onboarding_completed_at=now)
            db.session.add(u)
            db.session.commit()
            return u.id

        A_ID = user('admin@x.com', 'Admin', admin=True)
        O_ID = user('owner@x.com', 'Olive Owner')
        founder_feature.set_mode('founder_track', 'on', A_ID)
        v = founder_ventures.create_venture(db.session.get(User, O_ID), 'Pze-Charging', 'EV charging', 'seed')
        v.paid_at = now
        db.session.commit()
        V = v.id

        client = app.test_client()
        for verb in ('get', 'post'):
            orig = getattr(client, verb)
            setattr(client, verb, (lambda o: lambda *a, **k: (g.pop('_login_user', None), o(*a, **k))[1])(orig))

        def login(uid):
            with client.session_transaction() as s:
                s['_user_id'], s['_fresh'] = uid, True

        login(O_ID)
        r = client.post('/api/founder/v/%s/alignment/invite' % V,
                        json={'email': 'Jens@X.com', 'title': 'Co-CEO', 'team_role': 'cofounder'})
        check('the owner invites a co-founder', r.status_code == 201, r.get_json())
        inv = VentureMember.query.filter_by(venture_id=V, status='invited').first()

        print('\n1. The invitee signs up later (never returns to the join link)')
        J_ID = user('jens@x.com', 'Jens Andersen')
        login(J_ID)
        html = client.get('/dashboard').get_data(as_text=True)
        check('the dashboard shows the invite card', 'fiPending' in html and 'Pze-Charging' in html
              and 'Olive Owner invited you as Co-founder' in html and 'Co-CEO' in html)
        html = client.get('/founder').get_data(as_text=True)
        check('/founder shows the invite card', 'fiPending' in html and 'Pze-Charging' in html)

        print('\n2. Who does not see it')
        U_ID = user('jens@x.com.evil', 'Other')
        login(U_ID)
        check('another account does not see it', 'fiPending' not in client.get('/dashboard').get_data(as_text=True))
        N_ID = user('nov@x.com', 'Unverified', verified=False)
        inv2 = VentureMember(venture_id=V, invite_email='nov@x.com', invite_token=generate_id() + 'tok2',
                             role='cofounder', status='invited')
        db.session.add(inv2)
        db.session.commit()
        check('an unverified account sees no card', fas.pending_invites_for(db.session.get(User, N_ID)) == [])
        login(N_ID)
        r = client.post('/founder/invites/%s/accept' % inv2.id)
        check('... and cannot accept from the card', db.session.get(VentureMember, inv2.id).status == 'invited', r.status_code)
        login(U_ID)
        client.post('/founder/invites/%s/accept' % inv.id)
        check('a different account cannot accept someone else\'s invite',
              db.session.get(VentureMember, inv.id).status == 'invited')
        inv.created_at = now - timedelta(days=fas.invite_days() + 1)
        db.session.commit()
        check('an expired invite is not listed', fas.pending_invites_for(db.session.get(User, J_ID)) == [])
        inv.created_at = now
        db.session.commit()

        print('\n3. Accepting from the card')
        login(J_ID)
        r = client.post('/founder/invites/%s/accept' % inv.id)
        m = db.session.get(VentureMember, inv.id)
        check('accept links the account and marks the member active',
              m.status == 'active' and m.user_id == J_ID and m.joined_at is not None, (m.status, m.user_id))
        check('... and goes to the alignment page', r.status_code == 302 and '/founder/v/%s/alignment' % V in r.headers.get('Location', ''),
              r.headers.get('Location'))
        check('the card is gone afterwards', 'fiPending' not in client.get('/dashboard').get_data(as_text=True))
        login(O_ID)
        html = client.get('/founder/v/%s/alignment' % V).get_data(as_text=True)
        check('the owner no longer sees them as waiting to join', 'Jens Andersen' in html)
        r = client.post('/founder/invites/%s/accept' % inv.id)
        check('accepting a used invite again changes nothing', db.session.get(VentureMember, inv.id).user_id == J_ID)

    print('\n%d passed, %d failed' % (len(PASS), len(FAIL)))
    for label in FAIL:
        print('  FAILED: ' + label)
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
