"""Admin outreach reset: a user's contacts back to "never emailed".

Covers the dry run, the confirm requirement, admin-only access, what changes
(contact fields, email history detached from runs, ledger claims, queued
steps, optional timeline), and what must never change (do-not-contact,
suppressed and archived contacts, other users' data).

    python test_outreach_reset.py
"""
import os
import sys
from datetime import datetime, timedelta

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
    app = create_app('testing')
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'

    with app.app_context():
        db.create_all()
        from app.models.user import User
        from app.models.simulation import Simulation
        from app.models.agent_action import AgentAction
        from app.models.agent_recipient import AgentRecipient
        from app.models.action_step import ActionStep
        from app.models.audit_log import AuditLog
        from app.models.contact import Contact, ContactActivity
        from app.models.outreach_email import EmailLog, EmailSuppression
        from app.services import recipient_ledger as ledger

        admin = User(id=generate_id(), email='admin@x.com', full_name='Admin', password_hash='x',
                     is_admin=True, email_verified=True)
        owner = User(id=generate_id(), email='owner@x.com', full_name='Owner', password_hash='x',
                     email_verified=True)
        other = User(id=generate_id(), email='other@x.com', full_name='Other', password_hash='x')
        db.session.add_all([admin, owner, other])
        db.session.flush()
        sim = Simulation(id=generate_id(), user_id=owner.id, name='S', expertise_zone='Ops',
                         status='complete')
        osim = Simulation(id=generate_id(), user_id=other.id, name='O', expertise_zone='Ops',
                          status='complete')
        db.session.add_all([sim, osim])
        db.session.flush()
        run = AgentAction(id=generate_id(), simulation_id=sim.id, layer_number=1,
                          action_type='cold_email_campaign', status='complete',
                          created_at=datetime.utcnow(), completed_at=datetime.utcnow())
        orun = AgentAction(id=generate_id(), simulation_id=osim.id, layer_number=1,
                           action_type='cold_email_campaign', status='complete',
                           completed_at=datetime.utcnow())
        db.session.add_all([run, orun])

        def contact(user, first, email, **kw):
            c = Contact(id=generate_id(), user_id=user.id, first_name=first, last_name='X',
                        email=email, source='manual', **kw)
            db.session.add(c)
            db.session.flush()
            return c

        sent = dict(pipeline_stage='active', last_contacted_at=datetime.utcnow(), outreach_count=2)
        ann = contact(owner, 'Ann', 'ann@a.com', **sent)                    # emailed
        bob = contact(owner, 'Bob', 'bob@b.com')                            # untouched prospect
        dnc = contact(owner, 'Dan', 'dan@d.com', do_not_contact=True, **sent)
        sup = contact(owner, 'Sue', 'sue@s.com', **sent)                    # unsubscribed
        arc = contact(owner, 'Arc', 'arc@a.com', is_archived=True, **sent)
        oth = contact(other, 'Ola', 'ola@o.com', **sent)                    # someone else's
        db.session.add(EmailSuppression(id=generate_id(), email='sue@s.com', reason='unsubscribe'))
        for c, r, s_ in ((ann, run, sim), (sup, run, sim), (oth, orun, osim)):
            db.session.add(EmailLog(id=generate_id(), simulation_id=s_.id, contact_id=c.id,
                                    action_id=r.id, subject='Hi', from_email='me@x.com',
                                    from_name='Me', to_email=c.email, status='sent'))
            db.session.add(ContactActivity(id=generate_id(), contact_id=c.id, simulation_id=s_.id,
                                           action_id=r.id, activity_type='outreach_sent',
                                           created_by='orchestrator'))
        step = ActionStep(id=generate_id(), agent_action_id=run.id, simulation_id=sim.id,
                          step_number=2, total_steps=3, action_type='cold_email_campaign',
                          step_type='email_followup', scheduled_for=datetime.utcnow() + timedelta(days=3))
        step.payload = {'contact_id': ann.id}
        db.session.add(step)
        db.session.commit()
        ledger.record(sim.id, 'cold_email_campaign', run.id, [{'email': 'ann@a.com'}, {'email': 'bob@b.com'}])
        ledger.record(osim.id, 'cold_email_campaign', orun.id, [{'email': 'ola@o.com'}])
        check('before: Ann is blocked for Cold Email',
              'ann@a.com' in ledger.used_keys(sim.id, 'cold_email_campaign'))
        ids = dict(ann=ann.id, bob=bob.id, dnc=dnc.id, sup=sup.id, arc=arc.id, oth=oth.id,
                   step=step.id, sim=sim.id, osim=osim.id, owner=owner.id, admin=admin.id)

        client = app.test_client()

        def login(uid):
            with client.session_transaction() as sess:
                sess['_user_id'] = uid
                sess['_fresh'] = True
            # Requests here share the outer app context, so Flask-Login's
            # per-context user cache must be dropped when switching users.
            from flask import g
            g.pop('_login_user', None)

        url = '/api/admin/users/%s/outreach-reset' % ids['owner']
        print('\n1. Access and dry run')
        login(ids['owner'])
        check('non-admins are refused', client.get(url).status_code in (302, 401, 403))
        login(ids['admin'])
        res = client.get(url)
        p = res.get_json() or {}
        check('preview answers', res.status_code == 200, res.status_code)
        check('preview counts only the resettable emailed contact (Ann)', p.get('contacts_reset') == 1, p)
        check('protected contacts counted separately (do-not-contact, suppressed, archived)',
              p.get('contacts_protected') == 3, p.get('contacts_protected'))
        # Ann needs no claim row: her sent email already blocks her (ledger rule 1),
        # so only Bob's draft claim exists.
        check('preview shows emails, claims, steps and activities',
              p.get('emails_detached') == 1 and p.get('claims_cleared') == 1
              and p.get('steps_cancelled') == 1 and p.get('activities_removable') == 1, p)
        check('the preview changes nothing',
              Contact.query.get(ids['ann']).pipeline_stage == 'active')
        check('POST without confirm is refused',
              client.post(url, json={}).status_code == 400)

        print('\n2. Reset')
        res = client.post(url, json={'confirm': True, 'clear_activities': True})
        r = res.get_json() or {}
        check('reset succeeds', res.status_code == 200 and r.get('ok'), r)
        db.session.expire_all()
        a = Contact.query.get(ids['ann'])
        check('Ann is a fresh prospect again',
              a.pipeline_stage == 'prospect' and a.last_contacted_at is None and a.outreach_count == 0)
        check('the sent email is kept, only detached from its run',
              EmailLog.query.filter_by(contact_id=ids['ann']).count() == 1
              and EmailLog.query.filter_by(contact_id=ids['ann']).first().action_id is None)
        check('Ann is no longer blocked for Cold Email',
              'ann@a.com' not in ledger.used_keys(ids['sim'], 'cold_email_campaign'))
        check('queued follow-up cancelled', ActionStep.query.get(ids['step']).status == 'cancelled')
        check('outreach timeline entry removed (opted in)',
              ContactActivity.query.filter_by(contact_id=ids['ann']).count() == 0)
        for key, label in (('dnc', 'do-not-contact'), ('sup', 'unsubscribed'), ('arc', 'archived')):
            c = Contact.query.get(ids[key])
            check('%s contact untouched' % label,
                  c.pipeline_stage == 'active' and c.outreach_count == 2, (c.pipeline_stage, c.outreach_count))
        check('unsubscribed contact\'s email history still linked',
              EmailLog.query.filter_by(contact_id=ids['sup']).first().action_id is not None)
        o = Contact.query.get(ids['oth'])
        check('other users are untouched',
              o.pipeline_stage == 'active'
              and 'ola@o.com' in ledger.used_keys(ids['osim'], 'cold_email_campaign'))
        log = AuditLog.query.filter_by(action='admin_outreach_reset').first()
        check('reset is audit-logged by the admin',
              log is not None and log.user_id == ids['admin'] and log.resource_id == ids['owner'])
        again = client.get(url).get_json() or {}
        check('a second preview finds nothing left to reset',
              again.get('contacts_reset') == 0 and again.get('emails_detached') == 0
              and again.get('claims_cleared') == 0, again)

    print('\n%d passed, %d failed' % (len(PASS), len(FAIL)))
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
