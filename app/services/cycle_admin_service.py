"""Admin reset / delete of a user's orchestrator run cycles.

A "run cycle" is a Layer6Cycle: one orchestrator execution that scores actions,
dispatches some of them, and produces artifacts. Deleting one has to reach a long
way, because everything the cycle caused hangs off the agent actions it dispatched.

The blast radius is deliberately split into three tiers, and the split is the whole
point of this module:

  Tier 1  the cycle machinery itself — layer6_cycles, layer6_action_queue,
          layer6_execution_log, cycle_posterior_snapshots, layer6_share_tokens.
          Hard deleted.

  Tier 2  what the cycle generated — agent_actions and everything keyed to them:
          artifact_versions, artifact_dependencies, action_steps, calibration_runs,
          prospect_research_runs, action_items, contact_activities, unposted social
          queue entries. Hard deleted. This is "all activities for the run cycle".

  Tier 3  records of things that already happened in the real world, reachable from
          the same action ids — sent email, executed documents, published pages,
          reported income, created Kajabi products, live social posts, the user's own
          calibration outcome reports. These are PRESERVED and unlinked (action_id
          set to NULL). Deleting an email_logs row does not unsend the email; it
          only destroys the evidence that it was sent, which is exactly what you
          need when a recipient asks why they were contacted.

Contacts are never deleted. Beyond being real people, contact.outreach_count and
last_contacted_at are what the cold-email guard reads to avoid re-contacting
someone (see _dispatch_cold_email_campaign). Deleting a contact makes the next
cycle re-source and re-email them, which reads as spam to the recipient.

Only orchestrator-dispatched actions belong to a cycle. A user-triggered re-run has
no queue entry, so deleting a cycle leaves it alone; only a full reset clears those.
"""
from __future__ import annotations

import logging
from datetime import datetime

logger = logging.getLogger(__name__)

# Tier 3: (model attribute path, column to null). Preserved, never deleted.
# Expressed as a table/column pair rather than ORM objects because several of these
# models are not imported in models/__init__.py and are resolved lazily below.
_TIER3_UNLINK = (
    ('email_logs', 'action_id'),
    ('signing_documents', 'action_id'),
    ('published_pages', 'action_id'),
    ('layer_income_records', 'action_id'),
    ('kajabi_products', 'action_id'),
    ('email_campaigns', 'action_id'),
    ('integration_activity_log', 'action_id'),
    ('advisor_flags', 'action_id'),
    ('outcome_reports', 'run_id'),          # handled separately: keyed via calibration_runs
)


def _chunk(items, size=500):
    """MySQL chokes on very large IN () lists; batch them."""
    items = list(items)
    for i in range(0, len(items), size):
        yield items[i:i + size]


def _existing_tables() -> set:
    """Table names present in this database, resolved once per operation.

    Several tables (kajabi_products, social_post_queue, prospect_research_runs) come
    from models that models/__init__.py does not import, so they can be absent on a
    given database while existing in production. Skipping a missing table is
    correct; failing on one would abort a legitimate delete.

    Inspection runs over the SESSION's connection, never db.engine. Opening and
    closing a separate pooled connection mid-transaction returns it to the pool with
    a ROLLBACK — and under SQLite's SingletonThreadPool that is the *same* physical
    connection the session is using, so it silently undoes every delete performed so
    far. Resolving once, up front, over the session's own connection avoids both the
    correctness trap and a per-table round-trip.
    """
    from sqlalchemy import inspect
    from app.extensions import db
    try:
        return set(inspect(db.session.connection()).get_table_names())
    except Exception as exc:
        logger.warning('Could not inspect tables, assuming all present: %s', exc)
        return set()


def _exec(sql: str, params: dict) -> int:
    """Run one statement, returning affected rowcount."""
    from sqlalchemy import text
    from app.extensions import db
    result = db.session.execute(text(sql), params)
    return result.rowcount or 0


# ---------------------------------------------------------------------------
# Scope resolution
# ---------------------------------------------------------------------------

def resolve_scope(simulation_id: str, cycle_ids: list = None) -> dict:
    """Work out exactly which cycles, queue entries and agent actions are in scope.

    cycle_ids=None means every cycle for the simulation.
    """
    from app.models.layer6 import Layer6ActionQueue, Layer6Cycle

    q = Layer6Cycle.query.filter_by(simulation_id=simulation_id)
    if cycle_ids:
        q = q.filter(Layer6Cycle.id.in_(list(cycle_ids)))
    cycles = q.order_by(Layer6Cycle.cycle_number).all()
    resolved_cycle_ids = [c.id for c in cycles]

    queue_ids, action_ids = [], []
    if resolved_cycle_ids:
        rows = Layer6ActionQueue.query.filter(
            Layer6ActionQueue.cycle_id.in_(resolved_cycle_ids),
        ).all()
        queue_ids = [r.id for r in rows]
        action_ids = [r.agent_action_id for r in rows if r.agent_action_id]

    return {
        'simulation_id': simulation_id,
        'cycles': cycles,
        'cycle_ids': resolved_cycle_ids,
        'queue_ids': queue_ids,
        'action_ids': sorted(set(action_ids)),
    }


def orphan_action_ids(simulation_id: str) -> list:
    """Agent actions for this simulation with no orchestrator queue entry.

    These are user-triggered runs. They survive a cycle delete and are only cleared
    by a full reset, where "start over" means no artifacts left standing.
    """
    from app.models.agent_action import AgentAction
    from app.models.layer6 import Layer6ActionQueue

    linked = {
        r.agent_action_id for r in Layer6ActionQueue.query.filter_by(
            simulation_id=simulation_id,
        ).all() if r.agent_action_id
    }
    all_ids = {a.id for a in AgentAction.query.filter_by(
        simulation_id=simulation_id,
    ).with_entities(AgentAction.id).all()}
    return sorted(all_ids - linked)


# ---------------------------------------------------------------------------
# Preview (dry run)
# ---------------------------------------------------------------------------

def preview(simulation_id: str, cycle_ids: list = None,
            include_orphans: bool = False) -> dict:
    """Count everything a delete would touch, without touching it.

    Returns separate 'delete' and 'preserve' maps so the confirmation dialog can
    state plainly what survives — the difference between the two is the whole
    safety story of this feature.
    """
    from sqlalchemy import text
    from app.extensions import db

    scope = resolve_scope(simulation_id, cycle_ids)
    cids, qids, aids = scope['cycle_ids'], scope['queue_ids'], list(scope['action_ids'])

    if include_orphans:
        aids = sorted(set(aids) | set(orphan_action_ids(simulation_id)))

    tables = _existing_tables()

    def count(table, column, values):
        if not values or (tables and table not in tables):
            return 0
        total = 0
        for batch in _chunk(values):
            keys = {'v%d' % i: v for i, v in enumerate(batch)}
            placeholders = ', '.join(':' + k for k in keys)
            row = db.session.execute(
                text('SELECT COUNT(*) FROM {} WHERE {} IN ({})'.format(
                    table, column, placeholders)), keys,
            ).scalar()
            total += int(row or 0)
        return total

    delete_counts = {
        'layer6_cycles': len(cids),
        'layer6_action_queue': len(qids),
        'layer6_execution_log': count('layer6_execution_log', 'cycle_id', cids),
        'cycle_posterior_snapshots': count('cycle_posterior_snapshots', 'cycle_id', cids),
        'layer6_share_tokens': count('layer6_share_tokens', 'cycle_id', cids),
        'agent_actions': len(aids),
        'artifact_versions': count('artifact_versions', 'action_id', aids),
        'artifact_dependencies': count('artifact_dependencies', 'upstream_action_id', aids),
        'action_steps': count('action_steps', 'agent_action_id', aids),
        'calibration_runs': count('calibration_runs', 'action_id', aids),
        'prospect_research_runs': count('prospect_research_runs', 'action_id', aids),
        'action_items': count('action_items', 'source_action_id', aids),
        'contact_activities': count('contact_activities', 'action_id', aids),
    }

    preserve_counts = {
        'email_logs': count('email_logs', 'action_id', aids),
        'signing_documents': count('signing_documents', 'action_id', aids),
        'published_pages': count('published_pages', 'action_id', aids),
        'layer_income_records': count('layer_income_records', 'action_id', aids),
        'kajabi_products': count('kajabi_products', 'action_id', aids),
        'email_campaigns': count('email_campaigns', 'action_id', aids),
        'contacts': count('contacts', 'source_action_id', aids),
    }

    # Money already recorded against these actions — the single most alarming number
    # for an admin to see preserved rather than destroyed, so surface it explicitly.
    income_total = 0.0
    if aids and (not tables or 'layer_income_records' in tables):
        for batch in _chunk(aids):
            keys = {'v%d' % i: v for i, v in enumerate(batch)}
            placeholders = ', '.join(':' + k for k in keys)
            val = db.session.execute(
                text('SELECT COALESCE(SUM(amount), 0) FROM layer_income_records '
                     'WHERE is_void = 0 AND action_id IN ({})'.format(placeholders)),
                keys,
            ).scalar()
            income_total += float(val or 0)

    running = [c for c in scope['cycles'] if c.cycle_completed_at is None]

    return {
        'simulation_id': simulation_id,
        'cycle_numbers': [c.cycle_number for c in scope['cycles']],
        'cycles_in_scope': len(cids),
        'running_cycles': [c.cycle_number for c in running],
        'includes_user_runs': include_orphans,
        'delete': {k: v for k, v in delete_counts.items() if v},
        'preserve': {k: v for k, v in preserve_counts.items() if v},
        'preserved_income_usd': round(income_total, 2),
        'total_rows_deleted': sum(delete_counts.values()),
    }


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------

def delete_cycles(simulation_id: str, cycle_ids: list = None,
                  include_orphans: bool = False, admin_user_id: str = None,
                  force_running: bool = False) -> dict:
    """Delete cycles and everything they generated. Tier 3 is preserved and unlinked.

    Runs as one transaction: either the whole cascade lands or nothing does. A
    half-deleted cycle graph would leave the orchestrator dispatching against
    artifacts that no longer exist.
    """
    from app.extensions import db
    from app.models.audit_log import AuditLog

    scope = resolve_scope(simulation_id, cycle_ids)
    cids, qids = scope['cycle_ids'], scope['queue_ids']
    aids = list(scope['action_ids'])
    if include_orphans:
        aids = sorted(set(aids) | set(orphan_action_ids(simulation_id)))

    if not cids and not aids:
        return {'ok': True, 'deleted': {}, 'preserved': {}, 'cycles_deleted': 0,
                'message': 'Nothing to delete.'}

    # A cycle still mid-flight has a Celery task that will write to rows we are
    # about to remove. Refuse by default rather than race it.
    running = [c.cycle_number for c in scope['cycles'] if c.cycle_completed_at is None]
    if running and not force_running:
        return {
            'ok': False,
            'error': 'cycle_running',
            'running_cycles': running,
            'message': ('Cycle {} has not finished. Wait for it, or re-submit with '
                        'force=true to delete anyway.'.format(
                            ', '.join(str(n) for n in running))),
        }

    snapshot = preview(simulation_id, cycle_ids, include_orphans)
    deleted, preserved = {}, {}
    tables = _existing_tables()

    def has(table):
        return not tables or table in tables

    def wipe(table, column, values, label=None):
        if not values or not has(table):
            return
        n = 0
        for batch in _chunk(values):
            keys = {'v%d' % i: v for i, v in enumerate(batch)}
            placeholders = ', '.join(':' + k for k in keys)
            n += _exec('DELETE FROM {} WHERE {} IN ({})'.format(
                table, column, placeholders), keys)
        if n:
            deleted[label or table] = deleted.get(label or table, 0) + n

    def unlink(table, column, values, where_extra=''):
        if not values or not has(table):
            return
        n = 0
        for batch in _chunk(values):
            keys = {'v%d' % i: v for i, v in enumerate(batch)}
            placeholders = ', '.join(':' + k for k in keys)
            n += _exec('UPDATE {} SET {} = NULL WHERE {} IN ({}){}'.format(
                table, column, column, placeholders, where_extra), keys)
        if n:
            preserved[table] = preserved.get(table, 0) + n

    try:
        # ── Tier 3 first: unlink before the parents disappear ────────────────
        # Several of these FKs declare ON DELETE CASCADE, so deleting agent_actions
        # before unlinking would take the real-world records down with them.
        if aids:
            # outcome_reports point at calibration_runs, not at actions, so resolve
            # the run ids before those runs are deleted below.
            if has('calibration_runs') and has('outcome_reports'):
                from sqlalchemy import text as _t
                run_ids = []
                for batch in _chunk(aids):
                    keys = {'v%d' % i: v for i, v in enumerate(batch)}
                    ph = ', '.join(':' + k for k in keys)
                    run_ids += [r[0] for r in db.session.execute(
                        _t('SELECT id FROM calibration_runs WHERE action_id IN ({})'.format(ph)),
                        keys).fetchall()]
                unlink('outcome_reports', 'run_id', run_ids)

            for table, column in _TIER3_UNLINK:
                if table == 'outcome_reports':
                    continue
                unlink(table, column, aids)

            # A social post already on the platform is a real-world event; one still
            # pending review never happened and goes with the artifact.
            if has('social_post_queue'):
                unlink('social_post_queue', 'artifact_id', aids,
                       ' AND platform_post_id IS NOT NULL')
                wipe_pending = []
                for batch in _chunk(aids):
                    keys = {'v%d' % i: v for i, v in enumerate(batch)}
                    ph = ', '.join(':' + k for k in keys)
                    from sqlalchemy import text as _t2
                    wipe_pending += [r[0] for r in db.session.execute(
                        _t2('SELECT id FROM social_post_queue WHERE artifact_id IN ({}) '
                            'AND platform_post_id IS NULL'.format(ph)), keys).fetchall()]
                wipe('social_post_queue', 'id', wipe_pending)

            # Contacts are kept; only their provenance pointers are cleared so they
            # do not dangle. outreach_count / last_contacted_at are untouched, which
            # is what stops the next cycle re-emailing these people.
            unlink('contacts', 'source_action_id', aids)

        if cids:
            unlink('contacts', 'source_cycle_id', cids)

        # ── Tier 2: generated activity, children before parents ─────────────
        wipe('contact_activities', 'action_id', aids)
        wipe('action_items', 'source_action_id', aids)
        wipe('prospect_research_runs', 'action_id', aids)
        wipe('calibration_runs', 'action_id', aids)
        wipe('action_steps', 'agent_action_id', aids)
        wipe('artifact_dependencies', 'upstream_action_id', aids)
        wipe('artifact_versions', 'action_id', aids)
        wipe('agent_actions', 'id', aids)

        # ── Tier 1: the cycle machinery ─────────────────────────────────────
        wipe('cycle_posterior_snapshots', 'cycle_id', cids)
        wipe('layer6_execution_log', 'cycle_id', cids)
        wipe('layer6_share_tokens', 'cycle_id', cids)
        wipe('action_steps', 'parent_action_id', qids)   # any left keyed to the queue
        wipe('layer6_action_queue', 'cycle_id', cids)
        wipe('layer6_cycles', 'id', cids)

        AuditLog.log(
            'admin_cycles_deleted',
            user_id=admin_user_id,
            resource_id=simulation_id,
            metadata={
                'cycle_numbers': snapshot['cycle_numbers'],
                'cycles_deleted': len(cids),
                'rows_deleted': deleted,
                'rows_preserved': preserved,
                'included_user_runs': include_orphans,
                'forced_running': bool(running),
            },
        )
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        logger.error('Cycle delete failed for simulation %s: %s', simulation_id, exc,
                     exc_info=True)
        return {'ok': False, 'error': 'delete_failed', 'message': str(exc)[:300]}

    logger.info('Admin deleted %d cycle(s) for simulation %s — %d rows removed, '
                '%d preserved+unlinked',
                len(cids), simulation_id, sum(deleted.values()), sum(preserved.values()))

    return {
        'ok': True,
        'cycles_deleted': len(cids),
        'cycle_numbers': snapshot['cycle_numbers'],
        'deleted': deleted,
        'preserved': preserved,
        'preserved_income_usd': snapshot['preserved_income_usd'],
        'message': _summarise(len(cids), deleted, preserved),
    }


def _summarise(n_cycles: int, deleted: dict, preserved: dict) -> str:
    parts = ['Deleted {} cycle{} and {} related row{}.'.format(
        n_cycles, '' if n_cycles == 1 else 's',
        sum(deleted.values()), '' if sum(deleted.values()) == 1 else 's')]
    if preserved:
        parts.append('Preserved {} record{} of real-world activity (sent email, '
                     'signed documents, published pages, reported income, contacts) '
                     'and unlinked them.'.format(
                         sum(preserved.values()),
                         '' if sum(preserved.values()) == 1 else 's'))
    return ' '.join(parts)


# ---------------------------------------------------------------------------
# Reset
# ---------------------------------------------------------------------------

def reset_simulation(simulation_id: str, admin_user_id: str = None,
                     force_running: bool = False) -> dict:
    """Wipe every cycle and start the orchestrator over from cycle 1.

    Keeps the setup the user already did — layer6_config, selected agents, the
    resume, the resolved calibration cohort, and the five generated layers. Clears
    everything the cycles taught the orchestrator, so scoring restarts from its
    priors rather than from beliefs formed by runs that no longer exist.
    """
    from app.extensions import db
    from app.models.audit_log import AuditLog
    from app.models.simulation import Simulation

    sim = Simulation.query.get(simulation_id)
    if not sim:
        return {'ok': False, 'error': 'not_found'}

    result = delete_cycles(
        simulation_id, cycle_ids=None, include_orphans=True,
        admin_user_id=admin_user_id, force_running=force_running,
    )
    if not result.get('ok'):
        return result

    cleared = {}
    tables = _existing_tables()
    try:
        for table in ('bayesian_posteriors', 'layer6_momentum', 'layer6_outcomes',
                      'agent_context'):
            if tables and table not in tables:
                continue
            n = _exec('DELETE FROM {} WHERE simulation_id = :sid'.format(table),
                      {'sid': simulation_id})
            if n:
                cleared[table] = n

        # Return the simulation to a runnable state. lifecycle_phase matters: a sim
        # parked in 'dormant' or 'wound_down' would never be picked up by the cycle
        # scheduler again, making the reset look like it did nothing.
        sim.lifecycle_phase = 'active'
        sim.updated_at = datetime.utcnow()

        AuditLog.log(
            'admin_simulation_reset',
            user_id=admin_user_id,
            resource_id=simulation_id,
            metadata={'cycles_deleted': result['cycles_deleted'],
                      'orchestrator_state_cleared': cleared},
        )
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        logger.error('Reset failed for simulation %s: %s', simulation_id, exc,
                     exc_info=True)
        return {'ok': False, 'error': 'reset_failed', 'message': str(exc)[:300]}

    result['orchestrator_state_cleared'] = cleared
    result['message'] = (
        result['message'] +
        ' Orchestrator state cleared ({}) — the next cycle starts at cycle 1. '
        'Agent selection, Layer 6 config, the resume and the generated layers were '
        'kept.'.format(', '.join(cleared) or 'nothing to clear')
    )
    logger.info('Admin reset simulation %s', simulation_id)
    return result


# ---------------------------------------------------------------------------
# Listing, for the admin UI
# ---------------------------------------------------------------------------

def list_user_simulations(user_id: str) -> list:
    """A user's simulations with cycle and artifact counts."""
    from app.models.agent_action import AgentAction
    from app.models.layer6 import Layer6Cycle
    from app.models.simulation import Simulation

    sims = Simulation.query.filter_by(user_id=user_id).order_by(
        Simulation.created_at.desc(),
    ).all()
    out = []
    for s in sims:
        cycles = Layer6Cycle.query.filter_by(simulation_id=s.id).count()
        actions = AgentAction.query.filter_by(simulation_id=s.id).count()
        out.append({
            'id': s.id,
            'name': s.name,
            'status': s.status,
            'lifecycle_phase': s.lifecycle_phase,
            'expertise_zone': s.expertise_zone,
            'cycle_count': cycles,
            'action_count': actions,
            'created_at': s.created_at.isoformat() if s.created_at else None,
        })
    return out


def list_cycles(simulation_id: str) -> list:
    """Cycles for one simulation, newest first, with per-cycle artifact counts."""
    from app.models.layer6 import Layer6ActionQueue, Layer6Cycle

    cycles = Layer6Cycle.query.filter_by(simulation_id=simulation_id).order_by(
        Layer6Cycle.cycle_number.desc(),
    ).all()
    out = []
    for c in cycles:
        entries = Layer6ActionQueue.query.filter_by(cycle_id=c.id).all()
        out.append({
            'id': c.id,
            'cycle_number': c.cycle_number,
            'phase': c.phase,
            'actions_scored': c.actions_scored,
            'actions_dispatched': c.actions_dispatched,
            'actions_escalated': c.actions_escalated,
            'queued': len(entries),
            'artifacts': len([e for e in entries if e.agent_action_id]),
            'is_running': c.cycle_completed_at is None,
            'started_at': c.cycle_started_at.isoformat() if c.cycle_started_at else None,
            'completed_at': c.cycle_completed_at.isoformat() if c.cycle_completed_at else None,
        })
    return out
