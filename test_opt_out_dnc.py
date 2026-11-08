"""Opting out sets Do Not Contact: unsubscribe link, reply STOP, spam report.

    python test_opt_out_dnc.py    (in-memory SQLite, no network)
"""
import os
import sys
import warnings
from datetime import datetime, timedelta

warnings.filterwarnings('ignore')
os.environ.setdefault('DATABASE_URL', 'sqlite:///:memory:')
os.environ.setdefault('SECRET_KEY', 'test-only')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PASS, FAIL = [], []


def check(label, condition, detail=''):
    (PASS if condition else FAIL).append(label)
    print('  %s %s%s' % ('ok  ' if condition else 'FAIL', label, ('  -- %s' % (detail,)) if detail and not condition else ''))


def main():
    from app import create_app
    from app.extensions import db
    app = create_app('testing')
    app.config.update(SQLALCHEMY_DATABASE_URI='sqlite:///:memory:', WTF_CSRF_ENABLED=False)
    with app.app_context():
        from flask import g
        from app import models as _models  # noqa: F401
        db.create_all()
        from app.models.user import User
        from app.models.contact import Contact, ContactActivity
        from app.models.mail_crm import ContactDncFlag
        from app.models.outreach_email import EmailSuppression
        from app.models.action_step import ActionStep
        from app.models.audit_log import AuditLog
        from app.models.simulation import Simulation
        from app.models.agent_action import AgentAction
        from app.services.outreach_campaign_service import make_unsubscribe_token
        from utils.id_gen import generate_id

        for uid in ('MEMBERA01', 'MEMBERB01'):
            db.session.add(User(id=uid, email='%s@t.local' % uid.lower(), full_name='M ' + uid, password_hash='x',
                                email_verified=True, onboarding_completed_at=datetime.utcnow()))
        db.session.commit()

        def contact(owner, email):
            c = Contact(id=generate_id(), user_id=owner, first_name='Pat', last_name='Lee', email=email,
                        source='manual_entry', pipeline_stage='prospect')
            db.session.add(c)
            db.session.commit()
            return c

        def step_for(c):
            sim = Simulation(id=generate_id(), user_id=c.user_id, name='S', status='completed')
            db.session.add(sim)
            act = AgentAction(id=generate_id(), simulation_id=sim.id, layer_number=1,
                              action_type='cold_email_campaign', status='complete')
            db.session.add(act)
            st = ActionStep(id=generate_id(), agent_action_id=act.id, simulation_id=sim.id, step_number=2,
                            total_steps=3, action_type='cold_email_campaign', step_type='email_followup',
                            condition_ref=c.id, scheduled_for=datetime.utcnow() + timedelta(days=2))
            db.session.add(st)
            db.session.commit()
            return st

        fresh = lambda c: db.session.get(Contact, c.id)
        client = app.test_client()

        print('\n1. Unsubscribe link')
        a = contact('MEMBERA01', 'Pat@Example.com')
        b = contact('MEMBERB01', 'pat@example.com')
        other = contact('MEMBERA01', 'someone@example.com')
        st = step_for(a)
        r = client.get('/outreach/unsubscribe/%s' % make_unsubscribe_token('pat@example.com'))
        check('the unsubscribe page works', r.status_code == 200, r.status_code)
        check('the address is on the global opt-out list',
              EmailSuppression.query.filter_by(email='pat@example.com', reason='unsubscribe').count() == 1)
        check('Do Not Contact is set on that contact', fresh(a).do_not_contact is True)
        check("...and on every member's contact with that address (any letter case)", fresh(b).do_not_contact is True)
        check('other contacts are untouched', fresh(other).do_not_contact is False)
        check('the Email-to-CRM flag row is recorded', ContactDncFlag.query.filter_by(contact_id=a.id, reason='unsubscribe').count() == 1)
        check('scheduled follow-ups to them are cancelled',
              db.session.get(ActionStep, st.id).status == ActionStep.STATUS_CANCELLED
              and 'opted out' in (db.session.get(ActionStep, st.id).skip_reason or ''))
        check('the contact timeline says why', ContactActivity.query.filter_by(contact_id=a.id, activity_type='opted_out',
                                                                             notes='Unsubscribed (email link)').count() == 1)
        check('an audit row per contact', AuditLog.query.filter_by(action='outreach_opt_out').count() == 2)
        r = client.post('/outreach/unsubscribe/%s' % make_unsubscribe_token('pat@example.com'))
        check('one-click POST (mail apps) works and a repeat changes nothing', r.status_code == 200
              and EmailSuppression.query.filter_by(email='pat@example.com').count() == 1
              and ContactDncFlag.query.filter_by(contact_id=a.id).count() == 1
              and AuditLog.query.filter_by(action='outreach_opt_out').count() == 2)
        r = client.get('/outreach/unsubscribe/not-a-token')
        check('a bad token changes nothing', r.status_code == 400 and fresh(other).do_not_contact is False)
        late = contact('MEMBERA01', 'pat@example.com')
        check('a contact added later with that address arrives Do Not Contact', late.do_not_contact is True)

        print('\n2. Reply STOP')
        c1 = contact('MEMBERA01', 'stop@example.com')
        c2 = contact('MEMBERB01', 'stop@example.com')
        from app.blueprints.integrations.routes import _opt_out_by_reply
        _opt_out_by_reply(c1.id)
        check("replying STOP marks every contact with that address", fresh(c1).do_not_contact and fresh(c2).do_not_contact)
        check('...and records it as a reply', ContactActivity.query.filter_by(contact_id=c2.id, notes='Replied STOP').count() == 1)

        print('\n3. Spam report (SendGrid event)')
        s1 = contact('MEMBERA01', 'spam@example.com')
        r = client.post('/api/webhooks/sendgrid', json=[{'event': 'spamreport', 'email': 'Spam@Example.com'}])
        check('a spam report marks the contact Do Not Contact', r.status_code == 200 and fresh(s1).do_not_contact is True
              and EmailSuppression.query.filter_by(email='spam@example.com', reason='complaint').count() == 1, r.status_code)

        print('\n4. What the member sees')
        with client.session_transaction() as s:
            s['_user_id'] = 'MEMBERA01'
            s['_fresh'] = True
        g.pop('_login_user', None)
        rows = {x['id']: x for x in client.get('/api/contacts?per_page=200').get_json()['contacts']}
        check('/contacts shows them as Do Not Contact', rows[a.id].get('do_not_contact') is True
              and rows[a.id]['simisi_invite']['status'] == 'blocked', rows[a.id].get('do_not_contact'))
        mig = open('sql/066_optout_dnc_backfill.sql', encoding='utf-8').read()
        check('a backfill marks contacts who unsubscribed before this fix',
              'UPDATE contacts' in mig and "s.reason IN ('unsubscribe', 'complaint')" in mig)

    print('\n%d passed, %d failed' % (len(PASS), len(FAIL)))
    for label in FAIL:
        print('  FAILED: ' + label)
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
