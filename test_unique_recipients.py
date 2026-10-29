"""Recipients are unique per agent per simulation, across every cycle.

An agent may target a person in only one of its runs. Follow-up steps of that
run still reach them; a later run of the agent never does — even if the first
run's emails were held for approval and never sent. An agent with no unused
recipients left is left out of the cycle.

Runs on in-memory SQLite; no network calls, no email sent.

    python test_unique_recipients.py
"""
import json
import os
import sys

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
    app.config['SENDGRID_API_KEY'] = None      # guard runs first; nothing is sent

    with app.app_context():
        db.create_all()
        from datetime import datetime, timedelta
        from app.models.user import User
        from app.models.simulation import Simulation
        from app.models.agent_action import AgentAction
        from app.models.agent_recipient import AgentRecipient
        from app.models.contact import Contact
        from app.models.layer6 import Layer6Config
        from app.models.outreach_email import EmailLog
        from app.models.action_step import ActionStep
        from app.models.prospect_research import Prospect
        from app.services import recipient_ledger as ledger
        from app.services import claude, layer6
        from app.services.action_step_service import create_steps_from_artifact
        from app.services.outreach_email_service import send_outreach_email
        from app.services.consulting_outreach_service import _filter_uncontacted

        user = User(id=generate_id(), email='owner@example.com', full_name='Owner',
                    password_hash='x')
        sim = Simulation(id=generate_id(), user_id=user.id, name='Sim',
                         expertise_zone='Operations Consulting', status='complete')
        db.session.add_all([user, sim])
        db.session.flush()
        config = Layer6Config(id=generate_id(), simulation_id=sim.id)
        db.session.add(config)

        def contact(first, email, stage='prospect'):
            c = Contact(id=generate_id(), user_id=user.id, first_name=first, last_name='X',
                        email=email, company_name=first + ' Co', pipeline_stage=stage,
                        source='manual')
            db.session.add(c)
            db.session.commit()
            return c

        a, b, c = (contact('Ann', 'ann@a.com'), contact('Bob', 'bob@b.com'),
                   contact('Cal', 'cal@c.com'))

        def run(agent, prospects, when=None):
            """A completed run of `agent` whose artifact names `prospects`."""
            act = AgentAction(id=generate_id(), simulation_id=sim.id, layer_number=1,
                              action_type=agent, status='complete',
                              completed_at=when or datetime.utcnow(),
                              created_at=when or datetime.utcnow())
            act.artifact = json.dumps({'prospects': [
                {'first_name': p.first_name, 'last_name': 'X', 'email': p.email,
                 'crm_contact_id': p.id,
                 'sequence': [{'step': 1, 'subject': 'Hi', 'body': 'Hello'}]}
                for p in prospects]})
            db.session.add(act)
            db.session.commit()
            ledger.record_from_artifact(sim.id, agent, act.id, act.artifact)
            return act

        def crm_names():
            text = claude._get_crm_prospect_section(user.id, sim.id, {}, 5)
            return {n for n in ('Ann', 'Bob', 'Cal', 'Dee') if n in text}

        # ── 1. Cold email never re-drafts a person, even unsent ────────────
        print('\n1. Cold Email recipients across cycles')
        check('cycle 1 sees the whole CRM pool', crm_names() == {'Ann', 'Bob', 'Cal'}, crm_names())
        r1 = run('cold_email_campaign', [a, b])
        check('cycle 2 excludes people drafted in cycle 1 (never sent)',
              crm_names() == {'Cal'}, crm_names())
        r2 = run('cold_email_campaign', [c])
        check('cycle 3 finds no one left', crm_names() == set(), crm_names())

        prospects = [Prospect(first_name='Ann', last_name='X', email='ANN@a.com'),
                     Prospect(first_name='Eve', last_name='Y', email='eve@e.com')]
        kept = ledger.filter_unused(sim.id, 'cold_email_campaign', prospects)
        check('research results drop earlier recipients (email case-insensitive)',
              [p.email for p in kept] == ['eve@e.com'])
        check('uniqueness is per agent — consulting_outreach may still reach Ann',
              'ann@a.com' not in ledger.used_keys(sim.id, 'consulting_outreach'))

        # ── 2. Eligibility ─────────────────────────────────────────────────
        print('\n2. Leaving exhausted agents out of the cycle')
        ok, _ = ledger.has_unused_recipients(sim.id, 'cold_email_campaign', user.id)
        check('still eligible while the last run found someone new', ok)
        r3 = run('cold_email_campaign', [])
        ok, why = ledger.has_unused_recipients(sim.id, 'cold_email_campaign', user.id)
        check('exhausted: last run found no one and CRM pool used up', not ok, why)
        eligible = {e['action_type'] for e in layer6.build_eligible_actions(
            sim.id, config, {'rate_card', 'booking_page'}, phase='explore')}
        check('orchestrator leaves cold_email_campaign out of the cycle',
              'cold_email_campaign' not in eligible)
        check('agents without recipients are unaffected', 'linkedin_optimization' in eligible)
        r3.completed_at = datetime.utcnow() - timedelta(days=ledger.RECHECK_DAYS + 1)
        db.session.commit()
        check('re-probed after %d days' % ledger.RECHECK_DAYS,
              ledger.has_unused_recipients(sim.id, 'cold_email_campaign', user.id)[0])
        r3.completed_at = datetime.utcnow()
        db.session.commit()
        d = contact('Dee', 'dee@d.com')
        check('a new CRM contact makes the agent eligible again',
              ledger.has_unused_recipients(sim.id, 'cold_email_campaign', user.id)[0])

        pat = contact('Pat', 'pat@p.com', stage='client')
        check('update-only agent eligible while a past client is untargeted',
              ledger.has_unused_recipients(sim.id, 'client_winback', user.id)[0])
        ledger.record(sim.id, 'client_winback', 'w1', [{'email': 'pat@p.com'}])
        check('update-only agent left out once every client was targeted',
              not ledger.has_unused_recipients(sim.id, 'client_winback', user.id)[0])

        # ── 3. Send-time guard ─────────────────────────────────────────────
        print('\n3. Send-time guard')
        res = send_outreach_email(sim.id, a.id, 'Hi again', '<p>x</p>', 'me@x.com', 'Me',
                                  action_id=r2.id)
        check('a later run cannot email someone an earlier run claimed',
              res.get('reason') == 'already_targeted_by_agent', res)
        res = send_outreach_email(sim.id, a.id, 'Re: Hi', '<p>x</p>', 'me@x.com', 'Me',
                                  action_id=r1.id)
        check('the claiming run can still send its follow-ups',
              res.get('reason') == 'no_api_key', res)
        db.session.add(EmailLog(id=generate_id(), simulation_id=sim.id, contact_id=a.id,
                                action_id=r1.id, subject='Hi', from_email='me@x.com',
                                from_name='Me', to_email='ann@a.com', status='sent'))
        db.session.commit()
        res = send_outreach_email(sim.id, a.id, 'Hi', '<p>x</p>', 'me@x.com', 'Me',
                                  action_id=r1.id)
        check('the same email twice from one run is blocked (hook + Step 1)',
              res.get('reason') == 'duplicate_send', res)

        # ── 4. Steps ───────────────────────────────────────────────────────
        print('\n4. Scheduled steps')
        stale = AgentAction(id=generate_id(), simulation_id=sim.id, layer_number=1,
                            action_type='cold_email_campaign', status='complete',
                            completed_at=datetime.utcnow())
        stale.artifact = json.dumps({'prospects': [
            {'first_name': 'Bob', 'last_name': 'X', 'email': 'bob@b.com', 'crm_contact_id': b.id,
             'sequence': [{'step': 1, 'subject': 'S', 'body': 'B'}] * 3},
            {'first_name': 'Dee', 'last_name': 'X', 'email': 'dee@d.com', 'crm_contact_id': d.id,
             'sequence': [{'step': 1, 'subject': 'S', 'body': 'B'}] * 3}]})
        db.session.add(stale)
        db.session.commit()
        create_steps_from_artifact(stale.id, sim.id, 'cold_email_campaign', stale.artifact)
        targets = {s.payload.get('contact_id') for s in ActionStep.query.filter_by(agent_action_id=stale.id)}
        check('no steps for someone an earlier run owns; steps for new people',
              b.id not in targets and d.id in targets, targets)

        # ── 5. Other agents ────────────────────────────────────────────────
        print('\n5. Same rule for other agents')
        claude._save_contacts_list([{'first_name': 'Gil', 'last_name': 'Z', 'email': 'gil@g.com'}],
                                   user.id, sim.id, 'referral_network', False, 'test', action_id='rn1')
        check('markdown agents claim their contacts',
              'gil@g.com' in ledger.used_keys(sim.id, 'referral_network'))
        again = ledger.filter_unused(sim.id, 'referral_network',
                                     [{'first_name': 'Gil', 'last_name': 'Z', 'email': 'gil@g.com'},
                                      {'first_name': 'Hal', 'last_name': 'Q', 'company_name': 'Q Ltd'}])
        check('…and later runs drop them; contacts without email dedupe by name+company',
              [x['first_name'] for x in again] == ['Hal'])
        ledger.record(sim.id, 'referral_network', 'rn2', again)
        check('name-keyed contact recorded', any(k.startswith('name:hal q|q ltd')
                                                 for k in ledger.used_keys(sim.id, 'referral_network')))
        ledger.record(sim.id, 'consulting_outreach', 'co1', [{'email': 'cal@c.com'}])
        kept = _filter_uncontacted(user.id, [Prospect(first_name='Cal', last_name='X', email='cal@c.com'),
                                             Prospect(first_name='Ivy', last_name='X', email='ivy@i.com')],
                                   simulation_id=sim.id)
        check('consulting_outreach research filter drops its earlier recipients',
              [p.email for p in kept] == ['ivy@i.com'])

        # ── 6. Backfill for simulations that ran before the ledger ─────────
        print('\n6. Backfill')
        sim2 = Simulation(id=generate_id(), user_id=user.id, name='Legacy',
                          expertise_zone='Ops', status='complete')
        db.session.add(sim2)
        old = AgentAction(id=generate_id(), simulation_id=sim2.id, layer_number=1,
                          action_type='cold_email_campaign', status='complete',
                          completed_at=datetime.utcnow())
        db.session.add(old)
        db.session.flush()
        db.session.add(EmailLog(id=generate_id(), simulation_id=sim2.id, contact_id=b.id,
                                action_id=old.id, subject='Old', from_email='me@x.com',
                                from_name='Me', to_email='bob@b.com', status='sent'))
        db.session.commit()
        check('history (sent emails) seeds the ledger on first read',
              'bob@b.com' in ledger.used_keys(sim2.id, 'cold_email_campaign'))

        # ── 7. Admin cycle delete ──────────────────────────────────────────
        print('\n7. Admin cycle delete')
        from app.services.cycle_admin_service import delete_cycles
        ledger.record(sim.id, 'cold_email_campaign', r1.id, [{'email': 'zed@z.com'}])
        res = delete_cycles(sim.id, include_orphans=True)
        check('delete succeeds', res.get('ok'), res.get('message'))
        rows = {r.recipient_key: r.action_id for r in
                AgentRecipient.query.filter_by(simulation_id=sim.id, action_type='cold_email_campaign')}
        check('emailed recipient stays claimed (unlinked)', rows.get('ann@a.com', 'gone') is None, rows)
        check('drafted-only recipients are released', 'bob@b.com' not in rows and 'zed@z.com' not in rows, rows)
        res = send_outreach_email(sim.id, a.id, 'New', '<p>x</p>', 'me@x.com', 'Me',
                                  action_id=(post_delete_run := run('cold_email_campaign', [d])).id)
        check('a new run still cannot email the person a deleted run emailed',
              res.get('reason') == 'already_targeted_by_agent', res)

        # ── 8a. Research: skips earlier recipients, respects its time budget ─
        print('\n8a. Prospect research budget and exclusion')
        import time as _time
        from app.services.prospect_research_engine import (
            ProspectResearchEngine, build_targeting_criteria)
        calls = []

        def slow_search(query):
            calls.append(query)
            _time.sleep(0.4)
            return 'results'

        def extract(text, source, targeting):
            n = len(calls)
            return [Prospect(first_name='Ann', last_name='X', email='ann@a.com', company_name='Ann Co'),
                    Prospect(first_name='New%d' % n, last_name='P', company_name='Co%d' % n)]

        eng = ProspectResearchEngine()
        eng._claude_web_search = slow_search
        eng._extract_prospects_from_results = extract
        targeting = build_targeting_criteria('cold_email_campaign', 'Operations Consulting', {})
        t0 = _time.time()
        res = eng.research(user_id=user.id, simulation_id=sim.id, action_id='x1',
                           targeting=targeting, target_count=50,
                           exclude_keys={'ann@a.com'}, time_budget_s=1.0)
        took = _time.time() - t0
        check('research stops at its time budget instead of running every query',
              took < 3.0 and len(calls) <= 4, '%.1fs, %d queries' % (took, len(calls)))
        check('already-targeted people are skipped during the search',
              all(p.email != 'ann@a.com' for p in res.prospects) and res.prospects,
              [p.first_name for p in res.prospects])
        check('stage timings recorded for the run trace',
              'web_search' in eng.stage_seconds, eng.stage_seconds)

        # ── 8. Deployed before sql/054 ran ─────────────────────────────────
        print('\n8. Missing ledger table degrades, never breaks a cycle')
        AgentRecipient.__table__.drop(db.engine)
        ledger._backfilled.clear()
        check('CRM pool still returned (old behaviour)', 'Dee' in crm_names(), crm_names())
        cfg = Layer6Config.query.filter_by(simulation_id=sim.id).first()
        try:
            got = {e['action_type'] for e in layer6.build_eligible_actions(
                sim.id, cfg, {'rate_card', 'booking_page'}, phase='explore')}
            ok = 'cold_email_campaign' in got
        except Exception as exc:
            ok, got = False, exc
        check('eligibility fails open instead of aborting the cycle', ok, got)
        res = send_outreach_email(sim.id, Contact.query.filter_by(email='dee@d.com').first().id,
                                  'Hi', '<p>x</p>', 'me@x.com', 'Me',
                                  action_id=post_delete_run.id)
        check('send path fails open', res.get('reason') == 'no_api_key', res)

    print('\n%d passed, %d failed' % (len(PASS), len(FAIL)))
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
