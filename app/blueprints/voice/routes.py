"""Voice training API routes (SIM-PRD-VOICE-001 Part A)."""
from __future__ import annotations
import logging
import os
from datetime import datetime

from flask import jsonify, request, redirect, current_app, send_file
from flask_login import login_required, current_user
import io

from app.blueprints.voice import voice_bp
from app.extensions import db

logger = logging.getLogger(__name__)

_ALLOWED_AUDIO = {'.mp3', '.wav', '.m4a', '.webm', '.ogg'}
_MAX_AUDIO_MB  = 50


# ── POST /api/voice/checkout ──────────────────────────────────────────────

@voice_bp.route('/api/voice/checkout', methods=['POST'])
@login_required
def voice_checkout():
    """Create a $4.99 Stripe Checkout Session for voice training."""
    from app.models.user import User
    user = User.query.get(current_user.id)
    if user and user.voice_training_paid_at:
        return jsonify({'error': 'Already purchased'}), 400
    from app.services.stripe_service import create_voice_training_checkout
    base_url = current_app.config.get('BASE_URL', request.host_url.rstrip('/'))
    result = create_voice_training_checkout(
        user_id=current_user.id,
        success_url=f'{base_url}/settings/voice?voice_paid=1',
        cancel_url=f'{base_url}/settings/voice',
    )
    return jsonify({'checkout_url': result['checkout_url']})


# ── POST /api/voice/consent ───────────────────────────────────────────────

@voice_bp.route('/api/voice/consent', methods=['POST'])
@login_required
def voice_consent():
    """Record consent acceptance."""
    from app.models.user import User
    user = User.query.get(current_user.id)
    if not user:
        return jsonify({'error': 'User not found'}), 404
    if not user.voice_training_paid_at:
        return jsonify({'error': 'Voice training not purchased'}), 403
    user.voice_consent_accepted_at = datetime.utcnow()
    db.session.commit()
    return jsonify({'ok': True})


# ── POST /api/voice/train ─────────────────────────────────────────────────

@voice_bp.route('/api/voice/train', methods=['POST'])
@login_required
def voice_train():
    """Accept audio (browser recording or upload), clone via ElevenLabs, save voice_id."""
    from app.models.user import User
    from app.models.profile import UserProfile
    user = User.query.get(current_user.id)
    if not user:
        return jsonify({'error': 'User not found'}), 404
    if not user.voice_training_paid_at:
        return jsonify({'error': 'Voice training not purchased'}), 403
    if not user.voice_consent_accepted_at:
        return jsonify({'error': 'Consent required'}), 403

    audio_file = request.files.get('audio')
    if not audio_file or not audio_file.filename:
        return jsonify({'error': 'Audio file required'}), 400

    ext = os.path.splitext(audio_file.filename)[1].lower()
    if ext not in _ALLOWED_AUDIO:
        return jsonify({'error': f'Unsupported audio format. Use: {", ".join(_ALLOWED_AUDIO)}'}), 400

    # Check size (read into memory first to get length, then save)
    audio_bytes = audio_file.read()
    if len(audio_bytes) > _MAX_AUDIO_MB * 1024 * 1024:
        return jsonify({'error': f'Audio file too large (max {_MAX_AUDIO_MB}MB)'}), 400
    if len(audio_bytes) < 30 * 1024:  # ~30KB minimum
        return jsonify({'error': 'Audio too short — minimum 30 seconds required'}), 400

    upload_dir = os.path.join(current_app.config.get('UPLOAD_FOLDER', 'uploads'), 'voice', user.id)
    os.makedirs(upload_dir, exist_ok=True)
    ts = datetime.utcnow().strftime('%Y%m%d%H%M%S')
    save_name = f'voice_sample_{ts}{ext}'
    save_path = os.path.join(upload_dir, save_name)
    with open(save_path, 'wb') as f:
        f.write(audio_bytes)

    try:
        from app.services.elevenlabs_service import clone_voice
        profile = UserProfile.query.filter_by(user_id=user.id).first()
        slug = (profile.username if profile else None) or user.id
        voice_id = clone_voice(slug, save_path)
    except Exception as exc:
        logger.error('ElevenLabs clone_voice failed for user %s: %s', user.id, exc)
        return jsonify({'error': f'Voice cloning failed: {str(exc)}'}), 502

    user.elevenlabs_voice_id      = voice_id
    user.voice_trained_at         = datetime.utcnow()
    db.session.commit()
    return jsonify({'ok': True, 'voice_id': voice_id})


# ── GET /api/voice/preview ────────────────────────────────────────────────

@voice_bp.route('/api/voice/preview', methods=['GET'])
@login_required
def voice_preview():
    """Generate and stream a short preview sentence in the user's trained voice."""
    from app.models.user import User
    user = User.query.get(current_user.id)
    if not user or not user.elevenlabs_voice_id:
        return jsonify({'error': 'Voice not trained'}), 404
    try:
        from app.services.elevenlabs_service import generate_preview
        mp3_bytes = generate_preview(user.elevenlabs_voice_id)
    except Exception as exc:
        logger.error('ElevenLabs preview failed for user %s: %s', user.id, exc)
        return jsonify({'error': 'Preview generation failed'}), 502
    return send_file(
        io.BytesIO(mp3_bytes),
        mimetype='audio/mpeg',
        as_attachment=False,
        download_name='voice_preview.mp3',
    )


# ── DELETE /api/voice ─────────────────────────────────────────────────────

@voice_bp.route('/api/voice', methods=['DELETE'])
@login_required
def voice_delete():
    """Delete the user's ElevenLabs voice clone and clear all voice fields."""
    from app.models.user import User
    user = User.query.get(current_user.id)
    if not user or not user.elevenlabs_voice_id:
        return jsonify({'error': 'No voice model found'}), 404
    try:
        from app.services.elevenlabs_service import delete_voice
        delete_voice(user.elevenlabs_voice_id)
    except Exception as exc:
        logger.warning('ElevenLabs delete_voice failed for user %s: %s', user.id, exc)
    user.elevenlabs_voice_id      = None
    user.voice_trained_at         = None
    user.voice_consent_accepted_at = None
    db.session.commit()
    return jsonify({'ok': True})
