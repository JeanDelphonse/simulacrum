from celery_worker import celery
from flask import current_app
import logging
import json
from concurrent.futures import ThreadPoolExecutor, as_completed

logger = logging.getLogger(__name__)


def _generate_single_layer(app_obj, simulation_id, layer_num, expertise_zone,
                            focus_hint, parsed_text, user_id, fintech_enabled):
    """Generate one layer in its own app context + DB session (safe for threading)."""
    with app_obj.app_context():
        from app.extensions import db
        from app.models.simulation import SimulationLayer, IncomeStream
        from app.services.claude import generate_simulation_layer

        layer_data = generate_simulation_layer(
            layer_number=layer_num,
            expertise_zone=expertise_zone,
            focus_hint=focus_hint,
            parsed_text=parsed_text,
            user_id=user_id,
            simulation_id=simulation_id,
            fintech_enabled=fintech_enabled,
        )

        sim_layer = SimulationLayer(
            simulation_id=simulation_id,
            layer_number=layer_data.get('layer_number', layer_num),
            layer_name=layer_data.get('layer_name', f'Layer {layer_num}'),
            income_type=layer_data.get('income_type', ''),
            ai_narrative=layer_data.get('ai_narrative', ''),
            priority_score=layer_data.get('priority_score'),
        )
        db.session.add(sim_layer)
        db.session.flush()

        for stream_data in layer_data.get('income_streams', []):
            low = stream_data.get('est_monthly_low')
            high = stream_data.get('est_monthly_high')
            if low is not None and high is not None and low > high:
                low, high = high, low
            stream = IncomeStream(
                layer_id=sim_layer.id,
                name=stream_data.get('name', ''),
                description=stream_data.get('description', ''),
                platform=stream_data.get('platform', ''),
                est_monthly_low=low,
                est_monthly_high=high,
                ai_reasoning=stream_data.get('ai_reasoning', ''),
                automation_level=stream_data.get('automation_level', ''),
                launch_timeline_weeks=stream_data.get('launch_timeline_weeks'),
            )
            stream.deliverable_refs = stream_data.get('deliverable_refs', [])
            db.session.add(stream)

        db.session.commit()
        logger.info('Simulation %s layer %d complete', simulation_id, layer_num)
        return layer_num


@celery.task(bind=True, max_retries=2, default_retry_delay=10)
def generate_simulation_task(self, simulation_id: str):
    """
    Generate all 5 layers for a simulation — runs 5 Claude calls in parallel.
    Triggered by confirm-payment (background thread) or cron fallback.
    On failure after retries, issues automatic Stripe refund.
    """
    from app.extensions import db
    from app.models.simulation import Simulation, SimulationLayer
    from app.models.resume import Resume
    from app.services.fintech import is_fintech_enabled

    try:
        sim = Simulation.query.get(simulation_id)
        if not sim:
            logger.error('Simulation %s not found', simulation_id)
            return

        if sim.status not in (Simulation.STATUS_PENDING, Simulation.STATUS_PROCESSING, Simulation.STATUS_ERROR):
            logger.warning('Simulation %s already in status %s — skipping', simulation_id, sim.status)
            return

        sim.status = Simulation.STATUS_STREAMING
        db.session.commit()

        resume = Resume.query.get(sim.resume_id)
        parsed_text = resume.parsed_text if resume else ''
        fintech_enabled = is_fintech_enabled()

        app_obj = current_app._get_current_object()

        # Run all 5 Claude calls in parallel — reduces wall time from ~150s to ~30s
        errors = []
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = {
                executor.submit(
                    _generate_single_layer,
                    app_obj, simulation_id, layer_num,
                    sim.expertise_zone, sim.focus_hint or '',
                    parsed_text, sim.user_id, fintech_enabled,
                ): layer_num
                for layer_num in range(1, 6)
            }
            for future in as_completed(futures):
                layer_num = futures[future]
                try:
                    future.result()
                except Exception as layer_err:
                    logger.error('Layer %d failed for simulation %s: %s', layer_num, simulation_id, layer_err)
                    errors.append((layer_num, layer_err))

        if errors:
            raise RuntimeError(f'Layer generation failed: {errors}')

        # Verify all 5 layers were created
        layer_count = SimulationLayer.query.filter_by(simulation_id=simulation_id).count()
        if layer_count < 5:
            raise RuntimeError(f'Expected 5 layers but only {layer_count} were created')

        sim = Simulation.query.get(simulation_id)
        sim.status = Simulation.STATUS_COMPLETE
        charged = sim.amount_charged_cents or current_app.config['SIMULATION_PRICE_CENTS']
        from app.models.user import User
        user = User.query.get(sim.user_id)
        if user:
            user.simulation_count = (user.simulation_count or 0) + 1
            user.total_spend = (user.total_spend or 0) + charged
        db.session.commit()

        try:
            from app.services.email_service import send_invoice_email
            send_invoice_email(user.email, user.full_name, sim.name, sim.id, charged)
        except Exception as email_err:
            logger.error('Invoice email failed for simulation %s: %s', simulation_id, email_err)

        logger.info('Simulation %s completed successfully', simulation_id)

    except Exception as exc:
        logger.error('Simulation %s failed: %s', simulation_id, exc)
        try:
            self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            _handle_simulation_failure(simulation_id)


def _handle_simulation_failure(simulation_id: str):
    """Mark simulation as failed and issue Stripe refund."""
    from app.extensions import db
    from app.models.simulation import Simulation
    from app.services.stripe_service import issue_refund

    sim = Simulation.query.get(simulation_id)
    if not sim:
        return

    sim.status = Simulation.STATUS_ERROR
    db.session.commit()

    if sim.stripe_payment_intent_id:
        try:
            issue_refund(sim.stripe_payment_intent_id, reason='Simulation generation failed after retries')
            sim.status = Simulation.STATUS_REFUNDED
            db.session.commit()
            logger.info('Refund issued for simulation %s', simulation_id)
        except Exception as e:
            logger.error('Failed to issue refund for %s: %s', simulation_id, e)

    try:
        from app.models.user import User
        from app.services.email_service import send_simulation_failed_email
        user = User.query.get(sim.user_id)
        if user:
            charged = sim.amount_charged_cents or 1000
            send_simulation_failed_email(user.email, user.full_name, sim.name, sim.id, charged)
    except Exception as e:
        logger.error('Failed to send failure notification for %s: %s', simulation_id, e)
