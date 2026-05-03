import json
import time
import threading
from flask import Response, stream_with_context, current_app
from app.models.simulation import Simulation, SimulationLayer


def sse_event(event_type: str, data: dict, event_id: str = None) -> str:
    lines = []
    if event_id:
        lines.append(f'id: {event_id}')
    lines.append(f'event: {event_type}')
    lines.append(f'data: {json.dumps(data)}')
    lines.append('')
    lines.append('')
    return '\n'.join(lines)


def sse_keepalive() -> str:
    """SSE comment — invisible to JS but prevents proxy/server from closing an idle connection."""
    return ': keepalive\n\n'


def _start_generation_if_needed(simulation_id: str, app_obj):
    """
    Restart generation if the confirm-payment background thread died and left the
    simulation stuck in STATUS_PROCESSING.  Uses an atomic SQL UPDATE as a mutex
    so only one thread ever generates for a given simulation at a time.
    """
    import logging
    logger = logging.getLogger(__name__)

    with app_obj.app_context():
        from app.extensions import db
        from app.models.simulation import Simulation as Sim, SimulationLayer, IncomeStream
        from app.models.resume import Resume
        from flask import current_app

        # Atomic mutex: only the thread that transitions PROCESSING → STREAMING proceeds.
        try:
            rows = db.session.execute(
                db.text(
                    "UPDATE simulations SET status = :new WHERE id = :sid AND status = :old"
                ),
                {'new': Sim.STATUS_STREAMING, 'sid': simulation_id, 'old': Sim.STATUS_PROCESSING},
            )
            db.session.commit()
            if rows.rowcount == 0:
                return  # another thread already claimed it
        except Exception as e:
            db.session.rollback()
            logger.error('SSE mutex failed for %s: %s', simulation_id, e)
            return

        # We own generation — run the 5 layers sequentially inline.
        sim = Sim.query.get(simulation_id)
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
                logger.info('SSE recovery: simulation %s layer %d complete', simulation_id, layer_num)

            sim = Sim.query.get(simulation_id)
            sim.status = Sim.STATUS_COMPLETE
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
                logger.error('Invoice email failed (SSE recovery) %s: %s', simulation_id, email_err)

        except Exception as exc:
            logger.error('SSE recovery generation failed for %s: %s', simulation_id, exc)
            db.session.rollback()
            sim = Sim.query.get(simulation_id)
            if sim:
                sim.status = Sim.STATUS_ERROR
                db.session.commit()


def stream_simulation(simulation_id: str, user_id: str):
    app_obj = current_app._get_current_object()

    def generate():
        sim = Simulation.query.get(simulation_id)
        if not sim or sim.user_id != user_id:
            yield sse_event('simulation_error', {'error': 'Simulation not found'})
            return

        yield sse_event('simulation_start', {
            'simulation_id': simulation_id,
            'name': sim.name,
            'expertise_zone': sim.expertise_zone,
        })

        # If the generation thread from confirm-payment died, restart it here.
        # The atomic SQL UPDATE acts as a mutex — only one SSE connection wins.
        if sim.status == Simulation.STATUS_PROCESSING:
            t = threading.Thread(
                target=_start_generation_if_needed,
                args=(simulation_id, app_obj),
                daemon=True,
            )
            t.start()

        sent_layers = set()
        timeout      = 600   # 10 min — 5 Claude calls can take 2+ min total
        start_time   = time.time()
        poll_interval = 2.0
        last_keepalive = time.time()

        while time.time() - start_time < timeout:
            sim = Simulation.query.get(simulation_id)
            if not sim:
                break

            if sim.status == Simulation.STATUS_ERROR:
                yield sse_event('simulation_error', {
                    'error': sim.error_message or 'Generation failed',
                    'simulation_id': simulation_id,
                })
                return

            if sim.status == Simulation.STATUS_REFUNDED:
                yield sse_event('simulation_error', {
                    'error': 'Generation failed — payment refunded',
                    'simulation_id': simulation_id,
                    'refunded': True,
                })
                return

            layers = SimulationLayer.query.filter_by(
                simulation_id=simulation_id
            ).order_by(SimulationLayer.layer_number).all()

            for layer in layers:
                if layer.id not in sent_layers:
                    yield sse_event('layer_start', {
                        'layer_number': layer.layer_number,
                        'layer_name': layer.layer_name,
                    }, event_id=f'layer-{layer.layer_number}')
                    yield sse_event('layer_data', layer.to_dict(),
                                    event_id=f'layer-data-{layer.layer_number}')
                    sent_layers.add(layer.id)
                    last_keepalive = time.time()

            if sim.status == Simulation.STATUS_COMPLETE:
                yield sse_event('simulation_complete', {
                    'simulation_id': simulation_id,
                    'total_layers': len(layers),
                })
                return

            # Send keepalive every 15 s so Apache/Passenger don't close the connection
            if time.time() - last_keepalive >= 15:
                yield sse_keepalive()
                last_keepalive = time.time()

            time.sleep(poll_interval)

        yield sse_event('simulation_error', {'error': 'Stream timeout — refresh to check status'})

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={
            'Cache-Control':     'no-cache',
            'X-Accel-Buffering': 'no',      # nginx
            'X-Content-Type-Options': 'nosniff',
            'Connection':        'keep-alive',
        },
    )
