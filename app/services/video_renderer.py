"""Video rendering service — ElevenLabs TTS + ffmpeg MP4 (SIM-PRD-VOICE-001 Part B).

The video overview is a scene slideshow: the narration script is split into
scenes, each rendered as a branded image slide showing that scene's text (Pillow),
then assembled into a timed slideshow synced to the voice-over (ffmpeg). Falls
back to a single static background slide if Pillow/scene rendering is unavailable,
and to audio-only if ffmpeg is missing.
"""
from __future__ import annotations
import logging
import os
import re
import subprocess
from datetime import datetime

import requests
from flask import current_app

logger = logging.getLogger(__name__)

_ELEVEN_BASE = 'https://api.elevenlabs.io/v1'

# Resolution map (width, height)
_RESOLUTIONS = {
    'square':    (1080, 1080),
    'landscape': (1920, 1080),
    'vertical':  (1080, 1920),
}

# Brand palette
_BG_TOP = (10, 22, 40)      # #0a1628 navy
_BG_BOTTOM = (13, 59, 68)   # #0d3b44 deep teal
_ACCENT = (77, 217, 200)    # #4dd9c8
_TEXT = (245, 248, 250)

_FONT_CANDIDATES = [
    '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
    '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
    '/usr/share/fonts/dejavu/DejaVuSans.ttf',
    '/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf',
    'DejaVuSans.ttf',
    'Arial.ttf',
]


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
    # Estimate duration: ~150 words/min speaking pace
    words = len(script.split())
    return max(30, int(words / 2.5))


def _ffmpeg_available() -> bool:
    try:
        result = subprocess.run(['ffmpeg', '-version'], capture_output=True, timeout=5)
        return result.returncode == 0
    except Exception:
        return False


def _probe_duration(path: str) -> float:
    try:
        probe = subprocess.run(
            ['ffprobe', '-v', 'quiet', '-show_entries', 'format=duration',
             '-of', 'default=noprint_wrappers=1:nokey=1', path],
            capture_output=True, timeout=15,
        )
        return float(probe.stdout.strip())
    except Exception:
        return 0.0


# ---------------------------------------------------------------------------
# Scene / slide generation (Pillow)
# ---------------------------------------------------------------------------

def _split_scenes(script: str, max_scenes: int = 8, target_words: int = 16) -> list[str]:
    """Split narration into scene chunks (~target_words each, capped at max_scenes)."""
    text = (script or '').strip()
    if not text:
        return ['Simulation Overview']
    sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', text) if s.strip()]
    if not sentences:
        return [text]

    scenes: list[str] = []
    buf: list[str] = []
    buf_words = 0
    for s in sentences:
        w = len(s.split())
        if buf and buf_words + w > target_words:
            scenes.append(' '.join(buf))
            buf, buf_words = [], 0
        buf.append(s)
        buf_words += w
    if buf:
        scenes.append(' '.join(buf))

    # Cap the count by merging trailing scenes into the last kept one.
    if len(scenes) > max_scenes:
        head = scenes[:max_scenes - 1]
        tail = ' '.join(scenes[max_scenes - 1:])
        scenes = head + [tail]
    return scenes


def _load_font(size: int):
    from PIL import ImageFont
    for p in _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(p, size)
        except Exception:
            continue
    try:
        return ImageFont.load_default(size=size)  # Pillow >= 10.1
    except Exception:
        return ImageFont.load_default()


def _wrap(draw, text: str, font, max_w: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    cur = ''
    for word in words:
        trial = f'{cur} {word}'.strip()
        try:
            width = draw.textlength(trial, font=font)
        except Exception:
            width = len(trial) * (font.size * 0.5 if hasattr(font, 'size') else 8)
        if width <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def _render_slide(text: str, footer_label: str, size: tuple[int, int], path: str) -> bool:
    """Render one branded scene slide to `path`. Returns True on success."""
    try:
        from PIL import Image, ImageDraw
    except Exception as exc:
        logger.warning('Pillow unavailable — cannot render scene slides: %s', exc)
        return False

    w, h = size
    img = Image.new('RGB', (w, h), _BG_TOP)
    draw = ImageDraw.Draw(img)

    # Vertical gradient background
    for y in range(h):
        t = y / max(1, h - 1)
        r = int(_BG_TOP[0] + (_BG_BOTTOM[0] - _BG_TOP[0]) * t)
        g = int(_BG_TOP[1] + (_BG_BOTTOM[1] - _BG_TOP[1]) * t)
        b = int(_BG_TOP[2] + (_BG_BOTTOM[2] - _BG_TOP[2]) * t)
        draw.line([(0, y), (w, y)], fill=(r, g, b))

    # Body text — wrapped, centered
    body_size = max(28, w // 22)
    body_font = _load_font(body_size)
    max_text_w = int(w * 0.82)
    lines = _wrap(draw, text, body_font, max_text_w)
    line_h = int(body_size * 1.4)
    block_h = line_h * len(lines)
    y0 = (h - block_h) // 2
    for i, line in enumerate(lines):
        try:
            lw = draw.textlength(line, font=body_font)
        except Exception:
            lw = len(line) * body_size * 0.5
        draw.text(((w - lw) / 2, y0 + i * line_h), line, font=body_font, fill=_TEXT)

    # Accent rule above footer
    draw.line([(w * 0.4, h - int(h * 0.12)), (w * 0.6, h - int(h * 0.12))],
              fill=_ACCENT, width=max(2, w // 400))

    # Footer: sim label + brand
    foot_size = max(18, w // 40)
    foot_font = _load_font(foot_size)
    footer = f'{footer_label}  ·  SimulacrumAI.io' if footer_label else 'SimulacrumAI.io'
    try:
        fw = draw.textlength(footer, font=foot_font)
    except Exception:
        fw = len(footer) * foot_size * 0.5
    draw.text(((w - fw) / 2, h - int(h * 0.09)), footer, font=foot_font, fill=_ACCENT)

    try:
        img.save(path, 'PNG')
        return True
    except Exception as exc:
        logger.warning('Slide save failed: %s', exc)
        return False


def _render_scene_slideshow(audio_path: str, fmt: str, save_dir: str, label: str,
                            script: str, video_path: str) -> bool:
    """Build a timed slide slideshow synced to the audio. Returns True on success."""
    w, h = _RESOLUTIONS.get(fmt, (1080, 1080))
    scenes = _split_scenes(script)

    # Render one slide per scene
    slide_paths: list[str] = []
    for i, scene in enumerate(scenes):
        sp = os.path.join(save_dir, f'slide_{i:02d}.png')
        if not _render_slide(scene, label, (w, h), sp):
            return False
        slide_paths.append(sp)
    if not slide_paths:
        return False

    # Allocate per-slide durations proportional to word count, summing to audio length
    audio_dur = _probe_duration(audio_path) or max(30.0, len(script.split()) / 2.5)
    weights = [max(1, len(s.split())) for s in scenes]
    total_w = sum(weights)
    raw = [max(2.0, audio_dur * (wt / total_w)) for wt in weights]
    scale = audio_dur / sum(raw)
    durs = [d * scale for d in raw]

    # ffmpeg concat demuxer list — repeat last frame so its duration is honored
    list_path = os.path.join(save_dir, 'slides.txt')
    with open(list_path, 'w', encoding='utf-8') as f:
        for sp, d in zip(slide_paths, durs):
            f.write(f"file '{sp}'\n")
            f.write(f'duration {d:.3f}\n')
        f.write(f"file '{slide_paths[-1]}'\n")

    cmd = [
        'ffmpeg', '-y',
        '-f', 'concat', '-safe', '0', '-i', list_path,
        '-i', audio_path,
        '-vf', 'format=yuv420p',
        '-r', '30',
        '-c:v', 'libx264', '-preset', 'fast', '-crf', '28',
        '-c:a', 'aac', '-b:a', '128k',
        '-shortest', '-movflags', '+faststart',
        video_path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=300)
        if result.returncode != 0:
            logger.error('ffmpeg slideshow failed: %s', result.stderr.decode(errors='ignore')[:500])
            return False
    except Exception as exc:
        logger.error('ffmpeg slideshow subprocess error: %s', exc)
        return False
    finally:
        # Clean up intermediate slides + list (keep the mp4/thumb)
        for sp in slide_paths:
            try:
                os.remove(sp)
            except Exception:
                pass
        try:
            os.remove(list_path)
        except Exception:
            pass
    return os.path.exists(video_path)


def _render_static_background(audio_path: str, fmt: str, w: int, h: int,
                              label: str, video_path: str) -> bool:
    """Fallback: single solid background + centered label over the audio."""
    text_safe = (label or 'Simulation Overview').replace("'", "\\'")[:40]
    cmd = [
        'ffmpeg', '-y',
        '-f', 'lavfi', '-i', f'color=c=#0a1628:size={w}x{h}:rate=30',
        '-i', audio_path,
        '-vf', f'drawtext=text={text_safe!r}:fontsize={int(w // 24)}:fontcolor=white:x=(w-text_w)/2:y=(h-text_h)/2-40,'
               f'drawtext=text=SimulacrumAI.io:fontsize={int(w // 36)}:fontcolor=0x4dd9c8:x=(w-text_w)/2:y=(h-text_h)/2+60',
        '-c:v', 'libx264', '-preset', 'fast', '-crf', '28',
        '-c:a', 'aac', '-b:a', '128k',
        '-shortest', '-movflags', '+faststart',
        video_path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=300)
        return result.returncode == 0 and os.path.exists(video_path)
    except Exception as exc:
        logger.error('ffmpeg static background error: %s', exc)
        return False


def render_video(audio_path: str, fmt: str, save_dir: str, label: str = '',
                 script: str = '') -> tuple[str, str, int]:
    """
    Create an MP4 from audio. Preferred path is a branded scene slideshow built
    from `script` (images with the narration text, timed to the voice). Falls back
    to a single static background, then to audio-only if ffmpeg is unavailable.
    Returns (video_path, thumbnail_path, duration_seconds).
    """
    os.makedirs(save_dir, exist_ok=True)
    ts = datetime.utcnow().strftime('%Y%m%d%H%M%S')
    video_path = os.path.join(save_dir, f'video_{fmt}_{ts}.mp4')
    thumb_path = os.path.join(save_dir, f'thumb_{fmt}_{ts}.jpg')

    w, h = _RESOLUTIONS.get(fmt, (1080, 1080))

    # Estimate duration from audio file size (~16KB/s for 128kbps mp3)
    try:
        duration_est = max(30, int(os.path.getsize(audio_path) / 16000))
    except Exception:
        duration_est = 60

    if not _ffmpeg_available():
        logger.warning('ffmpeg not found — storing audio only')
        return audio_path, '', duration_est

    # Preferred: scene slideshow with the narration text. Fall back to a static bg.
    built = False
    if script and script.strip():
        try:
            built = _render_scene_slideshow(audio_path, fmt, save_dir, label, script, video_path)
        except Exception as exc:
            logger.warning('Scene slideshow render failed, falling back: %s', exc)
            built = False
    if not built:
        if not _render_static_background(audio_path, fmt, w, h, label, video_path):
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

    # Actual duration via ffprobe
    actual = _probe_duration(video_path)
    duration_est = int(actual) if actual else duration_est

    return video_path, thumb_path, duration_est
