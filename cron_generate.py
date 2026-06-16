"""
cron_generate.py — run by GoDaddy cron every minute.

Picks up simulations in STATUS_PROCESSING that have no layers yet
and generates all 5 layers synchronously.

cPanel cron command:
  /home/dburriyy6pdz/virtualenv/public_html/simulacrum/3.11/bin/python \
    /home/dburriyy6pdz/public_html/simulacrum/cron_generate.py >> \
    /home/dburriyy6pdz/public_html/simulacrum/cron.log 2>&1
"""
import sys
import os

APP_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, APP_ROOT)

import logging
logging.basicConfig(
    filename=os.path.join(APP_ROOT, 'cron.log'),
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s %(message)s',
)
log = logging.getLogger(__name__)

from app import create_app

app = create_app('production')

with app.app_context():
    from app.extensions import db
    from app.models.simulation import Simulation, SimulationLayer

    # Find simulations that are processing but have no layers yet
    pending = (
        Simulation.query
        .filter(Simulation.status == Simulation.STATUS_PROCESSING)
        .all()
    )

    if not pending:
        log.info('No simulations to process')
        sys.exit(0)

    for sim in pending:
        layer_count = SimulationLayer.query.filter_by(simulation_id=sim.id).count()

        if layer_count >= 5:
            # All layers exist but status never flipped — heal it atomically
            try:
                res = db.session.execute(
                    db.text("UPDATE simulations SET status = 'complete' WHERE id = :sid AND status = 'processing'"),
                    {'sid': sim.id}
                )
                db.session.commit()
                if res.rowcount == 0:
                    continue
            except Exception as e:
                db.session.rollback()
                log.error(f'Failed to heal complete simulation {sim.id}: {e}')
                continue

            from app.models.user import User
            user = User.query.get(sim.user_id)
            if user:
                user.simulation_count = (user.simulation_count or 0) + 1
                user.total_spend = (user.total_spend or 0) + (sim.amount_charged_cents or 1000)
            db.session.commit()
            log.info(f'Simulation {sim.id} healed — marked complete ({layer_count} layers found)')
            continue

        # Atomic lock: transition status to STATUS_STREAMING so no other runner picks it up.
        try:
            res = db.session.execute(
                db.text("UPDATE simulations SET status = :new WHERE id = :sid AND status = :old"),
                {'new': Simulation.STATUS_STREAMING, 'sid': sim.id, 'old': Simulation.STATUS_PROCESSING}
            )
            db.session.commit()
            if res.rowcount == 0:
                log.info(f'Simulation {sim.id} already locked or processed by another task, skipping')
                continue
        except Exception as e:
            db.session.rollback()
            log.error(f'Failed to lock simulation {sim.id}: {e}')
            continue

        if layer_count > 0:
            # Partial — layers exist but generation was cut short; reset and retry
            log.warning(f'Simulation {sim.id} has only {layer_count}/5 layers — resetting for retry')
            for layer in SimulationLayer.query.filter_by(simulation_id=sim.id).all():
                db.session.delete(layer)
            db.session.commit()

        log.info(f'Starting generation for simulation {sim.id} ({sim.name})')
        try:
            from app.tasks.simulation import generate_simulation_task
            generate_simulation_task.apply(args=[sim.id])
            log.info(f'Simulation {sim.id} generation complete')
        except Exception as e:
            log.error(f'Simulation {sim.id} generation failed: {e}')
