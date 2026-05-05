from __future__ import annotations

from flask import jsonify, request
from flask_login import current_user, login_required

from app.blueprints.chat import chat_bp
from app.extensions import db


def _check_sim(sim_id: str):
    from app.models.simulation import Simulation
    return Simulation.query.filter_by(id=sim_id, user_id=current_user.id).first_or_404()


def _active_session_id(sim_id: str, user_id: str):
    """Return the most recent active session_id for this sim+user, or None."""
    from app.models.chat import SimulationChatMessage
    latest = (
        SimulationChatMessage.query
        .filter_by(simulation_id=sim_id, user_id=user_id, is_archived=False)
        .order_by(SimulationChatMessage.created_at.desc())
        .first()
    )
    return latest.session_id if latest else None


@chat_bp.route('/<sim_id>/chat', methods=['GET'])
@login_required
def get_history(sim_id: str):
    _check_sim(sim_id)
    from app.models.chat import SimulationChatMessage

    session_id = request.args.get('session_id') or _active_session_id(sim_id, current_user.id)

    if not session_id:
        return jsonify({'session_id': None, 'messages': []})

    msgs = (
        SimulationChatMessage.query
        .filter_by(simulation_id=sim_id, user_id=current_user.id,
                   session_id=session_id, is_archived=False)
        .order_by(SimulationChatMessage.created_at.asc())
        .limit(50)
        .all()
    )
    return jsonify({'session_id': session_id, 'messages': [m.to_dict() for m in msgs]})


@chat_bp.route('/<sim_id>/chat', methods=['POST'])
@login_required
def send_message(sim_id: str):
    _check_sim(sim_id)
    body = request.get_json(silent=True) or {}
    user_message = (body.get('message') or '').strip()
    session_id   = (body.get('session_id') or '').strip() or None

    if not user_message:
        return jsonify({'error': 'message required'}), 400

    from app.models.chat import SimulationChatMessage
    from utils.id_gen import generate_id
    from app.services.chat_service import chat_response
    import logging as _log

    # Resolve or create session
    if not session_id:
        session_id = _active_session_id(sim_id, current_user.id) or generate_id()

    try:
        user_msg = SimulationChatMessage(
            id=generate_id(), session_id=session_id,
            simulation_id=sim_id, user_id=current_user.id,
            role='user', content=user_message,
        )
        db.session.add(user_msg)
        db.session.commit()
    except Exception as _e:
        db.session.rollback()
        _log.getLogger(__name__).error('Chat user-msg save failed: %s', _e)
        return jsonify({'error': f'DB error (migration needed?): {_e}'}), 500

    full_name = current_user.full_name or ''
    display_name = (full_name.strip().split()[0]
                    if full_name.strip()
                    else (current_user.email or 'there').split('@')[0])

    try:
        result = chat_response(sim_id, current_user.id, user_message, display_name,
                               session_id=session_id)
        return jsonify(result)
    except Exception as _e:
        _log.getLogger(__name__).error('Chat response failed: %s', _e, exc_info=True)
        return jsonify({'error': str(_e)}), 500


@chat_bp.route('/<sim_id>/chat/confirm', methods=['POST'])
@login_required
def confirm_action(sim_id: str):
    _check_sim(sim_id)
    body = request.get_json(silent=True) or {}
    message_id  = body.get('message_id')
    action_type = body.get('action_type')
    parameters  = body.get('parameters', {})

    if not message_id or not action_type:
        return jsonify({'error': 'message_id and action_type required'}), 400

    from app.services.chat_service import execute_chat_action
    try:
        result = execute_chat_action(sim_id, current_user.id, message_id, action_type, parameters)
        return jsonify({'ok': True, 'result': result})
    except Exception as e:
        return jsonify({'error': str(e)}), 400


@chat_bp.route('/<sim_id>/chat/cancel', methods=['POST'])
@login_required
def cancel_action(sim_id: str):
    _check_sim(sim_id)
    body = request.get_json(silent=True) or {}
    message_id = body.get('message_id')
    if not message_id:
        return jsonify({'error': 'message_id required'}), 400

    from app.models.chat import SimulationChatMessage
    msg = SimulationChatMessage.query.filter_by(
        id=message_id, simulation_id=sim_id, user_id=current_user.id
    ).first_or_404()
    msg.action_status = SimulationChatMessage.ACTION_CANCELLED
    db.session.commit()
    return jsonify({'ok': True})


@chat_bp.route('/<sim_id>/chat', methods=['DELETE'])
@login_required
def clear_history(sim_id: str):
    _check_sim(sim_id)
    body = request.get_json(silent=True) or {}
    session_id = (body.get('session_id') or '').strip() or None

    from app.models.chat import SimulationChatMessage
    q = SimulationChatMessage.query.filter_by(
        simulation_id=sim_id, user_id=current_user.id, is_archived=False
    )
    if session_id:
        q = q.filter_by(session_id=session_id)
    q.update({'is_archived': True})
    db.session.commit()
    return jsonify({'ok': True})
