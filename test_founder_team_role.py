"""Team roles on /founder/v/<id>/alignment (sql/077).

The account owner need not be a founder: each member has a team role
(Founder, Co-founder, Advisor, Team member) separate from the account
permission. The owner sets anyone's, a member their own; at least one
founder stays; only founders and co-founders take the alignment check; invites
carry a role; the startup page lists founders first with their role.
In-memory SQLite; network and email blocked.

    python test_founder_team_role.py
"""
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
    from app import create_app
    from app.extensions import db
    from utils.id_gen import generate_id
    app = create_app('testing')
    app.config.update(SQLALCHEMY_DATABASE_URI='sqlite:///:memory:', WTF_CSRF_ENABLED=False,
                      UPLOAD_FOLDER=tempfile.mkdtemp(prefix='teamrole_'))
    from app.services import email_service
    email_service._send = lambda *a, **k: None

    with app.app_context():
        db.create_all()
        from flask import g
        from app.models.founder import VentureMember
        from app.models.user import User
        from app.services import founder_alignment_service as fas
        from app.services import founder_feature, founder_ventures
        from app.services import startup_page_service as sps
        now = datetime.utcnow()

        def user(email, name, admin=False):
            u = User(id=generate_id(), email=email, full_name=name, password_hash='x', is_admin=admin,
                     email_verified=True, onboarding_completed_at=now)
            db.session.add(u)
            db.session.commit()
            return u.id

        A_ID = user('admin@x.com', 'Admin', admin=True)
        O_ID = user('owner@x.com', 'Olive Assistant')
        C_ID = user('cora@x.com', 'Cora Founder')
        D_ID = user('dev@x.com', 'Dan Dev')
        X_ID = user('out@x.com', 'Outsider')
        founder_feature.set_mode('founder_track', 'on', A_ID)
        v = founder_ventures.create_venture(db.session.get(User, O_ID), 'Dockwise', 'Dock scheduling', 'seed')
        v.paid_at = now
        db.session.add(VentureMember(venture_id=v.id, user_id=C_ID, role='cofounder', status='active', title='CEO',
                                     joined_at=now))
        db.session.commit()
        V = v.id

        client = app.test_client()
        for verb in ('get', 'post'):
            orig = getattr(client, verb)
            setattr(client, verb, (lambda o: lambda *a, **k: (g.pop('_login_user', None), o(*a, **k))[1])(orig))

        def login(uid):
            with client.session_transaction() as s:
                s['_user_id'], s['_fresh'] = uid, True

        def member(uid):
            return VentureMember.query.filter_by(venture_id=V, user_id=uid).first()

        def setm(uid, mid, **body):
            login(uid)
            r = client.post('/api/founder/v/%s/alignment/members/%s' % (V, mid), json=body)
            return r.status_code, (r.get_json(silent=True) or {})

        print('\n1. Defaults: owner Founder, others Co-founder')
        check('the owner defaults to Founder', member(O_ID).team_role_key == 'founder' and member(O_ID).team_role is None)
        check('a co-founder defaults to Co-founder', member(C_ID).team_role_key == 'cofounder')
        login(O_ID)
        html = client.get('/founder/v/%s/alignment' % V).get_data(as_text=True)
        check('"Your team" shows team roles and an edit form', 'Your team' in html and 'data-member-form="%s"' % member(O_ID).id in html
              and '>Advisor<' in html and 'account owner' in html)

        print('\n2. The owner says they are not a founder')
        code, body = setm(O_ID, member(O_ID).id, team_role='advisor', title='Operations')
        check('the owner sets their own team role to Advisor', code == 200 and member(O_ID).team_role == 'advisor'
              and member(O_ID).title == 'Operations', (code, body))
        check('... and keeps the account permission', member(O_ID).role == 'owner')
        check('the alignment check now counts founders only (one founder: skipped as solo)', fas.is_solo(v))
        login(O_ID)
        html = client.get('/founder/v/%s/alignment' % V).get_data(as_text=True)
        check('the page still shows "Your team" so roles can be changed', 'Your team' in html
              and 'data-member-form' in html, html[:0])
        login(O_ID)
        r = client.post('/api/founder/v/%s/alignment/answers' % V, json={'answers': {'roles': {'choice': 'x'}}})
        check('an advisor cannot answer the alignment check', r.status_code == 400
              and 'founders and co-founders' in (r.get_json() or {}).get('error', ''), r.get_json())

        print('\n3. Who may change what')
        code, body = setm(C_ID, member(O_ID).id, team_role='founder')
        check("a co-founder cannot change the owner's role", code == 403, (code, body))
        code, body = setm(C_ID, member(C_ID).id, title='CEO and co-founder')
        check('a member can change their own title', code == 200 and member(C_ID).title == 'CEO and co-founder')
        code, body = setm(C_ID, member(C_ID).id, team_role='team_member')
        check('the last founder cannot step down (one founder must remain)', code == 400
              and 'founder or co-founder' in body.get('error', '') and member(C_ID).team_role_key == 'cofounder', body)
        code, body = setm(O_ID, member(C_ID).id, team_role='chairman')
        check('an unknown team role is refused', code == 400)
        code, _ = setm(X_ID, member(C_ID).id, team_role='founder')
        check('a non-member cannot change roles', code in (403, 404), code)
        code, body = setm(O_ID, member(C_ID).id, team_role='founder')
        check('the owner can set anyone\'s role', code == 200 and member(C_ID).team_role == 'founder')

        print('\n4. Invites carry a team role; the check counts founders only')
        login(O_ID)
        r = client.post('/api/founder/v/%s/alignment/invite' % V, json={'email': 'dev@x.com', 'title': 'Engineer',
                                                                          'team_role': 'team_member'})
        inv = VentureMember.query.filter_by(venture_id=V, invite_email='dev@x.com').first()
        check('an invite records the chosen team role', r.status_code == 201 and inv and inv.team_role == 'team_member',
              r.get_json())
        inv.user_id, inv.status, inv.joined_at = D_ID, 'active', now
        db.session.add(VentureMember(venture_id=V, user_id=X_ID, role='cofounder', status='active', joined_at=now))
        db.session.commit()
        view = fas.alignment_view(v, db.session.get(User, C_ID))
        check('the alignment check counts only founders and co-founders',
              all(q['total'] == 2 for q in view['questions']) and len(view['members']) == 4,
              ([q['total'] for q in view['questions']][:2], len(view['members'])))
        check('a team member is not asked to answer', not fas.alignment_view(v, db.session.get(User, D_ID))['me_founding'])

        print('\n5. The startup page lists founders first, with their role')
        team = sps._team(v)
        names = [t['name'] for t in team]
        check('founders come before advisors and team members',
              names.index('Cora Founder') < names.index('Olive Assistant') and names.index('Cora Founder') < names.index('Dan Dev'),
              names)
        by = {t['name']: t['title'] for t in team}
        check('each person shows team role and title', by.get('Cora Founder') == 'Founder · CEO and co-founder'
              and by.get('Olive Assistant') == 'Advisor · Operations' and by.get('Dan Dev') == 'Team member · Engineer', by)

        print('\n6. Deploy')
        check('the migration exists', os.path.exists(os.path.join(ROOT, 'sql', '077_team_role.sql')))

    print('\n%d passed, %d failed' % (len(PASS), len(FAIL)))
    for label in FAIL:
        print('  FAILED: ' + label)
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
