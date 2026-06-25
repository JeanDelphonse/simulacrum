"""ElevenLabs API service — voice cloning and TTS (SIM-PRD-VOICE-001)."""
from __future__ import annotations
import logging
import os
import requests
from flask import current_app

logger = logging.getLogger(__name__)

_BASE = 'https://api.elevenlabs.io/v1'


def _key() -> str:
    return current_app.config.get('ELEVENLABS_API_KEY', '')


def clone_voice(user_slug: str, audio_path: str) -> str:
    """Upload audio and create an instant voice clone. Returns ElevenLabs voice_id."""
    key = _key()
    if not key:
        raise ValueError('ELEVENLABS_API_KEY not configured')
    ext = os.path.splitext(audio_path)[1].lower()
    mime_map = {'.mp3': 'audio/mpeg', '.wav': 'audio/wav', '.m4a': 'audio/mp4', '.webm': 'audio/webm'}
    mime = mime_map.get(ext, 'audio/mpeg')
    with open(audio_path, 'rb') as f:
        resp = requests.post(
            f'{_BASE}/voices/add',
            headers={'xi-api-key': key},
            data={'name': f'simulacrum_{user_slug}'},
            files=[('files', (os.path.basename(audio_path), f, mime))],
            timeout=180,
        )
    resp.raise_for_status()
    voice_id = resp.json().get('voice_id')
    if not voice_id:
        raise ValueError(f'ElevenLabs returned no voice_id: {resp.text}')
    return voice_id


def generate_preview(voice_id: str) -> bytes:
    """Generate a short preview sentence. Returns raw MP3 bytes."""
    key = _key()
    text = (
        'Your simulation has built a wealth pathway across five income layers. '
        'Your bio page is live and your AI agents are ready to grow your income.'
    )
    resp = requests.post(
        f'{_BASE}/text-to-speech/{voice_id}',
        headers={'xi-api-key': key, 'Content-Type': 'application/json'},
        json={
            'text': text,
            'model_id': 'eleven_multilingual_v2',
            'voice_settings': {'stability': 0.5, 'similarity_boost': 0.75},
        },
        timeout=60,
    )
    resp.raise_for_status()
    return resp.content


def delete_voice(voice_id: str) -> bool:
    """Delete the voice clone from ElevenLabs. Returns True on success."""
    key = _key()
    resp = requests.delete(
        f'{_BASE}/voices/{voice_id}',
        headers={'xi-api-key': key},
        timeout=30,
    )
    return resp.status_code in (200, 204)
