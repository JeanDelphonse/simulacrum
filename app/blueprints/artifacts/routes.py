"""Artifact Intelligence API routes.

Endpoints:
  Pre-fill
    GET  /api/simulations/<sim_id>/actions/<action_type>/prefill
    POST /api/simulations/<sim_id>/prefill-corrections

  Artifact Versioning
    GET  /api/simulations/<sim_id>/layers/<layer_num>/actions/<action_id>/versions
    POST /api/simulations/<sim_id>/layers/<layer_num>/actions/<action_id>/versions/<version_id>/restore
    GET  /api/simulations/<sim_id>/layers/<layer_num>/actions/<action_id>/versions/compare

  Artifact Bundling
    GET  /api/simulations/<sim_id>/bundles
    POST /api/simulations/<sim_id>/bundles
    GET  /api/simulations/<sim_id>/bundles/<bundle_id>
    GET  /api/simulations/<sim_id>/bundles/<bundle_id>/download

  Artifact Dependencies
    GET  /api/simulations/<sim_id>/artifact-dependencies
"""
from typing import Optional
from datetime import datetime, timedelta

from flask import request, jsonify, current_app
from flask_login import login_required, current_user

from app.blueprints.artifacts import artifacts_bp
from app.extensions import db
from app.models.simulation import Simulation
from app.models.agent_action import AgentAction
from app.models.resume import Resume
from app.models.artifact import (
    PrefillCorrection, ArtifactVersion, ArtifactBundle, ArtifactDependency,
)
from utils.id_gen import generate_id


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _check_sim_access(sim_id):
    """Return (sim, is_editor) or (None, None) if not found / not accessible."""
    sim = Simulation.query.get(sim_id)
    if not sim:
        return None, None
    if sim.user_id == current_user.id:
        return sim, True
    from app.models.collaboration import Collaboration
    collab = Collaboration.query.filter_by(
        simulation_id=sim_id,
        invitee_email=current_user.email,
    ).filter(
        Collaboration.accepted_at.isnot(None),
        Collaboration.revoked_at.is_(None),
    ).first()
    if collab:
        return sim, collab.permission_level == 'editor'
    return None, None


# ---------------------------------------------------------------------------
# Pre-fill Engine
# ---------------------------------------------------------------------------

@artifacts_bp.route('/<sim_id>/actions/<action_type>/prefill', methods=['GET'])
@login_required
def get_prefill(sim_id, action_type):
    """Return confidence-scored pre-fill values for every field in an action form.

    Query params:
      layer_num (int, required) — the layer the action belongs to
    """
    sim, _ = _check_sim_access(sim_id)
    if not sim:
        return jsonify({'error': 'Not found'}), 404

    try:
        layer_num = int(request.args.get('layer_num', 0))
    except ValueError:
        return jsonify({'error': 'layer_num must be an integer'}), 400

    if layer_num not in range(1, 6):
        return jsonify({'error': 'layer_num must be 1–5'}), 400

    resume = Resume.query.get(sim.resume_id) if sim.resume_id else None

    from app.services.prefill_engine import PrefillEngine
    engine = PrefillEngine(
        simulation=sim,
        resume=resume,
        action_type=action_type,
        layer_number=layer_num,
    )
    payload = engine.generate()
    return jsonify({
        'simulation_id': sim_id,
        'action_type': action_type,
        'layer_number': layer_num,
        'prefill': payload,
        'generated_at': datetime.utcnow().isoformat(),
    }), 200


@artifacts_bp.route('/<sim_id>/prefill-corrections', methods=['POST'])
@login_required
def capture_prefill_corrections(sim_id):
    """Record corrections the user made to pre-filled form fields before submitting.

    Body:
      {
        "action_type": "cold_email_campaign",
        "corrections": [
          {"field_name": "target_company_size", "prefilled_value": "SMB", "corrected_value": "Enterprise",
           "prefill_source": "resume", "confidence_level": "medium"}
        ]
      }
    """
    sim, _ = _check_sim_access(sim_id)
    if not sim:
        return jsonify({'error': 'Not found'}), 404

    data = request.get_json() or {}
    action_type = data.get('action_type')
    corrections = data.get('corrections', [])

    if not action_type:
        return jsonify({'error': 'action_type is required'}), 400
    if not corrections:
        return jsonify({'message': 'No corrections to record'}), 200

    saved = 0
    for corr in corrections:
        field_name = corr.get('field_name')
        corrected_value = corr.get('corrected_value')
        if not field_name:
            continue
        pc = PrefillCorrection(
            id=generate_id(),
            simulation_id=sim_id,
            user_id=current_user.id,
            action_type=action_type,
            field_name=field_name,
            prefilled_value=corr.get('prefilled_value'),
            corrected_value=corrected_value,
            prefill_source=corr.get('prefill_source'),
            confidence_level=corr.get('confidence_level'),
        )
        db.session.add(pc)
        saved += 1

    db.session.commit()
    return jsonify({'message': f'{saved} correction(s) recorded'}), 201


# ---------------------------------------------------------------------------
# Artifact Versioning
# ---------------------------------------------------------------------------

@artifacts_bp.route('/<sim_id>/layers/<int:layer_num>/actions/<action_id>/versions', methods=['GET'])
@login_required
def list_artifact_versions(sim_id, layer_num, action_id):
    """Return all versions of an artifact, newest first."""
    sim, _ = _check_sim_access(sim_id)
    if not sim:
        return jsonify({'error': 'Not found'}), 404

    action = AgentAction.query.filter_by(
        id=action_id, simulation_id=sim_id, layer_number=layer_num,
    ).first_or_404()

    versions = ArtifactVersion.query.filter_by(
        action_id=action_id,
    ).order_by(ArtifactVersion.version_number.desc()).all()

    return jsonify({
        'action_id': action_id,
        'action_type': action.action_type,
        'versions': [v.to_dict() for v in versions],
        'total': len(versions),
    }), 200


@artifacts_bp.route(
    '/<sim_id>/layers/<int:layer_num>/actions/<action_id>/versions/<version_id>/restore',
    methods=['POST'],
)
@login_required
def restore_artifact_version(sim_id, layer_num, action_id, version_id):
    """Promote a prior version to is_current=True without deleting any other version."""
    sim, is_editor = _check_sim_access(sim_id)
    if not sim:
        return jsonify({'error': 'Not found'}), 404
    if not is_editor:
        return jsonify({'error': 'Editor permission required'}), 403

    target = ArtifactVersion.query.filter_by(
        id=version_id, action_id=action_id,
    ).first_or_404()

    # Demote all other versions
    ArtifactVersion.query.filter_by(action_id=action_id).filter(
        ArtifactVersion.id != version_id,
    ).update({'is_current': False})

    target.is_current = True

    # Sync the live artifact text back to AgentAction
    action = AgentAction.query.filter_by(
        id=action_id, simulation_id=sim_id, layer_number=layer_num,
    ).first_or_404()
    action.artifact = target.content

    db.session.commit()

    return jsonify({
        'message': f'v{target.version_number} restored as current version.',
        'version': target.to_dict(),
    }), 200


@artifacts_bp.route(
    '/<sim_id>/layers/<int:layer_num>/actions/<action_id>/versions/compare',
    methods=['GET'],
)
@login_required
def compare_artifact_versions(sim_id, layer_num, action_id):
    """Side-by-side comparison of two versions.

    Query params:
      v1 (int) — first version number
      v2 (int) — second version number
    """
    sim, _ = _check_sim_access(sim_id)
    if not sim:
        return jsonify({'error': 'Not found'}), 404

    try:
        v1_num = int(request.args.get('v1', 0))
        v2_num = int(request.args.get('v2', 0))
    except ValueError:
        return jsonify({'error': 'v1 and v2 must be integers'}), 400

    if not v1_num or not v2_num:
        return jsonify({'error': 'v1 and v2 query params required'}), 400

    v1 = ArtifactVersion.query.filter_by(action_id=action_id, version_number=v1_num).first()
    v2 = ArtifactVersion.query.filter_by(action_id=action_id, version_number=v2_num).first()

    if not v1 or not v2:
        return jsonify({'error': 'One or both versions not found'}), 404

    # Word-level diff for text artifacts
    diff = None
    if v1.content and v2.content:
        diff = _word_diff(v1.content, v2.content)

    return jsonify({
        'action_id': action_id,
        'version_a': v1.to_dict(),
        'version_b': v2.to_dict(),
        'diff': diff,
        'inputs_diff': _inputs_diff(v1.prefill_inputs, v2.prefill_inputs),
    }), 200


def _word_diff(text_a: str, text_b: str) -> list[dict]:
    """Return a list of {type, text} tokens: 'equal', 'insert', 'delete'."""
    import difflib
    words_a = text_a.split()
    words_b = text_b.split()
    matcher = difflib.SequenceMatcher(None, words_a, words_b)
    result = []
    for opcode, a0, a1, b0, b1 in matcher.get_opcodes():
        if opcode == 'equal':
            result.append({'type': 'equal', 'text': ' '.join(words_a[a0:a1])})
        elif opcode in ('replace', 'delete'):
            result.append({'type': 'delete', 'text': ' '.join(words_a[a0:a1])})
        if opcode in ('replace', 'insert'):
            result.append({'type': 'insert', 'text': ' '.join(words_b[b0:b1])})
    return result


def _inputs_diff(inputs_a: dict, inputs_b: dict) -> list[dict]:
    """Return field-level diff between two prefill_inputs dicts."""
    all_keys = set(inputs_a) | set(inputs_b)
    diffs = []
    for key in sorted(all_keys):
        va = inputs_a.get(key)
        vb = inputs_b.get(key)
        if va != vb:
            diffs.append({'field': key, 'before': va, 'after': vb})
    return diffs


# ---------------------------------------------------------------------------
# Artifact Bundling
# ---------------------------------------------------------------------------

@artifacts_bp.route('/<sim_id>/bundles', methods=['GET'])
@login_required
def list_bundles(sim_id):
    sim, _ = _check_sim_access(sim_id)
    if not sim:
        return jsonify({'error': 'Not found'}), 404

    bundles = ArtifactBundle.query.filter_by(
        simulation_id=sim_id,
    ).order_by(ArtifactBundle.created_at.desc()).all()

    # Expire stale bundles
    now = datetime.utcnow()
    updated = False
    for b in bundles:
        if b.expires_at and b.expires_at < now and b.status == ArtifactBundle.STATUS_READY:
            b.status = ArtifactBundle.STATUS_EXPIRED
            updated = True
    if updated:
        db.session.commit()

    return jsonify([b.to_dict() for b in bundles]), 200


@artifacts_bp.route('/<sim_id>/bundles', methods=['POST'])
@login_required
def create_bundle(sim_id):
    """Queue a Celery bundle compilation task.

    Body:
      {
        "bundle_type": "layer_portfolio",
        "bundle_name": "My Q2 Strategy Package",
        "audience": "Investor",
        "layer_number": 3,           (for layer_portfolio bundles)
        "artifact_ids": ["abc", ...] (for custom bundles; optional, defaults to all current)
      }
    """
    sim, is_editor = _check_sim_access(sim_id)
    if not sim:
        return jsonify({'error': 'Not found'}), 404
    if not is_editor:
        return jsonify({'error': 'Editor permission required'}), 403

    data = request.get_json() or {}
    bundle_type = data.get('bundle_type', ArtifactBundle.BUNDLE_LAYER_PORTFOLIO)

    if bundle_type not in ArtifactBundle.VALID_BUNDLE_TYPES:
        return jsonify({'error': f'Invalid bundle_type. Choose from: {", ".join(ArtifactBundle.VALID_BUNDLE_TYPES)}'}), 400

    layer_number = data.get('layer_number')
    if bundle_type == ArtifactBundle.BUNDLE_LAYER_PORTFOLIO and not layer_number:
        return jsonify({'error': 'layer_number is required for layer_portfolio bundles'}), 400

    # Collect artifact_ids: explicit list or all current versions for the layer
    artifact_ids = data.get('artifact_ids') or []
    if not artifact_ids:
        query = AgentAction.query.filter_by(
            simulation_id=sim_id,
            status=AgentAction.STATUS_COMPLETE,
        )
        if layer_number:
            query = query.filter_by(layer_number=layer_number)
        artifact_ids = [a.id for a in query.all()]

    if not artifact_ids:
        return jsonify({'error': 'No completed actions found to bundle'}), 400

    bundle = ArtifactBundle(
        id=generate_id(),
        simulation_id=sim_id,
        user_id=current_user.id,
        bundle_type=bundle_type,
        bundle_name=data.get('bundle_name') or _default_bundle_name(sim, bundle_type, layer_number),
        audience=data.get('audience'),
        layer_number=layer_number,
        status=ArtifactBundle.STATUS_PENDING,
        expires_at=datetime.utcnow() + timedelta(days=7),
    )
    bundle.artifact_ids = artifact_ids
    db.session.add(bundle)
    db.session.commit()

    # Queue Celery compilation task
    _queue_bundle_task(bundle.id)

    return jsonify({
        'bundle': bundle.to_dict(),
        'message': 'Bundle compilation queued. You will be notified when it is ready.',
    }), 202


@artifacts_bp.route('/<sim_id>/bundles/<bundle_id>', methods=['GET'])
@login_required
def get_bundle(sim_id, bundle_id):
    sim, _ = _check_sim_access(sim_id)
    if not sim:
        return jsonify({'error': 'Not found'}), 404

    bundle = ArtifactBundle.query.filter_by(
        id=bundle_id, simulation_id=sim_id,
    ).first_or_404()

    return jsonify(bundle.to_dict()), 200


@artifacts_bp.route('/<sim_id>/bundles/<bundle_id>/download', methods=['GET'])
@login_required
def download_bundle(sim_id, bundle_id):
    """Stream the compiled ZIP to the user."""
    sim, _ = _check_sim_access(sim_id)
    if not sim:
        return jsonify({'error': 'Not found'}), 404

    bundle = ArtifactBundle.query.filter_by(
        id=bundle_id, simulation_id=sim_id,
    ).first_or_404()

    if bundle.status == ArtifactBundle.STATUS_EXPIRED:
        return jsonify({'error': 'Bundle has expired. Regenerate it to download.'}), 410

    if bundle.status != ArtifactBundle.STATUS_READY:
        return jsonify({'error': f'Bundle is not ready yet (status: {bundle.status})'}), 409

    if not bundle.file_path:
        return jsonify({'error': 'Bundle file not found'}), 404

    import os
    from flask import send_file

    abs_path = bundle.file_path
    if not os.path.isabs(abs_path):
        abs_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))),
            abs_path,
        )

    if not os.path.exists(abs_path):
        return jsonify({'error': 'Bundle file missing from storage'}), 404

    bundle.download_count = (bundle.download_count or 0) + 1
    db.session.commit()

    filename = os.path.basename(abs_path)
    return send_file(abs_path, as_attachment=True, download_name=filename,
                     mimetype='application/zip')


# ---------------------------------------------------------------------------
# Artifact Dependencies
# ---------------------------------------------------------------------------

@artifacts_bp.route('/<sim_id>/artifact-dependencies', methods=['GET'])
@login_required
def list_artifact_dependencies(sim_id):
    """Return the full artifact dependency graph for a simulation."""
    sim, _ = _check_sim_access(sim_id)
    if not sim:
        return jsonify({'error': 'Not found'}), 404

    deps = ArtifactDependency.query.filter_by(
        simulation_id=sim_id,
    ).order_by(ArtifactDependency.created_at).all()

    stale_deps = [d for d in deps if d.is_stale]

    return jsonify({
        'simulation_id': sim_id,
        'dependencies': [d.to_dict() for d in deps],
        'stale_count': len(stale_deps),
        'stale_action_types': list({d.downstream_action_type for d in stale_deps}),
    }), 200


@artifacts_bp.route('/<sim_id>/artifact-dependencies/seed', methods=['POST'])
@login_required
def seed_dependencies(sim_id):
    """Seed artifact dependencies from the static config file (idempotent)."""
    sim, is_editor = _check_sim_access(sim_id)
    if not sim:
        return jsonify({'error': 'Not found'}), 404
    if not is_editor:
        return jsonify({'error': 'Editor permission required'}), 403

    from app.services.prefill_engine import seed_artifact_dependencies
    seed_artifact_dependencies(sim_id)
    count = ArtifactDependency.query.filter_by(simulation_id=sim_id).count()
    return jsonify({'message': f'Dependencies seeded. {count} total relationships.'}), 200


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _default_bundle_name(sim, bundle_type: str, layer_number: Optional[int]) -> str:
    zone = sim.expertise_zone or 'Simulation'
    date_str = datetime.utcnow().strftime('%b %Y')
    layer_names = {1: 'Active Income', 2: 'Leveraged Income', 3: 'Productized Income',
                   4: 'Automated Residual', 5: 'Wealth Deployment'}
    type_labels = {
        'layer_portfolio': f'Layer {layer_number} — {layer_names.get(layer_number, "Portfolio")} ({date_str})',
        'full_simulation': f'Full Simulation Package — {zone} ({date_str})',
        'advisor_brief': f'Advisor Brief — {zone} ({date_str})',
        'investor_one_pager': f'Investor One-Pager — {zone} ({date_str})',
        'custom': f'Custom Bundle — {zone} ({date_str})',
    }
    return type_labels.get(bundle_type, f'Bundle ({date_str})')


def _queue_bundle_task(bundle_id: str):
    """Queue the Celery bundle compilation task (falls back to threading)."""
    try:
        from app.tasks.bundle import compile_bundle_task
        app = current_app._get_current_object()

        import threading

        def _run():
            with app.app_context():
                compile_bundle_task.apply(args=[bundle_id])

        threading.Thread(target=_run, daemon=True).start()
    except Exception as e:
        current_app.logger.error('Failed to queue bundle task %s: %s', bundle_id, e)
