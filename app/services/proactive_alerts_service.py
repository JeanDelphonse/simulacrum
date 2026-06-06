"""
Proactive Alert System (SIM-PRD-ENHANCE-001 ENH-03/04).
Runs on a 15-minute scheduler heartbeat to detect trigger conditions and
surface ActionItems in the GCC Action Queue.
"""
from __future__ import annotations
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

INCOME_MILESTONES = [1_000, 5_000, 10_000, 25_000, 50_000, 100_000]
COLD_PROSPECT_DAYS = 5


def check_proactive_alerts():
    """Main entry point — iterate all active simulations and fire alert checks."""
    from app.extensions import db
    from app.models.simulation import Simulation
    from app.models.layer6 import Layer6Config

    try:
        active_sim_ids = [
            cfg.simulation_id
            for cfg in Layer6Config.query.filter_by(is_active=True).all()
        ]
        for sim_id in active_sim_ids:
            sim = Simulation.query.get(sim_id)
            if not sim or sim.status not in ('complete', 'processing'):
                continue
            try:
                _check_cold_prospects(sim)
                _check_income_milestones(sim)
                _check_layer_unlocks(sim)
                _check_cycle_due(sim)
            except Exception as exc:
                logger.warning('Proactive alerts for sim %s failed: %s', sim_id, exc)
    except Exception as exc:
        logger.exception('check_proactive_alerts top-level failure: %s', exc)


def _check_cold_prospects(sim):
    """Alert when a contact has had no activity for COLD_PROSPECT_DAYS days."""
    from app.extensions import db
    from app.models.contact import Contact, ContactActivity
    from utils.action_items import create_action_item

    cutoff = datetime.utcnow() - timedelta(days=COLD_PROSPECT_DAYS)
    # Contacts that were created or last touched before the cutoff and are still prospects
    stale = Contact.query.filter(
        Contact.user_id == sim.user_id,
        Contact.pipeline_stage == 'prospect',
        Contact.is_archived.is_(False),
        Contact.do_not_contact.is_(False),
        Contact.created_at <= cutoff,
    ).limit(5).all()

    for contact in stale:
        # Check that no activity was logged since cutoff
        recent = ContactActivity.query.filter(
            ContactActivity.contact_id == contact.id,
            ContactActivity.created_at >= cutoff,
        ).first()
        if recent:
            continue

        # Avoid duplicate alerts — check if one already exists for this contact
        from app.models.layer6 import ActionItem
        dup = ActionItem.query.filter_by(
            simulation_id=sim.id,
            item_type='proactive_alert',
            source_contact_id=contact.id,
            status=ActionItem.STATUS_ACTIVE,
        ).first()
        if dup:
            continue

        name = contact.display_name()
        create_action_item(
            simulation_id=sim.id,
            user_id=sim.user_id,
            item_type='proactive_alert',
            title=f'Follow up with {name} — no contact in {COLD_PROSPECT_DAYS}+ days',
            description=f'{name} ({contact.email}) is still in prospect stage with no recent activity.',
            action_url=f'/contacts/{contact.id}',
            source_contact_id=contact.id,
            emit_sse=True,
        )
        logger.info('Cold prospect alert created for contact %s (sim %s)', contact.id, sim.id)


def _check_income_milestones(sim):
    """Alert when total captured income crosses a milestone threshold."""
    from app.extensions import db
    from app.models.layer6 import Layer6Outcome, Layer6Momentum
    from sqlalchemy import func
    from utils.action_items import create_action_item

    total = float(
        db.session.query(func.sum(Layer6Outcome.actual_income))
        .filter_by(simulation_id=sim.id).scalar() or 0
    )
    total_cents = int(total * 100)

    # Find which milestone we've just crossed
    crossed = None
    for m in INCOME_MILESTONES:
        if total_cents >= m * 100:
            crossed = m

    if not crossed:
        return

    crossed_cents = crossed * 100

    # Check the momentum row to see if we already notified for this milestone
    momentum = Layer6Momentum.query.filter_by(simulation_id=sim.id).order_by(
        Layer6Momentum.snapshot_date.desc()
    ).first()

    last_notified = (momentum.last_milestone_reached_cents or 0) if momentum else 0
    if crossed_cents <= last_notified:
        return

    from app.models.layer6 import ActionItem
    dup = ActionItem.query.filter_by(
        simulation_id=sim.id,
        item_type='layer_unlocked',
        status=ActionItem.STATUS_ACTIVE,
    ).filter(ActionItem.title.like(f'%${crossed:,}%')).first()
    if dup:
        return

    create_action_item(
        simulation_id=sim.id,
        user_id=sim.user_id,
        item_type='layer_unlocked',
        title=f'Income milestone: ${crossed:,} captured!',
        description=f'Your simulation has now captured ${total:,.0f} in total income.',
        action_url=f'/simulations/{sim.id}/gcc?tab=income',
        emit_sse=True,
    )

    # Update the last milestone notified
    if momentum:
        momentum.last_milestone_reached_cents = crossed_cents
        from app.extensions import db as _db
        try:
            _db.session.commit()
        except Exception:
            _db.session.rollback()

    logger.info('Income milestone %d alert for sim %s', crossed, sim.id)


def _check_layer_unlocks(sim):
    """Alert when a layer blocker action was just completed."""
    from app.models.agent_action import AgentAction
    from app.services.layer6 import _LAYER_BLOCKERS
    from utils.action_items import create_action_item
    from app.models.layer6 import ActionItem

    recently = datetime.utcnow() - timedelta(minutes=30)

    for layer_num, blocker_type in _LAYER_BLOCKERS.items():
        unlock_action = AgentAction.query.filter_by(
            simulation_id=sim.id,
            action_type=blocker_type,
            status=AgentAction.STATUS_COMPLETE,
        ).filter(AgentAction.completed_at >= recently).first()

        if not unlock_action:
            continue

        dup = ActionItem.query.filter_by(
            simulation_id=sim.id,
            item_type='layer_unlocked',
            status=ActionItem.STATUS_ACTIVE,
        ).filter(ActionItem.title.like(f'%Layer {layer_num}%')).first()
        if dup:
            continue

        layer_names = {2: 'Leveraged', 3: 'Productized', 4: 'Automated', 5: 'Wealth Deployment'}
        name = layer_names.get(layer_num, f'Layer {layer_num}')
        create_action_item(
            simulation_id=sim.id,
            user_id=sim.user_id,
            item_type='layer_unlocked',
            title=f'Layer {layer_num} ({name} Income) is now unlocked!',
            description=f'You completed {blocker_type.replace("_", " ")} — Layer {layer_num} is ready to run.',
            action_url=f'/simulations/{sim.id}/gcc?tab=journey',
            layer_number=layer_num,
            emit_sse=True,
        )
        logger.info('Layer %d unlock alert for sim %s', layer_num, sim.id)


def _check_cycle_due(sim):
    """Alert when a simulation is overdue for a new cycle."""
    from app.models.layer6 import Layer6Cycle, Layer6Config, ActionItem
    from utils.action_items import create_action_item

    config = Layer6Config.query.filter_by(simulation_id=sim.id).first()
    if not config or not config.is_active:
        return

    last_cycle = Layer6Cycle.query.filter_by(simulation_id=sim.id).order_by(
        Layer6Cycle.cycle_number.desc()
    ).first()
    if not last_cycle:
        return

    cadence_hours = {
        'daily': 24, 'every_3_days': 72, 'weekly': 168,
        'every_12h': 12, 'every_48h': 48, 'every_72h': 72, 'every_168h': 168,
    }
    hours = cadence_hours.get(config.cadence, 24)
    due_at = last_cycle.cycle_started_at + timedelta(hours=hours * 1.5)  # 50% overdue threshold

    if datetime.utcnow() < due_at:
        return

    dup = ActionItem.query.filter_by(
        simulation_id=sim.id,
        item_type='cycle_ready',
        status=ActionItem.STATUS_ACTIVE,
    ).first()
    if dup:
        return

    create_action_item(
        simulation_id=sim.id,
        user_id=sim.user_id,
        item_type='cycle_ready',
        title='Your orchestrator is overdue for a new cycle',
        description=f'Last cycle ran {int((datetime.utcnow() - last_cycle.cycle_started_at).total_seconds() / 3600)} hours ago. Run a new cycle to keep momentum.',
        action_url=f'/simulations/{sim.id}/gcc?tab=journey',
        emit_sse=True,
    )
    logger.info('Cycle due alert for sim %s', sim.id)


def send_alert_digest():
    """Send a daily email digest of active proactive alerts to each user (ENH-04)."""
    from app.extensions import db
    from app.models.layer6 import ActionItem
    from app.models.user import User
    from sqlalchemy import func

    # Find users who have active proactive alerts
    user_alert_counts = db.session.query(
        ActionItem.user_id,
        func.count(ActionItem.id).label('cnt'),
    ).filter(
        ActionItem.status == ActionItem.STATUS_ACTIVE,
        ActionItem.item_type.in_(['proactive_alert', 'layer_unlocked', 'cycle_ready']),
    ).group_by(ActionItem.user_id).all()

    for row in user_alert_counts:
        if row.cnt == 0:
            continue
        user = User.query.get(row.user_id)
        if not user or not user.email:
            continue
        alerts = ActionItem.query.filter(
            ActionItem.user_id == row.user_id,
            ActionItem.status == ActionItem.STATUS_ACTIVE,
            ActionItem.item_type.in_(['proactive_alert', 'layer_unlocked', 'cycle_ready']),
        ).order_by(ActionItem.urgency_tier.asc(), ActionItem.created_at.desc()).limit(10).all()
        try:
            from app.services.email_service import send_alert_digest_email
            send_alert_digest_email(user, alerts)
        except Exception as exc:
            logger.warning('Alert digest email failed for user %s: %s', row.user_id, exc)
