"""Advisor API — /api/advisor/*
Notes, flags, and suggestions for partner coaching workflow.
"""
from datetime import datetime
from flask import request, jsonify
from flask_login import login_required, current_user
from app.blueprints.advisor import advisor_bp
from app.extensions import db
from app.models.partner import (
    ReferralPartner, AdvisorAccess, AdvisorNote, AdvisorFlag,
)
from utils.id_gen import generate_id


def _get_partner():
    return ReferralPartner.query.filter_by(
        user_id=current_user.id, status=ReferralPartner.STATUS_ACTIVE,
    ).first()


def _get_access(partner, sim_id):
    return AdvisorAccess.query.filter_by(
        partner_id=partner.id, simulation_id=sim_id, revoked_at=None,
    ).first()


# ---------------------------------------------------------------------------
# Notes
# ---------------------------------------------------------------------------

@advisor_bp.route('/notes/<sim_id>', methods=['GET'])
@login_required
def list_notes(sim_id):
    partner = _get_partner()
    if not partner:
        return jsonify({'error': 'Active partner account required'}), 403
    access = _get_access(partner, sim_id)
    if not access:
        return jsonify({'error': 'Access not found or revoked'}), 404
    layer = request.args.get('layer')
    q = AdvisorNote.query.filter_by(advisor_access_id=access.id, simulation_id=sim_id)
    if layer is not None:
        q = q.filter_by(layer_number=int(layer))
    notes = q.order_by(AdvisorNote.created_at.desc()).all()
    return jsonify([n.to_dict() for n in notes]), 200


@advisor_bp.route('/notes', methods=['POST'])
@login_required
def create_note():
    partner = _get_partner()
    if not partner:
        return jsonify({'error': 'Active partner account required'}), 403
    data = request.get_json()
    if not data or not data.get('simulation_id') or not data.get('note_text', '').strip():
        return jsonify({'error': 'simulation_id and note_text are required'}), 400
    sim_id = data['simulation_id']
    access = _get_access(partner, sim_id)
    if not access:
        return jsonify({'error': 'Access not found or revoked'}), 404
    note = AdvisorNote(
        id=generate_id(),
        advisor_access_id=access.id,
        simulation_id=sim_id,
        layer_number=data.get('layer_number'),
        note_text=data['note_text'].strip()[:500],
        is_shared=False,
    )
    db.session.add(note)
    db.session.commit()
    return jsonify(note.to_dict()), 201


@advisor_bp.route('/notes/<note_id>', methods=['DELETE'])
@login_required
def delete_note(note_id):
    partner = _get_partner()
    if not partner:
        return jsonify({'error': 'Active partner account required'}), 403
    note = AdvisorNote.query.get_or_404(note_id)
    access = AdvisorAccess.query.get(note.advisor_access_id)
    if not access or access.partner_id != partner.id:
        return jsonify({'error': 'Not authorized'}), 403
    db.session.delete(note)
    db.session.commit()
    return jsonify({'message': 'Note deleted'}), 200


# ---------------------------------------------------------------------------
# Flags
# ---------------------------------------------------------------------------

@advisor_bp.route('/flags', methods=['POST'])
@login_required
def create_flag():
    partner = _get_partner()
    if not partner:
        return jsonify({'error': 'Active partner account required'}), 403
    data = request.get_json()
    if not data or not data.get('simulation_id') or not data.get('action_type'):
        return jsonify({'error': 'simulation_id and action_type are required'}), 400
    sim_id = data['simulation_id']
    access = _get_access(partner, sim_id)
    if not access:
        return jsonify({'error': 'Access not found or revoked'}), 404
    flag = AdvisorFlag(
        id=generate_id(),
        advisor_access_id=access.id,
        simulation_id=sim_id,
        action_type=data['action_type'],
        action_id=data.get('action_id'),
        message=(data.get('message') or '')[:300] or None,
    )
    db.session.add(flag)
    db.session.commit()
    return jsonify(flag.to_dict()), 201


@advisor_bp.route('/flags/<flag_id>', methods=['DELETE'])
@login_required
def delete_flag(flag_id):
    partner = _get_partner()
    if not partner:
        return jsonify({'error': 'Active partner account required'}), 403
    flag = AdvisorFlag.query.get_or_404(flag_id)
    access = AdvisorAccess.query.get(flag.advisor_access_id)
    if not access or access.partner_id != partner.id:
        return jsonify({'error': 'Not authorized'}), 403
    db.session.delete(flag)
    db.session.commit()
    return jsonify({'message': 'Flag removed'}), 200


@advisor_bp.route('/flags/<flag_id>/dismiss', methods=['POST'])
@login_required
def dismiss_flag(flag_id):
    """Client dismisses a flag notification."""
    flag = AdvisorFlag.query.get_or_404(flag_id)
    access = AdvisorAccess.query.get(flag.advisor_access_id)
    if not access or access.granted_by != current_user.id:
        return jsonify({'error': 'Not authorized'}), 403
    flag.dismissed_at = datetime.utcnow()
    db.session.commit()
    return jsonify({'message': 'Flag dismissed'}), 200


# ---------------------------------------------------------------------------
# Suggestions (shared notes with suggestion_type='next_step')
# ---------------------------------------------------------------------------

@advisor_bp.route('/suggestions', methods=['POST'])
@login_required
def publish_suggestion():
    partner = _get_partner()
    if not partner:
        return jsonify({'error': 'Active partner account required'}), 403
    data = request.get_json()
    if not data or not data.get('simulation_id') or not data.get('layer_number') or not data.get('message', '').strip():
        return jsonify({'error': 'simulation_id, layer_number, and message are required'}), 400
    sim_id = data['simulation_id']
    layer_number = int(data['layer_number'])
    access = _get_access(partner, sim_id)
    if not access:
        return jsonify({'error': 'Access not found or revoked'}), 404

    # Replace existing active suggestion for this layer (max one per layer)
    AdvisorNote.query.filter_by(
        advisor_access_id=access.id,
        simulation_id=sim_id,
        layer_number=layer_number,
        is_shared=True,
        suggestion_type='next_step',
    ).update({'is_shared': False, 'updated_at': datetime.utcnow()})

    suggestion = AdvisorNote(
        id=generate_id(),
        advisor_access_id=access.id,
        simulation_id=sim_id,
        layer_number=layer_number,
        note_text=data['message'].strip()[:200],
        is_shared=True,
        suggestion_type='next_step',
        is_urgent=bool(data.get('is_urgent', False)),
    )
    db.session.add(suggestion)
    db.session.commit()
    return jsonify(suggestion.to_dict()), 201


@advisor_bp.route('/suggestions/<note_id>', methods=['PUT'])
@login_required
def edit_suggestion(note_id):
    partner = _get_partner()
    if not partner:
        return jsonify({'error': 'Active partner account required'}), 403
    note = AdvisorNote.query.get_or_404(note_id)
    access = AdvisorAccess.query.get(note.advisor_access_id)
    if not access or access.partner_id != partner.id:
        return jsonify({'error': 'Not authorized'}), 403
    data = request.get_json()
    if data.get('message', '').strip():
        note.note_text = data['message'].strip()[:200]
    if 'is_urgent' in data:
        note.is_urgent = bool(data['is_urgent'])
    note.updated_at = datetime.utcnow()
    db.session.commit()
    return jsonify(note.to_dict()), 200


@advisor_bp.route('/suggestions/<note_id>', methods=['DELETE'])
@login_required
def remove_suggestion(note_id):
    partner = _get_partner()
    if not partner:
        return jsonify({'error': 'Active partner account required'}), 403
    note = AdvisorNote.query.get_or_404(note_id)
    access = AdvisorAccess.query.get(note.advisor_access_id)
    if not access or access.partner_id != partner.id:
        return jsonify({'error': 'Not authorized'}), 403
    note.is_shared = False
    note.updated_at = datetime.utcnow()
    db.session.commit()
    return jsonify({'message': 'Suggestion removed from client view'}), 200
