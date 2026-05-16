"""
sse.py — simulation generation recovery helper.

The SSE streaming approach was replaced with client-side polling (sse.js) to avoid
holding a Passenger worker thread open for the entire generation duration, which
blocked navigation on shared hosting.

This module now exposes only the recovery trigger used by the polling client.
"""
import logging
import threading
from flask import current_app

logger = logging.getLogger(__name__)


def start_generation_if_needed(simulation_id: str, app_obj):
    """
    Restart generation if the confirm-payment background thread died and left the
    simulation stuck in STATUS_PROCESSING.  Uses an atomic SQL UPDATE as a mutex
    (PROCESSING → STREAMING) so only one thread ever generates for a given simulation.
    """
    with app_obj.app_context():
        from app.extensions import db
        from app.models.simulation import Simulation, SimulationLayer, IncomeStream
        from app.models.resume import Resume
        from flask import current_app as _app

        # Atomic mutex: only the thread that bumps PROCESSING → STREAMING proceeds.
        try:
            rows = db.session.execute(
                db.text(
                    "UPDATE simulations SET status = :new WHERE id = :sid AND status = :old"
                ),
                {'new': Simulation.STATUS_STREAMING, 'sid': simulation_id,
                 'old': Simulation.STATUS_PROCESSING},
            )
            db.session.commit()
            if rows.rowcount == 0:
                return  # already streaming/complete/error — nothing to do
        except Exception as e:
            db.session.rollback()
            logger.error('Recovery mutex failed for %s: %s', simulation_id, e)
            return

        sim = Simulation.query.get(simulation_id)
        if not sim:
            return

        try:
            from app.services.claude import generate_simulation_layer
            from app.services.fintech import is_fintech_enabled

            resume = Resume.query.get(sim.resume_id)
            parsed_text = resume.parsed_text if resume else ''
            fintech_enabled = is_fintech_enabled()

            for layer_num in range(1, 6):
                layer_data = generate_simulation_layer(
                    layer_number=layer_num,
                    expertise_zone=sim.expertise_zone,
                    focus_hint=sim.focus_hint or '',
                    parsed_text=parsed_text,
                    user_id=sim.user_id,
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
                logger.info('Recovery: simulation %s layer %d complete', simulation_id, layer_num)

            sim = Simulation.query.get(simulation_id)
            sim.status = Simulation.STATUS_COMPLETE
            charged = sim.amount_charged_cents or _app.config['SIMULATION_PRICE_CENTS']
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
                logger.error('Invoice email failed (recovery) %s: %s', simulation_id, email_err)

        except Exception as exc:
            logger.error('Recovery generation failed for %s: %s', simulation_id, exc)
            db.session.rollback()
            sim = Simulation.query.get(simulation_id)
            if sim:
                sim.status = Simulation.STATUS_ERROR
                db.session.commit()


def push_sse_event(simulation_id: str, payload: dict):
    """
    Placeholder for real-time SSE push. Currently a no-op — the app uses
    client-side polling. Replace with a real event bus (Redis pub/sub, etc.)
    when SSE streaming is re-introduced.
    """
    logger.debug('SSE event queued (no-op): sim=%s type=%s', simulation_id, payload.get('event_type'))


def trigger_recovery(simulation_id: str):
    """Start the recovery thread. Returns immediately — generation runs in background."""
    app_obj = current_app._get_current_object()
    t = threading.Thread(
        target=start_generation_if_needed,
        args=(simulation_id, app_obj),
        daemon=True,
    )
    t.start()
