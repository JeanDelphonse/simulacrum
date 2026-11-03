"""PRD-SIM-TRANS-001 phase 1: Employer Transition Package.

Email is faked; requests is blocked so a stray call fails.

    python test_transition.py
"""
import os
import re
import sys
from datetime import date, datetime, timedelta

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

    with app.app_context():
        db.create_all()
        from flask import g
        from app.models.user import User
        from app.models.profile import UserProfile
        from app.models.corporate import CorporateAccount
        from app.models.sme import SimiSME
        from app.models.platform_settings import PlatformSetting
        from app.models.transition import (CoachingNote, CoachingSession, TransitionCohort, TransitionOutcome,
                                           TransitionSeat)
        from app.models.bio_page import BioPage
        from app.models.simulation import Simulation
        from app.services import transition_service as ts, org_service, mail_sync, mail_feature
        import app.services.email_service as es

        sent = []
        es.send_transition_invite_email = lambda *a, **k: sent.append((a, k))
        import utils.action_items as ai
        ai._send_escalation_email = lambda item: None

        now = datetime.utcnow()

        def user(email, name, admin=False):
            u = User(id=generate_id(), email=email, full_name=name, password_hash='x', is_admin=admin,
                     email_verified=True, onboarding_completed_at=now)
            db.session.add(u)
            db.session.flush()
            db.session.add(UserProfile(user_id=u.id, username=email.split('@')[0].replace('.', '-'),
                                       display_name=name))
            db.session.commit()
            return u

        staff = user('staff@simisi.io', 'Staff', admin=True)
        hr = user('hr@acme.com', 'Harriet HR')
        stranger = user('x@x.com', 'Stranger')
        S, H, X = staff.id, hr.id, stranger.id

        client = app.test_client()
        for verb in ('get', 'post', 'put', 'delete'):
            orig = getattr(client, verb)
            setattr(client, verb, (lambda o: lambda *a, **k: (g.pop('_login_user', None), o(*a, **k))[1])(orig))

        def login(uid):
            with client.session_transaction() as s:
                s['_user_id'], s['_fresh'] = uid, True
            g.pop('_login_user', None)

        def logout():
            with client.session_transaction() as s:
                s.pop('_user_id', None)
            g.pop('_login_user', None)

        print('\n1. Tiers and pricing (admin settings, nothing hardcoded)')
        t = ts.tiers()
        check('three tiers with the PRD defaults', [t[k]['access_months'] for k in ('essentials', 'plus', 'executive')]
              == [3, 6, 12] and [t[k]['coaching_sessions'] for k in ('essentials', 'plus', 'executive')] == [0, 3, 8])
        q = ts.quote({'essentials': 10})
        check('no price until an admin sets one', q['missing_prices'] == ['Essentials'] and q['total_cents'] == 0)
        PlatformSetting.set('transition_price_essentials_cents', '40000')
        PlatformSetting.set('transition_price_plus_cents', '80000')
        PlatformSetting.set('transition_volume_bands', '[[20, 10]]')
        db.session.commit()
        q = ts.quote({'essentials': 15, 'plus': 5})
        check('quote uses admin prices and volume bands', q['gross_cents'] == 15 * 40000 + 5 * 80000
              and q['discount_pct'] == 10 and q['total_cents'] == int((15 * 40000 + 5 * 80000) * 0.9), q)

        print('\n2. Staff setup: employer, cohort, seats')
        login(S)
        res = client.post('/api/admin/transition/employers', json={'name': 'Acme Corp', 'contact_name': 'Harriet',
                                                                 'contact_email': 'hr@acme.com'})
        st = res.get_json()
        check('employer created as an organization of type transition', res.status_code == 200
              and st['employers'][0]['name'] == 'Acme Corp' and st['employers'][0]['hr_login'] == 'hr@acme.com')
        org = CorporateAccount.query.filter_by(org_type='transition').first()
        org_id = org.id
        check('no credit pool on a transition employer', org.credits_remaining == 0)
        res = client.post('/api/admin/transition/employers/%s/cohorts' % org_id, json={
            'name': 'March reduction', 'last_working_day': (date.today() + timedelta(days=60)).isoformat(),
            'invite_date': (date.today() + timedelta(days=2)).isoformat(), 'employer_domains': 'acme.com, acme.io'})
        cohort = TransitionCohort.query.filter_by(org_id=org_id).first()
        check('cohort created with employer domains', res.status_code == 200 and cohort.employer_domains == ['acme.com', 'acme.io'])
        roster = ('name,personal email,tier\n'
                  'Ann Lee,ann@gmail.com,essentials\nBo Diaz,bo@yahoo.com,executive\nCy Ng,cy@outlook.com,plus\n'
                  'Dee Roe,dee@gmail.com,plus\nEd Kim,ed@gmail.com,essentials\nFay Wu,fay@gmail.com,essentials\n'
                  'Bad Row,not-an-email,plus\nAnn Again,ANN@gmail.com,plus\nOdd Tier,odd@gmail.com,platinum\n')
        res = client.post('/api/admin/transition/cohorts/%s/seats' % cohort.id,
                          json={'roster_csv': roster, 'unassigned': {'plus': 2}})
        r = res.get_json()['result']
        check('roster rows become seats; bad, duplicate and unknown-tier rows are skipped',
              r['added'] == 8 and len(r['skipped']) == 3, r)
        seats = {ts._decrypt(s.invite_email_enc): s for s in TransitionSeat.query.filter_by(cohort_id=cohort.id).all()}
        check('personal emails are stored encrypted', all('gmail.com' not in (s.invite_email_enc or '')
              for s in seats.values()) and 'ann@gmail.com' in seats)
        check('unassigned seats exist', TransitionSeat.query.filter_by(cohort_id=cohort.id, status='unassigned').count() == 2)

        print('\n3. Invites and reminders')
        ts.send_due_invites()
        check('no invite before the cohort\'s invite date', not sent)
        cohort.invite_date = date.today()
        db.session.commit()
        n = ts.send_due_invites()
        check('invites go out on the invite date', n == 6 and len(sent) == 6, n)
        check('invite names the employer and tier', sent[0][0][2] == 'Acme Corp' and sent[0][0][4] in (3, 6, 12))
        ann = TransitionSeat.query.get(seats['ann@gmail.com'].id)
        ann.invite_sent_at = now - timedelta(days=4)
        db.session.commit()
        check('reminder on day 3', ts.send_due_reminders() == 1 and TransitionSeat.query.get(ann.id).reminder_count == 1)
        check('…not twice for the same day', ts.send_due_reminders() == 0)
        r = ts.run_maintenance()
        check('maintenance runs at most hourly', ts.run_maintenance() == {'skipped': True}, r)

        print('\n4. Employee activation')
        token = TransitionSeat.query.get(ann.id).invite_token
        logout()
        page = client.get('/transition/join/%s' % token).get_data(as_text=True)
        check('the invite page says what the employer can and never sees', 'never sees' in page
              and 'Your simulation, income paths or plans' in page and 'only if you choose to share it' in page)
        check('…and asks for sign-up with a personal email', 'personal' in page and 'Create my account' in page)
        emp = user('ann.lee@gmail.com', 'Ann Lee')
        A = emp.id
        with client.session_transaction() as s:
            s['transition_invite'] = token
        login(A)
        dash = client.get('/dashboard').get_data(as_text=True)
        check('the remembered invite shows on the dashboard', 'transition package waiting' in dash)
        res = client.post('/api/transition/join/%s' % token, json={'consent': False})
        check('activation requires consent to the privacy summary', res.status_code == 400)
        res = client.post('/api/transition/join/%s' % token, json={'consent': True, 'share_outcomes': True})
        seat = TransitionSeat.query.get(ann.id)
        check('activated with the tier\'s entitlements', res.status_code == 200 and seat.status == 'activated'
              and seat.sims_allowed == 2 and seat.sessions_total == 0 and seat.access_months == 3
              and 85 <= (seat.expires_at - seat.activated_at).days <= 93, (seat.sims_allowed, seat.expires_at))
        check('the invite token is single-use', seat.invite_token is None and
              client.get('/transition/join/%s' % token).status_code == 410)
        check('co-branding links the profile to the employer', UserProfile.query.filter_by(user_id=A).first().org_id == org_id)

        print('\n5. Seat funds simulations')
        s1 = Simulation(id=generate_id(), user_id=A, name='S1', status='pending')
        s2 = Simulation(id=generate_id(), user_id=A, name='S2', status='pending')
        s3 = Simulation(id=generate_id(), user_id=A, name='S3', status='pending')
        db.session.add_all([s1, s2, s3])
        db.session.commit()
        funded = [org_service.try_redeem_credit(A, s) for s in (s1, s2, s3)]
        db.session.commit()
        check('first run + one re-run are free, the third is not', funded == [True, True, False], funded)

        print('\n6. Revoke and reassign')
        un = TransitionSeat.query.filter_by(cohort_id=cohort.id, status='unassigned').first()
        login(S)
        check('an activated seat can\'t be revoked', client.post('/api/admin/transition/seats/%s/revoke' % ann.id).status_code == 400)
        res = client.post('/api/admin/transition/seats/%s/assign' % un.id, json={'name': 'Gus Oh', 'email': 'gus@gmail.com'})
        check('an unassigned seat can be assigned', res.status_code == 200 and TransitionSeat.query.get(un.id).status == 'invited')
        res = client.post('/api/admin/transition/seats/%s/revoke' % un.id)
        check('an unused seat can be revoked', res.status_code == 200 and TransitionSeat.query.get(un.id).status == 'revoked')

        print('\n7. Employer report: aggregate only, small groups hidden')
        login(H)
        rep = ts.report(CorporateAccount.query.get(org_id))
        check('with fewer than 5 activated, only seat counts show', rep['suppressed'] and 'engagement' not in rep
              and rep['seats']['activated'] == 1, rep)
        page = client.get('/transition/employer/%s' % org_id)
        html = page.get_data(as_text=True)
        check('HR can open the report', page.status_code == 200 and 'Fewer than 5 employees have activated' in html)
        check('…which shows seat status per person and nothing else about them',
              'Ann Lee' in html and 'activated' in html and 'S1' not in html and 'ran a simulation' not in html)
        login(X)
        check('another user can\'t open it', client.get('/transition/employer/%s' % org_id).status_code == 403)
        # Activate five more so the group is big enough.
        others = []
        for email in ('bo@yahoo.com', 'cy@outlook.com', 'dee@gmail.com', 'ed@gmail.com', 'fay@gmail.com'):
            u = user('p.' + email.split('@')[0] + '@mail.com', email.split('@')[0].title())
            ts.activate(TransitionSeat.query.get(seats[email].id).invite_token, u, share_outcomes=email != 'fay@gmail.com',
                        consent=True)
            others.append(u.id)
        db.session.add(BioPage(id=generate_id(), user_id=A, slug='ann-lee', status=BioPage.STATUS_PUBLISHED))
        db.session.commit()
        for i, uid in enumerate([A] + others[:2]):
            ts.report_outcome(uid, 'new_role' if i < 2 else 'consulting', share=True)
        rep = ts.report(CorporateAccount.query.get(org_id))
        check('with 6 activated, engagement numbers appear', not rep['suppressed'] and rep['activation_rate'] > 0)
        check('small counts are shown as "<5"', rep['engagement']['bio_pages_live'] == '<5'
              and rep['engagement']['ran_simulation'] == '<5', rep['engagement'])
        check('outcomes are hidden while fewer than 5 have shared', rep['outcomes']['suppressed']
              and rep['outcomes']['reported'] == '<5')
        ts.report_outcome(others[2], 'searching', share=True)
        ts.report_outcome(others[3], 'searching', share=False)       # kept private
        ts.report_outcome(others[4], 'venture', share=True)
        ts.set_sharing(others[4], False)                             # …and withdrawn later
        rep = ts.report(CorporateAccount.query.get(org_id))
        check('private and withdrawn outcomes are never counted',
              rep['outcomes']['suppressed'] and rep['outcomes']['reported'] == '<5', rep['outcomes'])
        ts.set_sharing(others[4], True)
        rep = ts.report(CorporateAccount.query.get(org_id))
        check('with 5 sharing, the breakdown shows, small cells as "<5"', not rep['outcomes']['suppressed']
              and rep['outcomes']['reported'] == 5
              and {o['outcome']: o['count'] for o in rep['outcomes']['by_outcome']}['new_role'] == '<5', rep['outcomes'])
        check('the report holds counts only — no names, simulations or income', not re.search(
            r'Ann|Lee|S1|income_by|amount', str(rep)))
        login(H)
        csv_text = client.get('/transition/employer/%s/seats.csv' % org_id).get_data(as_text=True)
        check('seat-status CSV export', csv_text.startswith('Cohort,Name,Tier,Status') and 'Ann Lee' in csv_text
              and '@' not in csv_text)
        res = client.get('/corporate/%s' % org_id)
        check('the general org dashboard redirects transition employers to the strict report',
              res.status_code == 302 and '/transition/employer/' in res.headers['Location'])

        print('\n8. Protecting the employer\'s information')
        mail_feature.set_user([A], 'allowed', 'Beta', S)
        try:
            mail_sync.connect_imap(A, 'ann@acme.com', 'pw', 'imap.acme.com')
            blocked = False
        except mail_sync.SyncError as exc:
            blocked = 'former employer' in str(exc)
        check('email import refuses a mailbox on the employer\'s domain', blocked)
        p = UserProfile.query.filter_by(user_id=A).first()
        p.career_history = [{'company_name': 'Acme', 'role': 'Analyst', 'start_date': '2019', 'source': 'manual'},
                            {'company_name': 'Beta', 'role': 'Lead', 'start_date': '2015', 'source': 'manual'}]
        db.session.commit()
        login(A)
        res = client.post('/api/bio/publish', json={})
        check('publishing first shows the confidentiality reminder', res.status_code == 428
              and 'confidential' in res.get_json()['message'], res.status_code)
        res = client.post('/api/bio/publish', json={'confidentiality_ack': True})
        check('…and publishes once acknowledged', res.status_code == 200
              and TransitionSeat.query.get(ann.id).confidentiality_ack_at is not None, res.get_json())
        check('the reminder is asked only once', client.post('/api/bio/publish', json={}).status_code == 200)

        print('\n9. Coaching')
        coach_user = user('coach@coach.com', 'Cora Coach')
        senior_user = user('senior@coach.com', 'Sam Senior')
        c1 = SimiSME(id=generate_id(), first_name='Cora', last_name='Coach', email='coach@coach.com',
                     auth_user_id=coach_user.id, is_coach=True, coach_booking_url='https://cal.com/cora')
        c2 = SimiSME(id=generate_id(), first_name='Sam', last_name='Senior', email='senior@coach.com',
                     auth_user_id=senior_user.id, is_coach=True, coach_seniority='senior')
        db.session.add_all([c1, c2])
        db.session.commit()
        bo_seat = TransitionSeat.query.get(seats['bo@yahoo.com'].id)
        ts.match_coach(bo_seat)
        db.session.commit()
        check('executive seats get a senior coach', bo_seat.coach_id == c2.id and bo_seat.sessions_total == 8)
        cy_seat = TransitionSeat.query.get(seats['cy@outlook.com'].id)
        cy_seat.coach_id = c1.id
        db.session.commit()
        CY = cy_seat.user_id
        coach = SimiSME.query.get(c1.id)
        s_held = ts.log_session(coach, cy_seat.id, 'held')
        ts.log_session(coach, cy_seat.id, 'no_show')
        ts.log_session(coach, cy_seat.id, 'cancelled')
        cy_seat = TransitionSeat.query.get(cy_seat.id)
        check('held uses a credit; the first no-show is forgiven; cancelled never counts',
              cy_seat.sessions_used == 1 and cy_seat.no_shows == 1, (cy_seat.sessions_used, cy_seat.no_shows))
        ts.log_session(coach, cy_seat.id, 'no_show')
        check('a second no-show uses a credit', TransitionSeat.query.get(cy_seat.id).sessions_used == 2)
        try:
            ts.log_session(coach, cy_seat.id, 'held')
            ts.log_session(coach, cy_seat.id, 'held')
            over = False
        except ts.TransitionError:
            over = True
        check('no more sessions than the tier includes', over and TransitionSeat.query.get(cy_seat.id).sessions_used == 3)
        login(coach_user.id)
        cl = client.get('/api/coach/caseload').get_json()['caseload']
        row = next(r for r in cl if r['seat_id'] == cy_seat.id)
        check('the coach doesn\'t see the simulation without access', 'simulations' not in row and not row['access'])
        login(CY)
        client.post('/api/transition/coach-access', json={'granted': True})
        login(coach_user.id)
        row = next(r for r in client.get('/api/coach/caseload').get_json()['caseload'] if r['seat_id'] == cy_seat.id)
        check('…and does once the employee grants it', row['access'] and 'simulations' in row)
        res = client.post('/api/coach/sessions/%s/notes' % s_held.id, json={'body': 'Explore consulting first.'})
        check('the coach can write a note', res.status_code == 200)
        note = CoachingNote.query.filter_by(session_id=s_held.id).first()
        check('notes are encrypted at rest', 'Explore consulting' not in note.body_enc)
        login(CY)
        got = client.get('/api/transition/sessions/%s/notes' % s_held.id).get_json()
        check('the employee reads the coach\'s note', got['notes'][0]['body'] == 'Explore consulting first.')
        login(X)
        check('nobody else can read them', client.get('/api/transition/sessions/%s/notes' % s_held.id).status_code == 404)
        login(H)
        check('HR can\'t read them either', client.get('/api/transition/sessions/%s/notes' % s_held.id).status_code == 404)
        login(CY)
        client.post('/api/transition/sessions/%s/rate' % s_held.id, json={'rating': 2})
        # A third rated session (another client) reaches the 3-rating minimum.
        dee_seat = TransitionSeat.query.get(seats['dee@gmail.com'].id)
        dee_seat.coach_id = c1.id
        db.session.commit()
        s_dee = ts.log_session(SimiSME.query.get(c1.id), dee_seat.id, 'held')
        ts.rate_session(dee_seat.user_id, s_dee.id, 3)
        login(CY)
        held = [x for x in CoachingSession.query.filter_by(seat_id=cy_seat.id, status='held').all() if x.id != s_held.id]
        for x in held:
            client.post('/api/transition/sessions/%s/rate' % x.id, json={'rating': 3})
        c1 = SimiSME.query.get(c1.id)
        check('ratings average onto the coach; below 4.0 pauses them for review',
              c1.coach_avg_rating < 4 and c1.coach_status == 'review', (c1.coach_avg_rating, c1.coach_status))
        check('a coach under review gets no new matches', all(c.id != c1.id for c in ts.coaches()))
        res = client.post('/api/transition/sessions/%s/rate' % s_held.id, json={'rating': 5})
        check('a session is rated once', res.status_code == 400)
        page = client.get('/transition').get_data(as_text=True)
        check('the employee page shows the package, coach and outcomes', 'My transition package' in page
              and 'Cora Coach' in page and 'What happened next?' in page)

        print('\n10. Expiry')
        seat = TransitionSeat.query.get(ann.id)
        seat.expires_at = datetime.utcnow() - timedelta(minutes=1)
        db.session.commit()
        ts.expire_seats()
        seat = TransitionSeat.query.get(ann.id)
        check('the seat expires at the end of access', seat.status == 'expired')
        check('the account stays theirs; co-branding ends', UserProfile.query.filter_by(user_id=A).first().org_id is None
              and BioPage.query.filter_by(user_id=A).first().status == BioPage.STATUS_PUBLISHED)
        s4 = Simulation(id=generate_id(), user_id=A, name='S4', status='pending')
        db.session.add(s4)
        db.session.commit()
        check('no more free simulations after expiry', not org_service.try_redeem_credit(A, s4))

        print('\n11. Pages')
        login(S)
        check('admin page renders', 'Transition Package' in client.get('/admin/transition').get_data(as_text=True))
        login(coach_user.id)
        check('coach console renders', 'Coaching — Cora Coach' in client.get('/coach').get_data(as_text=True))
        login(X)
        check('non-coaches can\'t open the coach console', client.get('/coach').status_code == 403)
        check('non-admins can\'t use staff APIs', client.post('/api/admin/transition/employers', json={}).status_code == 403)
        check('no network calls at any point', not network, network)

    print('\n%d passed, %d failed' % (len(PASS), len(FAIL)))
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
