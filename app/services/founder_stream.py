"""Founder workspace live updates (FOUNDER_PLAN.md section 5).

publish() writes a venture_step_event row; the SSE generator polls that table
every few seconds (the layer6 stream pattern), so no in-memory bus or Redis is
needed and every worker process sees every event.

    founder_stream.publish(venture.id, 'step_ready', {'plan_step_id': s.id, 'title': s.title})
    return founder_stream.response(venture.id)      # from /api/founder/v/<id>/stream
"""
from __future__ import annotations

import json
import logging
import time

logger = logging.getLogger(__name__)

POLL_SECONDS = 5
MAX_SECONDS = 300
BATCH = 50


def publish(venture_id: str, event: str, data: dict = None, commit: bool = True):
    """Record one event for the venture's stream. data may carry plan_step_id,
    from_state, to_state, actor_user_id, actor_kind and note; anything else is
    kept in the note as JSON. from_state/to_state are stored only when they are
    real step states (VenturePlanStep.STATES); the event name lives in the note."""
    from app.extensions import db
    from app.models.founder import VenturePlanStep, VentureStepEvent
    data = dict(data or {})
    actor_kind = data.pop('actor_kind', 'system')
    if actor_kind not in VentureStepEvent.ACTOR_KINDS:
        actor_kind = 'system'
    from_state = data.pop('from_state', None)
    to_state = data.pop('to_state', None)
    row = VentureStepEvent(
        venture_id=venture_id,
        plan_step_id=data.pop('plan_step_id', None),
        from_state=from_state if from_state in VenturePlanStep.STATES else None,
        to_state=to_state if to_state in VenturePlanStep.STATES else None,
        actor_user_id=data.pop('actor_user_id', None),
        actor_kind=actor_kind,
    )
    note = data.pop('note', None)
    payload = {'event': event}
    if note:
        payload['note'] = note
    payload.update(data)
    row.note = json.dumps(payload, default=str)
    db.session.add(row)
    if commit:
        db.session.commit()
    return row


def _event_payload(row) -> tuple:
    try:
        extra = json.loads(row.note) if row.note else {}
    except ValueError:
        extra = {'note': row.note}
    if not isinstance(extra, dict):
        extra = {'note': extra}
    name = extra.pop('event', None) or row.to_state or 'update'
    data = row.to_dict()
    data['note'] = extra.pop('note', None)
    data.update(extra)
    return name, data


def events_since(venture_id: str, after_created=None, seen_ids=None) -> list:
    """Events at or after the cursor time that haven't been sent yet, oldest
    first. seen_ids holds the ids already sent at the cursor time (ids are
    random, so the time alone can't order two events in the same second)."""
    from app.models.founder import VentureStepEvent
    seen_ids = seen_ids or ()
    q = VentureStepEvent.query.filter_by(venture_id=venture_id)
    if after_created is not None:
        q = q.filter(VentureStepEvent.created_at >= after_created)
    rows = q.order_by(VentureStepEvent.created_at.asc(), VentureStepEvent.id.asc()).limit(BATCH + len(seen_ids)).all()
    return [r for r in rows if r.id not in seen_ids][:BATCH]


def generate(venture_id: str, max_seconds: int = MAX_SECONDS, poll_seconds: int = POLL_SECONDS,
             sleep=time.sleep):
    """SSE generator: events written after the stream opened, plus keepalives."""
    from app.blueprints.simulations.sse import sse_event, sse_keepalive
    from app.extensions import db
    from app.models.founder import VentureStepEvent
    last = (VentureStepEvent.query.filter_by(venture_id=venture_id)
            .order_by(VentureStepEvent.created_at.desc()).first())
    cursor = last.created_at if last else None
    seen = set()
    if cursor is not None:
        seen = {r.id for r in VentureStepEvent.query.filter_by(venture_id=venture_id, created_at=cursor).all()}
    start = time.time()
    while time.time() - start < max_seconds:
        try:
            # End the read transaction so each poll sees rows other workers
            # committed since (MySQL REPEATABLE READ would keep the old snapshot).
            db.session.rollback()
            for r in events_since(venture_id, cursor, seen):
                name, data = _event_payload(r)
                yield sse_event(name, data)
                if r.created_at != cursor:
                    cursor, seen = r.created_at, set()
                seen.add(r.id)
        except Exception as exc:
            logger.error('founder stream poll failed for %s: %s', venture_id, exc)
        try:
            yield sse_keepalive()
        except GeneratorExit:
            return
        sleep(poll_seconds)


def response(venture_id: str):
    """A Flask streaming response for the venture's SSE stream."""
    from flask import Response, stream_with_context
    return Response(stream_with_context(generate(venture_id)), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})
