"""Video rendering service — ElevenLabs TTS + ffmpeg MP4 (SIM-PRD-VOICE-001 Part B)."""
from __future__ import annotations
import logging
import os
import subprocess
import tempfile
from datetime import datetime

import requests
from flask import current_app

logger = logging.getLogger(__name__)

_ELEVEN_BASE = 'https://api.elevenlabs.io/v1'

# Resolution map
_RESOLUTIONS = {
    'square':    '1080x1080',
    'landscape': '1920x1080',
    'vertical':  '1080x1920',
}


def generate_tts_audio(voice_id: str, script: str, save_path: str) -> int:
    """Call ElevenLabs TTS and save MP3 to save_path. Returns duration in seconds (estimated)."""
    api_key = current_app.config.get('ELEVENLABS_API_KEY', '')
    if not api_key:
        raise ValueError('ELEVENLABS_API_KEY not configured')
    resp = requests.post(
        f'{_ELEVEN_BASE}/text-to-speech/{voice_id}',
        headers={'xi-api-key': api_key, 'Content-Type': 'application/json'},
        json={
            'text': script,
            'model_id': 'eleven_multilingual_v2',
            'voice_settings': {'stability': 0.45, 'similarity_boost': 0.80},
        },
        timeout=120,
    )
    resp.raise_for_status()
    with open(save_path, 'wb') as f:
        f.write(resp.content)
    # Estimate duration: ~150 words/min speaking pace, avg 5 chars/word
    words = len(script.split())
    return max(30, int(words / 2.5))


def _ffmpeg_available() -> bool:
    try:
        result = subprocess.run(['ffmpeg', '-version'], capture_output=True, timeout=5)
        return result.returncode == 0
    except Exception:
        return False


def render_video(audio_path: str, fmt: str, save_dir: str, label: str = '') -> tuple[str, str, int]:
    """
    Create an MP4 from audio using ffmpeg colored background.
    Returns (video_path, thumbnail_path, duration_seconds).
    Falls back to returning (audio_path, '', duration) if ffmpeg unavailable.
    """
    os.makedirs(save_dir, exist_ok=True)
    ts = datetime.utcnow().strftime('%Y%m%d%H%M%S')
    video_name = f'video_{fmt}_{ts}.mp4'
    thumb_name = f'thumb_{fmt}_{ts}.jpg'
    video_path = os.path.join(save_dir, video_name)
    thumb_path = os.path.join(save_dir, thumb_name)

    res = _RESOLUTIONS.get(fmt, '1080x1080')
    w, h = res.split('x')

    # Estimate duration from audio file size (~16KB/s for 128kbps mp3)
    try:
        audio_size = os.path.getsize(audio_path)
        duration_est = max(30, int(audio_size / 16000))
    except Exception:
        duration_est = 60

    if not _ffmpeg_available():
        logger.warning('ffmpeg not found — storing audio only')
        return audio_path, '', duration_est

    # Build ffmpeg command: navy background + audio
    text_safe = (label or 'Simulation Overview').replace("'", "\\'")[:40]
    vf = (
        f"color=c=#0a1628:size={w}x{h}:rate=30,"
        f"drawtext=text='{text_safe}':fontsize={int(int(w)//24)}:"
        f"fontcolor=white:x=(w-text_w)/2:y=(h-text_h)/2-40,"
        f"drawtext=text='SimulacrumAI.io':fontsize={int(int(w)//36)}:"
        f"fontcolor=#4dd9c8:x=(w-text_w)/2:y=(h-text_h)/2+60"
    )
    cmd = [
        'ffmpeg', '-y',
        '-f', 'lavfi', '-i', f'color=c=#0a1628:size={w}x{h}:rate=30',
        '-i', audio_path,
        '-vf', f'drawtext=text={text_safe!r}:fontsize={int(int(w)//24)}:fontcolor=white:x=(w-text_w)/2:y=(h-text_h)/2-40,'
               f'drawtext=text=SimulacrumAI.io:fontsize={int(int(w)//36)}:fontcolor=0x4dd9c8:x=(w-text_w)/2:y=(h-text_h)/2+60',
        '-c:v', 'libx264', '-preset', 'fast', '-crf', '28',
        '-c:a', 'aac', '-b:a', '128k',
        '-shortest', '-movflags', '+faststart',
        video_path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=300)
        if result.returncode != 0:
            logger.error('ffmpeg failed: %s', result.stderr.decode(errors='ignore')[:500])
            return audio_path, '', duration_est
    except Exception as exc:
        logger.error('ffmpeg subprocess error: %s', exc)
        return audio_path, '', duration_est

    # Extract thumbnail
    try:
        subprocess.run(
            ['ffmpeg', '-y', '-i', video_path, '-ss', '0', '-vframes', '1', '-q:v', '5', thumb_path],
            capture_output=True, timeout=30,
        )
        if not os.path.exists(thumb_path):
            thumb_path = ''
    except Exception:
        thumb_path = ''

    # Get actual duration via ffprobe
    try:
        probe = subprocess.run(
            ['ffprobe', '-v', 'quiet', '-show_entries', 'format=duration',
             '-of', 'default=noprint_wrappers=1:nokey=1', video_path],
            capture_output=True, timeout=15,
        )
        duration_est = int(float(probe.stdout.strip()))
    except Exception:
        pass

    return video_path, thumb_path, duration_est
