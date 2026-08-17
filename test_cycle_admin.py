"""Admin run-cycle reset / delete — behavioural checks.

This feature is destructive and irreversible, so the tests are about the boundary:
what gets deleted, what survives, and what refuses to run. Against in-memory
SQLite; no network, no real data.

    python test_cycle_admin.py
"""
import os
import sys
from datetime import datetime, timedelta

os.environ.setdefault('SECRET_KEY', 'test-only')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PASS, FAIL = [], []


def check(label, condition, detail=''):
    (PASS if condition else FAIL).append(label)
    line = '  {} {}{}'.format('ok  ' if condition else 'FAIL', label,
                              ('  -- ' + str(detail)) if detail else '')
    enc = sys.stdout.encoding or 'ascii'
    print(line.encode(enc, 'replace').decode(enc))


def main():
    from app import create_app
    from app.extensions import db
    from utils.id_gen import generate_id

    app = create_app('testing')
    app.config.update(SQLALCHEMY_DATABASE_URI='sqlite:///:memory:')

    with app.app_context():
        # Every model must be imported BEFORE create_all(), or its table is absent
        # from the metadata and never created. Several of these (kajabi,
        # prospect_research, social_queue, bayesian) are not registered by
        # models/__init__.py, which is exactly why the service tolerates missing
        # tables — but here we want them present so those paths get exercised.
        from app.models import kajabi, prospect_research, social_queue  # noqa: F401
        from app.models.action_step import ActionStep
        from app.models.agent_action import AgentAction
        from app.models.artifact import ArtifactDependency, ArtifactVersion
        from app.models.bayesian import BayesianPosterior
        from app.models.calibration import CalibrationRun, OutcomeReport
        from app.models.contact import Contact, ContactActivity
        from app.models.income import LayerIncomeRecord
        from app.models.kajabi import KajabiProduct
        from app.models.layer6 import (
            ActionItem, CyclePosteriorSnapshot, Layer6ActionQueue, Layer6Config,
            Layer6Cycle, Layer6ExecutionLog, Layer6Momentum, Layer6Outcome,
        )
        from app.models.outreach_email import EmailLog
        from app.models.prospect_research import ProspectResearchRun
        from app.models.published_page import PublishedPage
        from app.models.signing import SigningDocument
        from app.models.simulation import Simulation
        from app.models.social_queue import SocialPostQueue
        from app.models.user import User
        from app.services import cycle_admin_service as cyc

        db.create_all()

        user = User(id=generate_id(), email='u@t.local', full_name='U', password_hash='x')
        db.session.add(user)
        sim = Simulation(id=generate_id(), user_id=user.id, name='Sim',
                         status='complete', lifecycle_phase='dormant')
        sim.selected_agents = ['rate_card', 'cold_email_campaign']
        sim.cohort_json = {'soc_group': '13-1111'}
        db.session.add(sim)
        db.session.add(Layer6Config(id=generate_id(), simulation_id=sim.id))
        db.session.flush()

        # Two completed cycles + one still running.
        cycles = []
        for n, done in ((1, True), (2, True), (3, False)):
            c = Layer6Cycle(
                id=generate_id(), simulation_id=sim.id, cycle_number=n,
                cycle_started_at=datetime.utcnow() - timedelta(days=10 - n),
                cycle_completed_at=(datetime.utcnow() if done else None),
            )
            db.session.add(c)
            cycles.append(c)
        db.session.flush()

        def make_action(cycle, atype):
            """One orchestrator-dispatched action with a full set of children."""
            a = AgentAction(id=generate_id(), simulation_id=sim.id, layer_number=1,
                           action_type=atype, status='complete')
            db.session.add(a)
            db.session.flush()
            q = Layer6ActionQueue(
                id=generate_id(), simulation_id=sim.id, cycle_id=cycle.id,
                source_layer=1, action_type=atype, agent_action_id=a.id,
                status='complete')
            db.session.add(q)
            db.session.add(ArtifactVersion(
                id=generate_id(), action_id=a.id, simulation_id=sim.id,
                layer_number=1, action_type=atype, version_number=1,
                version_label='v1', content='x', file_type='text', is_current=True,
                created_by='user'))
            db.session.add(ArtifactDependency(
                id=generate_id(), simulation_id=sim.id, upstream_action_id=a.id,
                upstream_action_type=atype, downstream_action_type='other'))
            db.session.add(ActionStep(
                id=generate_id(), agent_action_id=a.id, parent_action_id=q.id,
                simulation_id=sim.id, step_number=1, total_steps=2,
                action_type=atype, step_type='email',
                scheduled_for=datetime.utcnow()))
            db.session.add(CalibrationRun(
                id=generate_id(), simulation_id=sim.id, action_id=a.id,
                agent_key=atype, output_field='f', field_label='F',
                raw_value=1, cal_low=1, cal_mid=2, cal_high=3,
                confidence_tier='moderate'))
            db.session.add(ActionItem(
                id=generate_id(), simulation_id=sim.id, user_id=user.id,
                item_type='escalation', urgency_tier='today', title='t',
                action_label='Open', action_url='/x', source_action_id=a.id))
            db.session.add(ProspectResearchRun(
                id=generate_id(), simulation_id=sim.id, user_id=user.id,
                action_id=a.id, calling_agent=atype,
                targeting_criteria='{}', sources_used='[]'))
            db.session.add(Layer6ExecutionLog(
                id=generate_id(), simulation_id=sim.id, cycle_id=cycle.id,
                action_id=q.id, event_type='dispatched'))
            db.session.add(CyclePosteriorSnapshot(
                id=generate_id(), cycle_id=cycle.id, simulation_id=sim.id,
                action_type=atype, posterior_value=0.5))
            return a, q

        a1, q1 = make_action(cycles[0], 'rate_card')
        a2, q2 = make_action(cycles[1], 'cold_email_campaign')
        a3, q3 = make_action(cycles[2], 'referral_network')

        # A user-triggered run: no queue entry, so it belongs to no cycle.
        user_action = AgentAction(id=generate_id(), simulation_id=sim.id,
                                 layer_number=1, action_type='booking_page',
                                 status='complete', created_by=user.id)
        db.session.add(user_action)
        db.session.flush()
        db.session.add(ArtifactVersion(
            id=generate_id(), action_id=user_action.id, simulation_id=sim.id,
            layer_number=1, action_type='booking_page', version_number=1,
            version_label='v1', content='y', file_type='text', is_current=True,
            created_by='user'))

        # ── Tier 3: real-world records reachable from cycle 1's action ───────
        contact = Contact(id=generate_id(), user_id=user.id, first_name='Real',
                          last_name='Person', email='real@person.com',
                          pipeline_stage='active', source='agent_action',
                          source_action_id=a1.id, source_cycle_id=cycles[0].id,
                          outreach_count=3, last_contacted_at=datetime.utcnow())
        db.session.add(contact)
        db.session.flush()
        db.session.add(ContactActivity(
            id=generate_id(), contact_id=contact.id, simulation_id=sim.id,
            action_id=a1.id, activity_type='outreach_sent', created_by='orchestrator'))
        db.session.add(EmailLog(
            id=generate_id(), simulation_id=sim.id, action_id=a1.id,
            contact_id=contact.id, subject='s', from_email='me@x.com',
            from_name='Me', to_email='real@person.com', status='sent'))
        db.session.add(SigningDocument(
            id=generate_id(), user_id=user.id, simulation_id=sim.id,
            action_id=a1.id, action_type='rate_card',
            pandadoc_document_id='pd1', recipient_email='real@person.com',
            status='completed'))
        db.session.add(PublishedPage(
            id=generate_id(), user_id=user.id, simulation_id=sim.id,
            action_id=a1.id, slug='live-page', action_type='sales_page',
            html_content='<p>live</p>'))
        db.session.add(LayerIncomeRecord(
            id=generate_id(), simulation_id=sim.id, layer_number=1,
            action_id=a1.id, amount=8400, recorded_by=user.id))
        db.session.add(KajabiProduct(
            id=generate_id(), user_id=user.id, simulation_id=sim.id,
            action_id=a1.id, product_type='course', name='Course'))
        db.session.add(SocialPostQueue(
            id=generate_id(), user_id=user.id, platform='linkedin',
            simulation_id=sim.id, artifact_id=a1.id, post_text='posted',
            platform_post_id='li-123'))          # already live
        db.session.add(SocialPostQueue(
            id=generate_id(), user_id=user.id, platform='linkedin',
            simulation_id=sim.id, artifact_id=a1.id, post_text='pending'))
        db.session.flush()
        run1 = CalibrationRun.query.filter_by(action_id=a1.id).first()
        db.session.add(OutcomeReport(
            id=generate_id(), user_id=user.id, simulation_id=sim.id,
            run_id=run1.id, agent_key='rate_card', output_field='f',
            reported_value=1234, is_verified=True))

        # Orchestrator learned state
        db.session.add(BayesianPosterior(
            id=generate_id(), simulation_id=sim.id, posterior_key='k', value=0.7))
        from datetime import date as _date
        db.session.add(Layer6Momentum(
            id=generate_id(), simulation_id=sim.id, snapshot_date=_date.today()))
        db.session.add(Layer6Outcome(
            id=generate_id(), simulation_id=sim.id, layer_number=1,
            reporting_month='2026-08'))
        db.session.commit()

        c1_id, c2_id, c3_id = cycles[0].id, cycles[1].id, cycles[2].id
        sim_id, a1_id, ua_id = sim.id, a1.id, user_action.id
        contact_id = contact.id

        # ── Scope resolution ────────────────────────────────────────────────
        print('\nscope')
        sc = cyc.resolve_scope(sim_id, [c1_id])
        check('one cycle resolves one action', sc['action_ids'] == [a1_id], sc['action_ids'])
        check('user-run action is not in any cycle scope',
              ua_id not in cyc.resolve_scope(sim_id)['action_ids'])
        check('orphan detection finds exactly the user run',
              cyc.orphan_action_ids(sim_id) == [ua_id])

        # ── Preview is non-destructive ──────────────────────────────────────
        print('\npreview (dry run)')
        p = cyc.preview(sim_id, [c1_id])
        check('preview counts the cycle', p['cycles_in_scope'] == 1)
        check('preview counts artifacts to delete',
              p['delete'].get('artifact_versions') == 1, p['delete'])
        check('preview lists preserved email logs',
              p['preserve'].get('email_logs') == 1, p['preserve'])
        check('preview surfaces the preserved income total',
              p['preserved_income_usd'] == 8400.0, p['preserved_income_usd'])
        check('preview deleted nothing', Layer6Cycle.query.count() == 3)

        # ── Running-cycle guard ─────────────────────────────────────────────
        print('\nrunning-cycle guard')
        r = cyc.delete_cycles(sim_id, [c3_id])
        check('refuses to delete a mid-flight cycle',
              r['ok'] is False and r['error'] == 'cycle_running', r.get('message'))
        check('nothing was deleted by the refusal', Layer6Cycle.query.count() == 3)
        r = cyc.delete_cycles(sim_id, [c3_id], force_running=True)
        check('force=True deletes it', r['ok'] is True, r.get('message'))
        check('cycle 3 gone', Layer6Cycle.query.get(c3_id) is None)

        # ── Delete one cycle ────────────────────────────────────────────────
        print('\ndelete cycle 1 — tier 1 + 2 removed')
        r = cyc.delete_cycles(sim_id, [c1_id], admin_user_id='admin1234')
        check('delete succeeded', r['ok'] is True, r.get('message'))
        check('cycle row gone', Layer6Cycle.query.get(c1_id) is None)
        check('queue entries gone', Layer6ActionQueue.query.filter_by(cycle_id=c1_id).count() == 0)
        check('execution log gone', Layer6ExecutionLog.query.filter_by(cycle_id=c1_id).count() == 0)
        check('posterior snapshots gone',
              CyclePosteriorSnapshot.query.filter_by(cycle_id=c1_id).count() == 0)
        check('agent action gone', AgentAction.query.get(a1_id) is None)
        check('artifact versions gone',
              ArtifactVersion.query.filter_by(action_id=a1_id).count() == 0)
        check('artifact dependencies gone',
              ArtifactDependency.query.filter_by(upstream_action_id=a1_id).count() == 0)
        check('action steps gone', ActionStep.query.filter_by(agent_action_id=a1_id).count() == 0)
        check('calibration runs gone',
              CalibrationRun.query.filter_by(action_id=a1_id).count() == 0)
        check('action items gone', ActionItem.query.filter_by(source_action_id=a1_id).count() == 0)
        check('prospect research gone',
              ProspectResearchRun.query.filter_by(action_id=a1_id).count() == 0)
        check('contact activity gone',
              ContactActivity.query.filter_by(action_id=a1_id).count() == 0)
        check('pending social post gone',
              SocialPostQueue.query.filter_by(platform_post_id=None).count() == 0)

        print('\ndelete cycle 1 — tier 3 preserved and unlinked')
        el = EmailLog.query.first()
        check('email log kept', el is not None)
        check('email log unlinked', el is not None and el.action_id is None, el.action_id if el else None)
        sd = SigningDocument.query.first()
        check('signed document kept and unlinked', sd is not None and sd.action_id is None)
        pp = PublishedPage.query.first()
        check('published page kept and unlinked', pp is not None and pp.action_id is None)
        ir = LayerIncomeRecord.query.first()
        check('income record kept', ir is not None and float(ir.amount) == 8400.0)
        check('income record unlinked', ir is not None and ir.action_id is None)
        kp = KajabiProduct.query.first()
        check('kajabi product kept and unlinked', kp is not None and kp.action_id is None)
        live = SocialPostQueue.query.filter(SocialPostQueue.platform_post_id.isnot(None)).first()
        check('live social post kept and unlinked',
              live is not None and live.artifact_id is None)
        orep = OutcomeReport.query.first()
        check('outcome report kept and unlinked from its run',
              orep is not None and orep.run_id is None)

        print('\ncontacts are never deleted')
        ct = Contact.query.get(contact_id)
        check('contact still exists', ct is not None)
        check('contact provenance cleared', ct.source_action_id is None and ct.source_cycle_id is None)
        check('outreach_count preserved (stops re-emailing)', ct.outreach_count == 3,
              ct.outreach_count)
        check('last_contacted_at preserved', ct.last_contacted_at is not None)

        print('\nuser-triggered runs survive a cycle delete')
        check('user action still exists', AgentAction.query.get(ua_id) is not None)
        check('its artifact still exists',
              ArtifactVersion.query.filter_by(action_id=ua_id).count() == 1)
        check('cycle 2 untouched', Layer6Cycle.query.get(c2_id) is not None)

        print('\naudit trail')
        from app.models.audit_log import AuditLog
        entries = AuditLog.query.filter_by(action='admin_cycles_deleted').all()
        check('deletion was audit-logged', len(entries) >= 1, len(entries))
        check('audit records the admin', any(e.user_id == 'admin1234' for e in entries))
        check('audit records preserved counts',
              any(e.extra.get('rows_preserved') for e in entries))

        # ── Reset ───────────────────────────────────────────────────────────
        print('\nreset')
        r = cyc.reset_simulation(sim_id, admin_user_id='admin1234')
        check('reset succeeded', r['ok'] is True, r.get('message'))
        check('all cycles gone', Layer6Cycle.query.filter_by(simulation_id=sim_id).count() == 0)
        check('every agent action gone, including the user run',
              AgentAction.query.filter_by(simulation_id=sim_id).count() == 0)
        check('bayesian posteriors cleared',
              BayesianPosterior.query.filter_by(simulation_id=sim_id).count() == 0)
        check('momentum cleared', Layer6Momentum.query.filter_by(simulation_id=sim_id).count() == 0)
        check('outcomes cleared', Layer6Outcome.query.filter_by(simulation_id=sim_id).count() == 0)

        fresh = Simulation.query.get(sim_id)
        check('lifecycle returned to active (was dormant)',
              fresh.lifecycle_phase == 'active', fresh.lifecycle_phase)
        check('agent selection KEPT', fresh.selected_agents == ['rate_card', 'cold_email_campaign'])
        check('calibration cohort KEPT', fresh.cohort_json == {'soc_group': '13-1111'})
        check('layer6 config KEPT',
              Layer6Config.query.filter_by(simulation_id=sim_id).count() == 1)
        check('contact STILL not deleted by a reset', Contact.query.get(contact_id) is not None)
        check('income record still preserved through reset',
              LayerIncomeRecord.query.count() == 1)
        check('reset was audit-logged',
              AuditLog.query.filter_by(action='admin_simulation_reset').count() == 1)

        print('\nidempotence')
        r = cyc.reset_simulation(sim_id, admin_user_id='admin1234')
        check('resetting an already-clean simulation is a no-op, not an error',
              r['ok'] is True, r.get('message'))
        check('deleting an unknown cycle is a safe no-op',
              cyc.delete_cycles(sim_id, ['zzzzzzzzz'])['ok'] is True)

    print('\n' + '=' * 62)
    print('{} passed, {} failed'.format(len(PASS), len(FAIL)))
    for f in FAIL:
        print('  FAILED: ' + f)
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
