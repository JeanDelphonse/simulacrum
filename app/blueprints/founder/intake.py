"""Founder intake (FOUNDER_PLAN.md WP1): start a venture, upload documents,
talk the pitch through.

Pages:  GET  /founder/start                        create-venture form (redirects if you have one)
        GET  /founder/v/<id>/intake                documents + spoken pitch
APIs:   POST /api/founder/ventures                 create the venture
        GET  /api/founder/v/<id>/artifacts         current version per kind (+ history)
        POST /api/founder/v/<id>/artifacts         upload (multipart: file, kind)
        POST /api/founder/v/<id>/pitch/start       begin a fresh pitch conversation
        POST /api/founder/v/<id>/pitch/turn        {text, input_mode}
        POST /api/founder/v/<id>/pitch/confirm     {restatement?} -> pitch_transcript artifact

Every route 404s while the track is off for the user; pitch routes also 404
while founder_pitch is off. Venture routes 404 for an unknown venture and 403
for a non-member.
"""
from __future__ import annotations

from flask import jsonify, redirect, render_template, request, url_for
from flask_login import current_user

from app.blueprints.founder import founder_bp
from app.blueprints.founder.common import founder_required


def _venture(venture_id: str):
    from app.services import founder_ventures
    return founder_ventures.require_member(venture_id, current_user)


def _err(message: str, status: int = 400):
    return jsonify({'ok': False, 'error': message}), status


def _payload() -> dict:
    return request.get_json(silent=True) or request.form.to_dict() or {}


@founder_bp.route('/founder/start')
@founder_required
def start_page():
    from app.services import founder_ventures
    v = founder_ventures.venture_for_owner(current_user.id)
    if v is not None:
        return redirect(url_for('founder.intake_page', venture_id=v.id))
    return render_template('founder/intake.html', venture=None, stages=founder_ventures.STAGES)


@founder_bp.route('/api/founder/ventures', methods=['POST'])
@founder_required
def create_venture():
    from app.services import founder_ventures
    d = _payload()
    try:
        v = founder_ventures.create_venture(current_user, d.get('name', ''), d.get('one_liner', ''),
                                            d.get('stage', 'idea'))
    except founder_ventures.VentureError as exc:
        return _err(str(exc))
    return jsonify({'ok': True, 'venture_id': v.id,
                    'url': url_for('founder.intake_page', venture_id=v.id)}), 201


@founder_bp.route('/founder/v/<venture_id>/intake')
@founder_required
def intake_page(venture_id):
    from app.services import founder_feature, founder_intake_service as fis, founder_pitch_service as fps
    v = _venture(venture_id)
    pitch_on = founder_feature.is_enabled(current_user, founder_feature.PITCH)
    return render_template('founder/intake.html', venture=v, kinds=fis.summary(v),
                           upload_kinds=[(k, fis.KIND_LABELS[k]) for k in fis.UPLOAD_KINDS],
                           max_mb=fis.max_upload_bytes() // (1024 * 1024), pitch_on=pitch_on,
                           pitch=fps.state(v, current_user) if pitch_on else None,
                           element_labels=fps.ELEMENT_LABELS)


@founder_bp.route('/api/founder/v/<venture_id>/artifacts', methods=['GET'])
@founder_required
def list_artifacts(venture_id):
    from app.services import founder_intake_service as fis
    v = _venture(venture_id)
    return jsonify({'ok': True, 'kinds': fis.summary(v),
                    'history': [a.to_dict() for a in fis.artifacts(v)]})


@founder_bp.route('/api/founder/v/<venture_id>/artifacts', methods=['POST'])
@founder_required
def upload_artifact(venture_id):
    from app.services import founder_intake_service as fis
    v = _venture(venture_id)
    try:
        art = fis.save_upload(v, request.files.get('file'), (request.form.get('kind') or '').strip(), current_user)
    except fis.IntakeError as exc:
        return _err(str(exc), exc.status)
    return jsonify({'ok': True, 'artifact': art.to_dict(),
                    'message': '%s saved as version %d.' % (fis.KIND_LABELS[art.kind], art.version)}), 201


def _pitch_call(venture_id, fn):
    from app.services import founder_pitch_service as fps
    from app.services import founder_intake_service as fis
    v = _venture(venture_id)
    try:
        return fn(fps, v)
    except fps.PitchError as exc:
        return _err(str(exc), 409)
    except fis.IntakeError as exc:
        return _err(str(exc), exc.status)


@founder_bp.route('/api/founder/v/<venture_id>/pitch/start', methods=['POST'])
@founder_required(key='founder_pitch')
def pitch_start(venture_id):
    return _pitch_call(venture_id, lambda fps, v: jsonify({'ok': True, 'turn': fps.start(v, current_user)}))


@founder_bp.route('/api/founder/v/<venture_id>/pitch/turn', methods=['POST'])
@founder_required(key='founder_pitch')
def pitch_turn(venture_id):
    d = _payload()
    mode = d.get('input_mode') if d.get('input_mode') in ('voice', 'typed') else 'typed'
    return _pitch_call(venture_id, lambda fps, v: jsonify({'ok': True,
                                                           'turn': fps.turn(v, current_user, d.get('text', ''), mode)}))


@founder_bp.route('/api/founder/v/<venture_id>/pitch/confirm', methods=['POST'])
@founder_required(key='founder_pitch')
def pitch_confirm(venture_id):
    d = _payload()

    def _do(fps, v):
        art = fps.confirm(v, current_user, d.get('restatement'))
        return jsonify({'ok': True, 'artifact': art.to_dict(),
                        'message': 'Pitch saved as transcript version %d.' % art.version}), 201
    return _pitch_call(venture_id, _do)
