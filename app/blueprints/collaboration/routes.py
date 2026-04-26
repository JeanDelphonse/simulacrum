from datetime import datetime, timedelta
from flask import request, jsonify
from flask_login import login_required, current_user
from app.blueprints.collaboration import collaboration_bp
from app.extensions import db
from app.models.simulation import Simulation
from app.models.collaboration import Collaboration, CollabActivity
from app.models.audit_log import AuditLog
from utils.id_gen import generate_id


@collaboration_bp.route('/api/simulations/<sim_id>/collaborators', methods=['POST'])
@login_required
def invite_collaborator(sim_id):
    sim = Simulation.query.filter_by(id=sim_id, user_id=current_user.id).first_or_404()
    data = request.get_json()
    if not data or not data.get('email') or not data.get('permission_level'):
        return jsonify({'error': 'email and permission_level are required'}), 400

    perm = data['permission_level']
    if perm not in (Collaboration.PERM_VIEWER, Collaboration.PERM_COMMENTER, Collaboration.PERM_EDITOR):
        return jsonify({'error': 'Invalid permission_level'}), 400

    existing = Collaboration.query.filter_by(
        simulation_id=sim_id,
        invitee_email=data['email'].lower(),
        revoked_at=None,
    ).first()
    if existing:
        return jsonify({'error': 'Collaborator already invited'}), 409

    collab = Collaboration(
        id=generate_id(),
        simulation_id=sim_id,
        invitee_email=data['email'].lower(),
        permission_level=perm,
        expires_at=datetime.utcnow() + timedelta(days=30),
        created_by=current_user.id,
    )
    db.session.add(collab)
    AuditLog.log('collaborator_invited', user_id=current_user.id, resource_id=sim_id,
                 metadata={'invitee': data['email'], 'permission': perm})
    db.session.commit()

    try:
        from app.services.email_service import send_collab_invite_email
        send_collab_invite_email(
            collab.invitee_email, current_user.full_name, sim.name, collab.share_token
        )
    except Exception:
        pass

    return jsonify({'id': collab.id, 'share_token': collab.share_token}), 201


@collaboration_bp.route('/api/simulations/<sim_id>/collaborators', methods=['GET'])
@login_required
def list_collaborators(sim_id):
    Simulation.query.filter_by(id=sim_id, user_id=current_user.id).first_or_404()
    collabs = Collaboration.query.filter_by(simulation_id=sim_id, revoked_at=None).all()
    return jsonify([{
        'id': c.id,
        'invitee_email': c.invitee_email,
        'permission_level': c.permission_level,
        'accepted_at': c.accepted_at.isoformat() if c.accepted_at else None,
        'expires_at': c.expires_at.isoformat(),
    } for c in collabs]), 200


@collaboration_bp.route('/api/simulations/<sim_id>/collaborators/<collab_id>', methods=['PUT'])
@login_required
def update_collaborator(sim_id, collab_id):
    Simulation.query.filter_by(id=sim_id, user_id=current_user.id).first_or_404()
    collab = Collaboration.query.filter_by(id=collab_id, simulation_id=sim_id).first_or_404()
    data = request.get_json()
    if data.get('permission_level') in (Collaboration.PERM_VIEWER, Collaboration.PERM_COMMENTER, Collaboration.PERM_EDITOR):
        collab.permission_level = data['permission_level']
        db.session.commit()
    return jsonify({'message': 'Updated', 'permission_level': collab.permission_level}), 200


@collaboration_bp.route('/api/simulations/<sim_id>/collaborators/<collab_id>', methods=['DELETE'])
@login_required
def revoke_collaborator(sim_id, collab_id):
    Simulation.query.filter_by(id=sim_id, user_id=current_user.id).first_or_404()
    collab = Collaboration.query.filter_by(id=collab_id, simulation_id=sim_id).first_or_404()
    collab.revoked_at = datetime.utcnow()
    AuditLog.log('collaborator_revoked', user_id=current_user.id, resource_id=sim_id)
    db.session.commit()
    return jsonify({'message': 'Access revoked'}), 200


@collaboration_bp.route('/collab/accept/<token>', methods=['GET'])
@login_required
def accept_invite(token):
    collab = Collaboration.query.filter_by(share_token=token, revoked_at=None).first()
    if not collab:
        return jsonify({'error': 'Invalid or expired invite link'}), 404
    if collab.expires_at < datetime.utcnow():
        return jsonify({'error': 'Invite link has expired'}), 410
    if collab.invitee_email.lower() != current_user.email.lower():
        return jsonify({'error': 'This invite was sent to a different email address'}), 403

    collab.accepted_at = datetime.utcnow()
    collab.invitee_id = current_user.id
    db.session.commit()

    from flask import redirect, url_for
    return redirect(url_for('pages.simulation_view', sim_id=collab.simulation_id))


@collaboration_bp.route('/api/simulations/<sim_id>/collaborators/<collab_id>/renew', methods=['POST'])
@login_required
def renew_collaborator(sim_id, collab_id):
    """Extend a collaboration's expiry by 30 days from today."""
    Simulation.query.filter_by(id=sim_id, user_id=current_user.id).first_or_404()
    collab = Collaboration.query.filter_by(id=collab_id, simulation_id=sim_id, revoked_at=None).first_or_404()
    collab.expires_at = datetime.utcnow() + timedelta(days=30)
    AuditLog.log('collaborator_renewed', user_id=current_user.id, resource_id=sim_id,
                 metadata={'collab_id': collab_id})
    db.session.commit()
    return jsonify({'message': 'Collaboration renewed', 'expires_at': collab.expires_at.isoformat()}), 200


@collaboration_bp.route('/api/simulations/<sim_id>/activities', methods=['POST'])
@login_required
def add_activity(sim_id):
    # Check access
    sim = Simulation.query.get(sim_id)
    if not sim:
        return jsonify({'error': 'Not found'}), 404

    is_owner = sim.user_id == current_user.id
    collab = Collaboration.query.filter_by(
        simulation_id=sim_id,
        invitee_email=current_user.email,
    ).filter(
        Collaboration.accepted_at.isnot(None),
        Collaboration.revoked_at.is_(None),
    ).first()

    if not is_owner and not collab:
        return jsonify({'error': 'Forbidden'}), 403
    if collab and collab.permission_level == Collaboration.PERM_VIEWER:
        return jsonify({'error': 'Viewers cannot add activities'}), 403

    data = request.get_json()
    activity_type = data.get('activity_type', CollabActivity.TYPE_COMMENT)
    activity = CollabActivity(
        id=generate_id(),
        simulation_id=sim_id,
        collaborator_id=current_user.id,
        collaboration_id=collab.id if collab else None,
        activity_type=activity_type,
        layer_number=data.get('layer_number'),
        content=data.get('content', ''),
    )
    db.session.add(activity)
    db.session.commit()
    return jsonify(activity.to_dict()), 201


@collaboration_bp.route('/api/simulations/<sim_id>/activities', methods=['GET'])
@login_required
def list_activities(sim_id):
    sim = Simulation.query.filter_by(id=sim_id, user_id=current_user.id).first_or_404()
    activities = CollabActivity.query.filter_by(simulation_id=sim_id).order_by(
        CollabActivity.created_at.desc()
    ).all()
    return jsonify([a.to_dict() for a in activities]), 200
