import json
import time
from flask import Response, stream_with_context
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


def stream_simulation(simulation_id: str, user_id: str):
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
