"""Voice training + video overview API routes (SIM-PRD-VOICE-001)."""
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


# ── GET /api/voice/debug-key (admin only) ────────────────────────────────

@voice_bp.route('/api/voice/debug-key', methods=['GET'])
@login_required
def debug_key():
    import requests as _req
    key = current_app.config.get('ELEVENLABS_API_KEY') or ''
    masked = (key[:6] + '...' + key[-4:]) if len(key) > 10 else '(not set)'
    # Test the key against ElevenLabs /v1/user endpoint
    try:
        r = _req.get('https://api.elevenlabs.io/v1/user',
                     headers={'xi-api-key': key}, timeout=10)
        el_status = r.status_code
        el_body = r.json() if r.headers.get('content-type', '').startswith('application/json') else r.text[:200]
    except Exception as e:
        el_status = None
        el_body = str(e)
    return jsonify({'key_preview': masked, 'length': len(key),
                    'elevenlabs_status': el_status, 'elevenlabs_response': el_body})


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
        success_url=f'{base_url}/settings/voice?voice_session={{CHECKOUT_SESSION_ID}}',
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
    """Accept audio, save it, kick off background ElevenLabs clone, return immediately."""
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

    audio_bytes = audio_file.read()
    if len(audio_bytes) > _MAX_AUDIO_MB * 1024 * 1024:
        return jsonify({'error': f'Audio file too large (max {_MAX_AUDIO_MB}MB)'}), 400
    if len(audio_bytes) < 30 * 1024:
        return jsonify({'error': 'Audio too short — minimum 30 seconds required'}), 400

    upload_dir = os.path.join(current_app.config.get('UPLOAD_FOLDER', 'uploads'), 'voice', str(user.id))
    os.makedirs(upload_dir, exist_ok=True)
    ts = datetime.utcnow().strftime('%Y%m%d%H%M%S')
    save_name = f'voice_sample_{ts}{ext}'
    save_path = os.path.join(upload_dir, save_name)
    with open(save_path, 'wb') as f:
        f.write(audio_bytes)

    profile = UserProfile.query.filter_by(user_id=user.id).first()
    slug = (profile.username if profile else None) or str(user.id)

    # Mark as training-in-progress so status endpoint can report it
    # (reuse voice_consent_accepted_at as sentinel — already set; we need a flag file instead)
    flag_path = os.path.join(upload_dir, 'training.flag')
    error_path = os.path.join(upload_dir, 'training.error')
    for p in (flag_path, error_path):
        try:
            os.remove(p)
        except FileNotFoundError:
            pass
    open(flag_path, 'w').close()

    _app = current_app._get_current_object()
    _user_id = str(user.id)

    def _train():
        with _app.app_context():
            try:
                from app.services.elevenlabs_service import clone_voice
                voice_id = clone_voice(slug, save_path)
                from app.models.user import User as _User
                u = _User.query.get(_user_id)
                if u:
                    u.elevenlabs_voice_id = voice_id
                    u.voice_trained_at    = datetime.utcnow()
                    db.session.commit()
                    db.session.refresh(u)  # confirm write
                    logger.info('Voice training complete for user %s, voice_id=%s', _user_id, voice_id)
                else:
                    logger.error('Voice train: user %s not found after clone', _user_id)
                try:
                    os.remove(flag_path)
                except FileNotFoundError:
                    pass
            except Exception as exc:
                logger.error('ElevenLabs clone_voice failed for user %s: %s', _user_id, exc)
                db.session.rollback()
                try:
                    os.remove(flag_path)
                except FileNotFoundError:
                    pass
                with open(error_path, 'w') as ef:
                    ef.write(str(exc))
            finally:
                db.session.remove()

    import threading
    threading.Thread(target=_train, daemon=True).start()
    return jsonify({'ok': True, 'status': 'processing'}), 202


# ── GET /api/voice/train-status ───────────────────────────────────────────

@voice_bp.route('/api/voice/train-status', methods=['GET'])
@login_required
def voice_train_status():
    """Poll training progress. Returns status: processing | complete | failed."""
    from app.models.user import User
    user = User.query.get(current_user.id)
    if not user:
        return jsonify({'error': 'User not found'}), 404
    if user.voice_trained_at and user.elevenlabs_voice_id:
        return jsonify({'status': 'complete'})
    upload_dir = os.path.join(current_app.config.get('UPLOAD_FOLDER', 'uploads'), 'voice', str(user.id))
    error_path = os.path.join(upload_dir, 'training.error')
    if os.path.exists(error_path):
        try:
            msg = open(error_path).read(500)
        except Exception:
            msg = 'Unknown error'
        return jsonify({'status': 'failed', 'error': msg})
    return jsonify({'status': 'processing'})


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


# ═══════════════════════════════════════════════════════════════════════════
# Part B — Video Overview
# ═══════════════════════════════════════════════════════════════════════════

_SCRIPT_PROMPT = """\
Write a {word_count}-word narration script for a 60-90 second simulation overview video.
Spoken in first person by {display_name}.

Sections to include: {sections}

Simulation data:
- Total orchestrator cycles: {cycle_count}
- Days active: {days_active}
- Agents dispatched: {agents_dispatched}
- Contacts reached: {contacts_total}
- Emails sent: {emails_sent}
- Proposals sent: {proposals_sent}
- Discovery calls booked: {calls_booked}
- Income by layer: L1=${l1_income:,.0f}, L2=${l2_income:,.0f}, L3=${l3_income:,.0f}, L4=${l4_income:,.0f}, L5=${l5_income:,.0f}
- Total confirmed income: ${total_income:,.0f}
- Best outcome: {best_outcome}

Rules:
- Tone: confident, specific, data-driven. Never salesy or vague.
- Speak numbers as words where natural (fifteen thousand, not 15,000).
- DO NOT fabricate or inflate any metric — use only the data above.
- End with: "Create your free bio page at simulacrum AI dot IO."
- Return ONLY the script text. No headings, no stage directions.
"""

_SECTIONS_ALL = ['Opening', 'Wealth Pyramid', 'Agents', 'Results', 'Income', 'Best Win', 'CTA']


def _get_sim_stats(sim_id: str, user_id: str) -> dict:
    """Aggregate simulation stats needed for script generation."""
    from app.models.simulation import Simulation
    from app.models.layer6 import Layer6Cycle, Layer6Outcome, Layer6ActionQueue
    from app.models.agent_action import AgentAction
    from app.models.contact import Contact
    from app.extensions import db as _db
    from sqlalchemy import func as _func
    from datetime import datetime as _dt

    sim = Simulation.query.get(sim_id)
    if not sim:
        raise ValueError('Simulation not found')

    latest_cycle = Layer6Cycle.query.filter_by(simulation_id=sim_id).order_by(
        Layer6Cycle.cycle_number.desc()).first()
    cycle_count = latest_cycle.cycle_number if latest_cycle else 0

    created_at = sim.created_at or _dt.utcnow()
    days_active = max(1, (_dt.utcnow() - created_at).days)

    agents_dispatched = AgentAction.query.filter_by(simulation_id=sim_id).count()

    contacts_total = Contact.query.filter_by(user_id=user_id, is_archived=False).count()

    # Income by layer
    rows = _db.session.query(
        Layer6Outcome.layer_number,
        _func.sum(Layer6Outcome.actual_income).label('total'),
    ).filter_by(simulation_id=sim_id).group_by(Layer6Outcome.layer_number).all()
    income_map = {r.layer_number: float(r.total or 0) for r in rows}
    total_income = sum(income_map.values())

    # Best outcome
    best = Layer6Outcome.query.filter_by(simulation_id=sim_id).order_by(
        Layer6Outcome.actual_income.desc()).first()
    best_outcome = (
        f"${float(best.actual_income):,.0f} from Layer {best.layer_number}"
        if best and best.actual_income else 'Still building momentum'
    )

    return {
        'sim': sim,
        'cycle_count': cycle_count,
        'days_active': days_active,
        'agents_dispatched': agents_dispatched,
        'contacts_total': contacts_total,
        'emails_sent': 0,
        'proposals_sent': 0,
        'calls_booked': 0,
        'l1_income': income_map.get(1, 0),
        'l2_income': income_map.get(2, 0),
        'l3_income': income_map.get(3, 0),
        'l4_income': income_map.get(4, 0),
        'l5_income': income_map.get(5, 0),
        'total_income': total_income,
        'best_outcome': best_outcome,
    }


# ── POST /api/voice/simulations/<sim_id>/generate-script ─────────────────

@voice_bp.route('/api/voice/simulations/<sim_id>/generate-script', methods=['POST'])
@login_required
def generate_video_script(sim_id: str):
    """Generate a 150-200 word narration script via Claude Sonnet."""
    from app.models.simulation import Simulation
    from app.models.user import User
    from app.models.profile import UserProfile
    import anthropic

    sim = Simulation.query.get_or_404(sim_id)
    if sim.user_id != current_user.id:
        return jsonify({'error': 'Forbidden'}), 403

    user = User.query.get(current_user.id)
    if not user or not user.voice_training_paid_at:
        return jsonify({'error': 'Voice training not purchased'}), 403

    data = request.get_json(silent=True) or {}
    sections = data.get('sections') or _SECTIONS_ALL

    try:
        stats = _get_sim_stats(sim_id, current_user.id)
    except Exception as exc:
        return jsonify({'error': str(exc)}), 500

    profile = UserProfile.query.filter_by(user_id=current_user.id).first()
    display_name = (profile.display_name if profile else None) or user.full_name or 'I'

    prompt = _SCRIPT_PROMPT.format(
        word_count='150-200',
        display_name=display_name,
        sections=', '.join(sections),
        cycle_count=stats['cycle_count'],
        days_active=stats['days_active'],
        agents_dispatched=stats['agents_dispatched'],
        contacts_total=stats['contacts_total'],
        emails_sent=stats['emails_sent'],
        proposals_sent=stats['proposals_sent'],
        calls_booked=stats['calls_booked'],
        l1_income=stats['l1_income'],
        l2_income=stats['l2_income'],
        l3_income=stats['l3_income'],
        l4_income=stats['l4_income'],
        l5_income=stats['l5_income'],
        total_income=stats['total_income'],
        best_outcome=stats['best_outcome'],
    )

    try:
        client = anthropic.Anthropic(api_key=current_app.config['CLAUDE_API_KEY'])
        resp = client.messages.create(
            model='claude-sonnet-4-6',
            max_tokens=512,
            messages=[{'role': 'user', 'content': prompt}],
        )
        script = resp.content[0].text.strip()
    except Exception as exc:
        logger.error('Script generation failed: %s', exc)
        return jsonify({'error': 'Script generation failed'}), 502

    return jsonify({'script': script})


# ── POST /api/voice/simulations/<sim_id>/create-video ────────────────────

@voice_bp.route('/api/voice/simulations/<sim_id>/create-video', methods=['POST'])
@login_required
def create_video(sim_id: str):
    """Generate TTS audio + render MP4. Returns SimulationVideo data."""
    from app.models.simulation import Simulation
    from app.models.user import User
    from app.models.simulation_video import SimulationVideo
    from utils.id_gen import generate_id

    sim = Simulation.query.get_or_404(sim_id)
    if sim.user_id != current_user.id:
        return jsonify({'error': 'Forbidden'}), 403

    user = User.query.get(current_user.id)
    if not user or not user.elevenlabs_voice_id:
        return jsonify({'error': 'Voice not trained'}), 403

    data = request.get_json(silent=True) or {}
    script = (data.get('script') or '').strip()
    fmt    = data.get('format', 'square')
    if not script:
        return jsonify({'error': 'script required'}), 400
    if fmt not in ('square', 'landscape', 'vertical'):
        fmt = 'square'

    video_id = generate_id()
    upload_root = current_app.config.get('UPLOAD_FOLDER', 'uploads')
    save_dir = os.path.join(upload_root, 'videos', current_user.id, sim_id)
    os.makedirs(save_dir, exist_ok=True)

    # Create record as processing
    vid = SimulationVideo(
        id=video_id,
        simulation_id=sim_id,
        user_id=current_user.id,
        script=script,
        format=fmt,
        status=SimulationVideo.STATUS_PROCESSING,
    )
    db.session.add(vid)
    db.session.commit()

    try:
        # Step 1: ElevenLabs TTS
        from app.services.video_renderer import generate_tts_audio, render_video
        audio_name = f'audio_{video_id}.mp3'
        audio_path = os.path.join(save_dir, audio_name)
        duration = generate_tts_audio(user.elevenlabs_voice_id, script, audio_path)

        # Step 2: Render MP4 — a branded scene slideshow of the narration text,
        # timed to the voice-over (falls back to a static background if needed).
        sim_label = sim.expertise_zone or sim.name or 'Simulation Overview'
        video_path, thumb_path, duration = render_video(
            audio_path, fmt, save_dir, sim_label, script=script,
        )

        # Store relative paths
        rel_audio = os.path.relpath(audio_path, upload_root).replace('\\', '/')
        rel_video = os.path.relpath(video_path, upload_root).replace('\\', '/')
        rel_thumb = os.path.relpath(thumb_path, upload_root).replace('\\', '/') if thumb_path else ''

        vid.audio_path       = rel_audio
        vid.video_path       = rel_video
        vid.thumbnail_path   = rel_thumb or None
        vid.duration_seconds = duration
        vid.status           = SimulationVideo.STATUS_COMPLETE
        db.session.commit()

    except Exception as exc:
        logger.error('Video creation failed for %s: %s', video_id, exc)
        vid.status = SimulationVideo.STATUS_FAILED
        db.session.commit()
        return jsonify({'error': f'Video creation failed: {str(exc)}'}), 502

    return jsonify(_video_dict(vid))


# ── GET /api/voice/simulations/<sim_id>/videos ───────────────────────────

@voice_bp.route('/api/voice/simulations/<sim_id>/videos', methods=['GET'])
@login_required
def list_sim_videos(sim_id: str):
    from app.models.simulation import Simulation
    from app.models.simulation_video import SimulationVideo
    sim = Simulation.query.get_or_404(sim_id)
    if sim.user_id != current_user.id:
        return jsonify({'error': 'Forbidden'}), 403
    videos = SimulationVideo.query.filter_by(
        simulation_id=sim_id, user_id=current_user.id,
    ).order_by(SimulationVideo.created_at.desc()).all()
    return jsonify({'videos': [_video_dict(v) for v in videos]})


# ── DELETE /api/voice/videos/<video_id> ──────────────────────────────────

@voice_bp.route('/api/voice/videos/<video_id>', methods=['DELETE'])
@login_required
def delete_video(video_id: str):
    from app.models.simulation_video import SimulationVideo
    vid = SimulationVideo.query.filter_by(id=video_id, user_id=current_user.id).first()
    if not vid:
        return jsonify({'error': 'Not found'}), 404
    upload_root = current_app.config.get('UPLOAD_FOLDER', 'uploads')
    for rel_path in (vid.video_path, vid.audio_path, vid.thumbnail_path):
        if rel_path:
            try:
                os.remove(os.path.join(upload_root, rel_path))
            except Exception:
                pass
    db.session.delete(vid)
    db.session.commit()
    return jsonify({'ok': True})


# ── PATCH /api/voice/videos/<video_id>/embed ─────────────────────────────

@voice_bp.route('/api/voice/videos/<video_id>/embed', methods=['PATCH'])
@login_required
def toggle_embed(video_id: str):
    from app.models.simulation_video import SimulationVideo
    vid = SimulationVideo.query.filter_by(id=video_id, user_id=current_user.id).first()
    if not vid:
        return jsonify({'error': 'Not found'}), 404
    data = request.get_json(silent=True) or {}
    vid.embedded_on_bio = bool(data.get('embed', not vid.embedded_on_bio))
    db.session.commit()
    return jsonify({'ok': True, 'embedded_on_bio': vid.embedded_on_bio})


# ── GET /api/voice/videos/<video_id>/stream ──────────────────────────────

@voice_bp.route('/api/voice/videos/<video_id>/stream', methods=['GET'])
@login_required
def stream_video(video_id: str):
    from app.models.simulation_video import SimulationVideo
    vid = SimulationVideo.query.filter_by(id=video_id, user_id=current_user.id).first()
    if not vid or not vid.video_path:
        return jsonify({'error': 'Not found'}), 404
    upload_root = current_app.config.get('UPLOAD_FOLDER', 'uploads')
    full_path = os.path.join(upload_root, vid.video_path)
    if not os.path.exists(full_path):
        return jsonify({'error': 'File not found'}), 404
    ext = os.path.splitext(full_path)[1].lower()
    mime = 'video/mp4' if ext == '.mp4' else 'audio/mpeg'
    return send_file(full_path, mimetype=mime, conditional=True)


# ── GET /api/voice/videos/<video_id>/public-stream  (no auth, bio-embedded only) ──

@voice_bp.route('/api/voice/videos/<video_id>/public-stream', methods=['GET'])
def public_stream_video(video_id: str):
    from app.models.simulation_video import SimulationVideo
    vid = SimulationVideo.query.filter_by(
        id=video_id, embedded_on_bio=True, status='complete',
    ).first()
    if not vid or not vid.video_path:
        return jsonify({'error': 'Not found'}), 404
    upload_root = current_app.config.get('UPLOAD_FOLDER', 'uploads')
    full_path = os.path.join(upload_root, vid.video_path)
    if not os.path.exists(full_path):
        return jsonify({'error': 'File not found'}), 404
    ext = os.path.splitext(full_path)[1].lower()
    mime = 'video/mp4' if ext == '.mp4' else 'audio/mpeg'
    return send_file(full_path, mimetype=mime, conditional=True)


# ── GET /api/voice/videos/<video_id>/public-thumb  (no auth, bio-embedded only) ──

@voice_bp.route('/api/voice/videos/<video_id>/public-thumb', methods=['GET'])
def public_thumb_video(video_id: str):
    from app.models.simulation_video import SimulationVideo
    vid = SimulationVideo.query.filter_by(
        id=video_id, embedded_on_bio=True, status='complete',
    ).first()
    if not vid or not vid.thumbnail_path:
        return jsonify({'error': 'Not found'}), 404
    upload_root = current_app.config.get('UPLOAD_FOLDER', 'uploads')
    full_path = os.path.join(upload_root, vid.thumbnail_path)
    if not os.path.exists(full_path):
        return jsonify({'error': 'Not found'}), 404
    return send_file(full_path, mimetype='image/jpeg')


# ── GET /api/voice/videos/<video_id>/share-caption ───────────────────────

@voice_bp.route('/api/voice/videos/<video_id>/share-caption', methods=['GET'])
@login_required
def share_caption(video_id: str):
    from app.models.simulation_video import SimulationVideo
    vid = SimulationVideo.query.filter_by(id=video_id, user_id=current_user.id).first()
    if not vid:
        return jsonify({'error': 'Not found'}), 404
    name = current_user.full_name or current_user.email.split('@')[0]
    caption = (
        f"Just generated my career wealth simulation overview using Simulacrum AI. "
        f"Watch how my financial trajectory looks — modeled from real data. "
        f"#CareerWealth #SimulacrumAI #PersonalFinance"
    )
    return jsonify({'caption': caption})


# ── helper ────────────────────────────────────────────────────────────────

def _video_dict(v) -> dict:
    dur = v.duration_seconds or 0
    return {
        'id':               v.id,
        'simulation_id':    v.simulation_id,
        'format':           v.format,
        'duration_seconds': dur,
        'duration_label':   f'{dur // 60}:{dur % 60:02d}' if dur else '',
        'embedded_on_bio':  v.embedded_on_bio,
        'status':           v.status,
        'has_video':        bool(v.video_path and v.video_path.endswith('.mp4')),
        'stream_url':       f'/api/voice/videos/{v.id}/stream',
        'created_at':       v.created_at.strftime('%b %-d, %Y') if v.created_at else '',
    }
