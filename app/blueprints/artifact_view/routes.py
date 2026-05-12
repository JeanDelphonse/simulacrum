"""SIM-PRD-VIEW-001 — artifact Full View page + API.

In this codebase the durable artifact identifier is `agent_actions.id`. The PRD
calls it `artifact_id`. We use them interchangeably and the URL parameter
`artifact_id` resolves to an AgentAction row.
"""
from __future__ import annotations

import logging
from datetime import datetime

from flask import (
    abort, current_app, jsonify, redirect, render_template, request, url_for,
)
from flask_login import current_user, login_required

from app.blueprints.artifact_view import artifact_view_bp
from app.extensions import db
from app.models.agent_action import AgentAction
from app.models.artifact import ArtifactVersion
from app.models.simulation import Simulation
from app.services import artifact_edit_summary as summary_helper
from utils.id_gen import generate_id

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_artifact(sim_id: str, artifact_id: str):
    """Return (simulation, action, current_version) or abort 404 / 403."""
    sim = Simulation.query.get_or_404(sim_id)
    if sim.user_id != current_user.id:
        # Allow accepted collaborators
        from app.models.collaboration import Collaboration
        is_collab = Collaboration.query.filter_by(
            simulation_id=sim_id, invitee_email=current_user.email,
        ).filter(
            Collaboration.accepted_at.isnot(None),
            Collaboration.revoked_at.is_(None),
        ).first()
        if not is_collab:
            abort(403)
    action = AgentAction.query.filter_by(id=artifact_id, simulation_id=sim_id).first()
    if not action:
        abort(404)
    current = ArtifactVersion.current_for(action.id)
    return sim, action, current


_ACTION_LABELS = {
    'rate_card':                  'Your rate card',
    'linkedin_optimization':      'LinkedIn rewrite',
    'linkedin_optimize':          'LinkedIn rewrite',
    'booking_page':               'Your booking page',
    'cold_email_campaign':        'Cold email campaign',
    'consulting_outreach':        'Consulting outreach emails',
    'outreach_email':             'Outreach emails',
    'role_search':                'Fractional role search',
    'consulting_proposal':        'Consulting proposal',
    'sow_template':               'Statement of work',
    'agreement_template':         'Consulting agreement',
    'consulting_agreement':       'Consulting agreement',
    'referral_network':           'Referral network',
    'negotiation_script':         'Rate negotiation script',
    'rate_negotiation':           'Rate negotiation script',
    'workshop_curriculum':        'Workshop curriculum',
    'workshop_content':           'Workshop content',
    'corporate_training_pitch':   'Corporate training pitch',
    'corporate_training_proposal': 'Corporate training proposal',
    'speaking_proposals':         'Speaking proposals',
    'speaker_fee_rider':          'Speaker fee rider',
    'group_coaching_curriculum':  'Group coaching curriculum',
    'coaching_curriculum':        'Coaching curriculum',
    'waitlist_landing_page':      'Waitlist landing page',
    'alumni_reactivation':        'Alumni reactivation',
    'roi_calculator':             'ROI calculator',
    'workshop_roi':               'Workshop ROI calculator',
}


def _plain_name(action_type: str) -> str:
    return _ACTION_LABELS.get(action_type, action_type.replace('_', ' ').title())


# ---------------------------------------------------------------------------
# Page route — Full View
# ---------------------------------------------------------------------------

@artifact_view_bp.route('/simulations/<sim_id>/artifacts/<artifact_id>')
@login_required
def page_full_view(sim_id, artifact_id):
    sim, action, current = _resolve_artifact(sim_id, artifact_id)
    if current is None:
        # No version yet — likely action still pending. Send back to the GCC.
        return redirect(url_for('pages.layer6_view', sim_id=sim_id))

    history = ArtifactVersion.history_for(action.id)
    return render_template(
        'simulations/artifact_view.html',
        sim=sim,
        action=action,
        current=current,
        history=history,
        artifact_name=_plain_name(action.action_type),
    )


# ---------------------------------------------------------------------------
# API — read
# ---------------------------------------------------------------------------

@artifact_view_bp.route('/api/artifacts/<artifact_id>', methods=['GET'])
@login_required
def api_get(artifact_id):
    action = AgentAction.query.get_or_404(artifact_id)
    sim = Simulation.query.get(action.simulation_id)
    if not sim or sim.user_id != current_user.id:
        return jsonify({'error': 'forbidden'}), 403
    current = ArtifactVersion.current_for(artifact_id)
    if not current:
        return jsonify({'error': 'no_versions'}), 404

    return jsonify({
        'artifact_id':         action.id,
        'action_type':         action.action_type,
        'plain_name':          _plain_name(action.action_type),
        'simulation_id':       action.simulation_id,
        'layer_number':        action.layer_number,
        'status':              action.status,
        'archived_at':         action.archived_at.isoformat() if action.archived_at else None,
        'current_version':     current.version_number,
        'all_versions_count':  ArtifactVersion.query.filter_by(action_id=artifact_id).count(),
        'current_payload':     current.content or '',
        'draft_payload':       current.draft_content,
        'draft_updated_at':    current.draft_updated_at.isoformat() if current.draft_updated_at else None,
        'edited_by':           current.edited_by,
        'edit_summary':        current.edit_summary,
        'edited_at':           current.edited_at.isoformat() if current.edited_at else None,
        'created_at':          current.created_at.isoformat(),
    })


# ---------------------------------------------------------------------------
# API — Save as new version (FR-VIEW-09)
# ---------------------------------------------------------------------------

@artifact_view_bp.route('/api/artifacts/<artifact_id>/save', methods=['POST'])
@login_required
def api_save(artifact_id):
    body = request.get_json(silent=True) or {}
    payload = body.get('payload')
    if payload is None:
        return jsonify({'error': 'payload_required'}), 400

    action = AgentAction.query.get_or_404(artifact_id)
    sim = Simulation.query.get(action.simulation_id)
    if not sim or sim.user_id != current_user.id:
        return jsonify({'error': 'forbidden'}), 403

    new_text = payload if isinstance(payload, str) else str(payload)

    current = ArtifactVersion.current_for(artifact_id)
    if not current:
        return jsonify({'error': 'no_current_version'}), 409

    if (current.content or '') == new_text:
        # No-op save — clear any draft and return.
        current.draft_content = None
        current.draft_updated_at = None
        db.session.commit()
        return jsonify({
            'version_number': current.version_number,
            'edited_at':      (current.edited_at or current.created_at).isoformat(),
            'no_change':      True,
        })

    summary = summary_helper.for_user(current.content, new_text)

    current.is_current = False
    new_version_number = current.version_number + 1
    new_version = ArtifactVersion(
        id=generate_id(),
        action_id=action.id,
        simulation_id=action.simulation_id,
        layer_number=action.layer_number,
        action_type=action.action_type,
        version_number=new_version_number,
        version_label=f'v{new_version_number} — {datetime.utcnow().strftime("%b %d")}',
        content=new_text,
        edited_by=ArtifactVersion.EDITED_BY_USER,
        edit_summary=summary,
        parent_version_id=current.id,
        edited_at=datetime.utcnow(),
        is_current=True,
        created_by='user',
    )
    db.session.add(new_version)

    # Mirror the latest content to AgentAction.artifact for downstream code
    # paths that read action.artifact directly.
    action.artifact = new_text

    db.session.commit()

    return jsonify({
        'version_number': new_version.version_number,
        'edited_at':      new_version.edited_at.isoformat(),
        'edit_summary':   new_version.edit_summary,
    })


# ---------------------------------------------------------------------------
# API — Auto-save draft (FR-VIEW-08)
# ---------------------------------------------------------------------------

@artifact_view_bp.route('/api/artifacts/<artifact_id>/draft', methods=['POST'])
@login_required
def api_draft(artifact_id):
    body = request.get_json(silent=True) or {}
    payload = body.get('payload')
    if payload is None:
        return jsonify({'error': 'payload_required'}), 400

    action = AgentAction.query.get_or_404(artifact_id)
    sim = Simulation.query.get(action.simulation_id)
    if not sim or sim.user_id != current_user.id:
        return jsonify({'error': 'forbidden'}), 403

    current = ArtifactVersion.current_for(artifact_id)
    if not current:
        return jsonify({'error': 'no_current_version'}), 409

    text = payload if isinstance(payload, str) else str(payload)
    if (current.content or '') == text:
        current.draft_content = None
        current.draft_updated_at = None
    else:
        current.draft_content = text
        current.draft_updated_at = datetime.utcnow()
    db.session.commit()
    return jsonify({
        'saved_at': current.draft_updated_at.isoformat() if current.draft_updated_at else None,
        'has_draft': bool(current.draft_content),
    })


@artifact_view_bp.route('/api/artifacts/<artifact_id>/draft', methods=['DELETE'])
@login_required
def api_draft_discard(artifact_id):
    action = AgentAction.query.get_or_404(artifact_id)
    sim = Simulation.query.get(action.simulation_id)
    if not sim or sim.user_id != current_user.id:
        return jsonify({'error': 'forbidden'}), 403

    current = ArtifactVersion.current_for(artifact_id)
    if current:
        current.draft_content = None
        current.draft_updated_at = None
        db.session.commit()
    return jsonify({'status': 'discarded'})


# ---------------------------------------------------------------------------
# API — Versions (FR-VIEW-10, FR-VIEW-11)
# ---------------------------------------------------------------------------

@artifact_view_bp.route('/api/artifacts/<artifact_id>/versions', methods=['GET'])
@login_required
def api_versions_list(artifact_id):
    action = AgentAction.query.get_or_404(artifact_id)
    sim = Simulation.query.get(action.simulation_id)
    if not sim or sim.user_id != current_user.id:
        return jsonify({'error': 'forbidden'}), 403

    versions = ArtifactVersion.history_for(artifact_id)
    return jsonify({
        'versions': [{
            'id':              v.id,
            'version_number':  v.version_number,
            'version_label':   v.version_label,
            'edited_by':       v.edited_by or v.created_by,
            'edited_at':       (v.edited_at or v.created_at).isoformat(),
            'edit_summary':    v.edit_summary or v.change_summary,
            'is_current':      bool(v.is_current),
        } for v in versions]
    })


@artifact_view_bp.route('/api/artifacts/<artifact_id>/versions/<int:version_number>', methods=['GET'])
@login_required
def api_version_get(artifact_id, version_number):
    action = AgentAction.query.get_or_404(artifact_id)
    sim = Simulation.query.get(action.simulation_id)
    if not sim or sim.user_id != current_user.id:
        return jsonify({'error': 'forbidden'}), 403
    v = ArtifactVersion.query.filter_by(action_id=artifact_id, version_number=version_number).first()
    if not v:
        return jsonify({'error': 'not_found'}), 404
    return jsonify({
        'version_number':  v.version_number,
        'payload':         v.content or '',
        'edited_by':       v.edited_by or v.created_by,
        'edited_at':       (v.edited_at or v.created_at).isoformat(),
        'edit_summary':    v.edit_summary or v.change_summary,
        'is_current':      bool(v.is_current),
    })


@artifact_view_bp.route('/api/artifacts/<artifact_id>/versions/<int:version_number>/restore', methods=['POST'])
@login_required
def api_version_restore(artifact_id, version_number):
    action = AgentAction.query.get_or_404(artifact_id)
    sim = Simulation.query.get(action.simulation_id)
    if not sim or sim.user_id != current_user.id:
        return jsonify({'error': 'forbidden'}), 403

    target = ArtifactVersion.query.filter_by(action_id=artifact_id, version_number=version_number).first()
    if not target:
        return jsonify({'error': 'not_found'}), 404
    if target.is_current:
        return jsonify({'error': 'already_current'}), 409

    current = ArtifactVersion.current_for(artifact_id)
    if not current:
        return jsonify({'error': 'no_current_version'}), 409

    current.is_current = False
    new_n = current.version_number + 1
    new_version = ArtifactVersion(
        id=generate_id(),
        action_id=action.id,
        simulation_id=action.simulation_id,
        layer_number=action.layer_number,
        action_type=action.action_type,
        version_number=new_n,
        version_label=f'v{new_n} — {datetime.utcnow().strftime("%b %d")}',
        content=target.content,
        edited_by=ArtifactVersion.EDITED_BY_USER,
        edit_summary=summary_helper.for_restore(version_number),
        parent_version_id=target.id,
        edited_at=datetime.utcnow(),
        is_current=True,
        created_by='user',
    )
    db.session.add(new_version)
    action.artifact = target.content
    db.session.commit()
    return jsonify({
        'version_number': new_n,
        'edit_summary':   new_version.edit_summary,
    })


# ---------------------------------------------------------------------------
# API — Re-run (FR-VIEW-14)
# ---------------------------------------------------------------------------

@artifact_view_bp.route('/api/artifacts/<artifact_id>/rerun', methods=['POST'])
@login_required
def api_rerun(artifact_id):
    action = AgentAction.query.get_or_404(artifact_id)
    sim = Simulation.query.get(action.simulation_id)
    if not sim or sim.user_id != current_user.id:
        return jsonify({'error': 'forbidden'}), 403

    body = request.get_json(silent=True) or {}
    custom_inputs = body.get('custom_prefill_inputs')
    if isinstance(custom_inputs, dict) and custom_inputs:
        merged = dict(action.user_inputs or {})
        merged.update(custom_inputs)
        action.user_inputs = merged

    action.status = AgentAction.STATUS_PENDING
    action.error_message = None
    db.session.commit()

    try:
        from app.tasks.agent import execute_agent_action_task
        execute_agent_action_task.delay(action.id)
    except Exception as exc:
        logger.error('rerun dispatch failed action=%s: %s', action.id, exc)
        return jsonify({'error': 'dispatch_failed', 'detail': str(exc)}), 500

    return jsonify({'status': 'rerun_dispatched', 'action_id': action.id})


# ---------------------------------------------------------------------------
# API — Archive / Restore from archive (FR-VIEW-15)
# ---------------------------------------------------------------------------

@artifact_view_bp.route('/api/artifacts/<artifact_id>/archive', methods=['POST'])
@login_required
def api_archive(artifact_id):
    action = AgentAction.query.get_or_404(artifact_id)
    sim = Simulation.query.get(action.simulation_id)
    if not sim or sim.user_id != current_user.id:
        return jsonify({'error': 'forbidden'}), 403
    if action.archived_at is None:
        action.archived_at = datetime.utcnow()
        db.session.commit()
    return jsonify({'archived_at': action.archived_at.isoformat()})


@artifact_view_bp.route('/api/artifacts/<artifact_id>/unarchive', methods=['POST'])
@login_required
def api_unarchive(artifact_id):
    action = AgentAction.query.get_or_404(artifact_id)
    sim = Simulation.query.get(action.simulation_id)
    if not sim or sim.user_id != current_user.id:
        return jsonify({'error': 'forbidden'}), 403
    action.archived_at = None
    db.session.commit()
    return jsonify({'status': 'restored'})
