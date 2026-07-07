"""
SIM-PRD-QR-001 — Bio Page QR Code.

Generates a branded QR code for each published bio page. The QR encodes the
public bio URL (/u/{slug}) and composites the user's avatar (photo or initials
fallback) into the centre on a white plate that preserves scannability.

Design principle: zero user effort. The QR is generated on publish and
regenerated whenever the slug or avatar changes. It is never generated on page
load — the stored PNG is served statically.

Everything is best-effort: a failure here must never break the publish or
profile-update flow that triggered it, so callers use the guarded
``regenerate_for_profile`` wrapper.
"""
from __future__ import annotations

import os
import logging

from flask import current_app

logger = logging.getLogger(__name__)

# Simulacrum palette
NAVY = '#0D1B3E'
NAVY_RGB = (13, 27, 62)
TEAL_RGB = (19, 168, 158)

QR_OUTPUT_SIZE = 900          # px — high-res source, downscaled in the browser
AVATAR_PCT = 0.26            # avatar occupies 26% of QR width (safe under EC-H 30%)
PLATE_PAD_PCT = 0.12         # white plate padding around the avatar
PLATE_RADIUS_PCT = 0.22      # plate corner radius

# Common locations for DejaVu Serif Bold across Linux hosts (GoDaddy) and Windows.
_FONT_CANDIDATES = (
    '/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf',
    '/usr/share/fonts/dejavu/DejaVuSerif-Bold.ttf',
    '/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf',
    'C:\\Windows\\Fonts\\georgiab.ttf',
    'C:\\Windows\\Fonts\\timesbd.ttf',
)


def _qr_dir() -> str:
    """Absolute path to the static/qr directory, created if missing."""
    static_folder = current_app.static_folder or os.path.join(
        os.path.dirname(os.path.dirname(__file__)), 'static')
    path = os.path.join(static_folder, 'qr')
    os.makedirs(path, exist_ok=True)
    return path


def _bio_url(slug: str) -> str:
    """Public bio URL the QR resolves to. Uses the configured canonical base."""
    base = (current_app.config.get('BASE_URL') or 'https://simulacrumai.io').rstrip('/')
    return f'{base}/u/{slug}'


def _initials(profile) -> str:
    """First + last initial from display name, falling back to the account name."""
    name = (getattr(profile, 'display_name', None) or '').strip()
    if not name:
        user = getattr(profile, 'user', None)
        name = (getattr(user, 'full_name', None) or '').strip() if user else ''
    parts = [p for p in name.split() if p]
    if len(parts) >= 2:
        return (parts[0][0] + parts[-1][0]).upper()
    if parts:
        return parts[0][0].upper()
    return '?'


def _load_font(size: int):
    from PIL import ImageFont
    for candidate in _FONT_CANDIDATES:
        try:
            if os.path.exists(candidate):
                return ImageFont.truetype(candidate, size)
        except Exception:
            continue
    try:
        # Last resort: PIL's bundled default (bitmap, not scalable but never fails)
        return ImageFont.load_default()
    except Exception:
        return None


def _circular_crop(img):
    """Return a square RGBA image with a circular alpha mask applied."""
    from PIL import Image, ImageDraw
    size = min(img.size)
    img = img.convert('RGBA')
    # Center-crop to square first
    left = (img.width - size) // 2
    top = (img.height - size) // 2
    img = img.crop((left, top, left + size, top + size))
    mask = Image.new('L', (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size, size), fill=255)
    img.putalpha(mask)
    return img


def _white_rounded_plate(size: int, radius_pct: float = PLATE_RADIUS_PCT):
    """White rounded-square RGBA plate that sits behind the avatar."""
    from PIL import Image, ImageDraw
    plate = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(plate)
    radius = int(size * radius_pct)
    draw.rounded_rectangle((0, 0, size - 1, size - 1), radius=radius,
                           fill=(255, 255, 255, 255))
    return plate


def _make_initials_avatar(initials: str, size: int):
    """Navy→teal vertical-gradient circle with white serif initials.

    Mirrors the initials avatar used in the Hero and onboarding.
    """
    from PIL import Image, ImageDraw
    # Vertical gradient navy (top) → teal (bottom)
    grad = Image.new('RGBA', (size, size))
    top, bottom = NAVY_RGB, TEAL_RGB
    px = grad.load()
    for y in range(size):
        t = y / max(size - 1, 1)
        r = int(top[0] + (bottom[0] - top[0]) * t)
        g = int(top[1] + (bottom[1] - top[1]) * t)
        b = int(top[2] + (bottom[2] - top[2]) * t)
        for x in range(size):
            px[x, y] = (r, g, b, 255)

    # Circular mask
    mask = Image.new('L', (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size, size), fill=255)
    grad.putalpha(mask)

    # White serif initials at 40% of avatar size
    draw = ImageDraw.Draw(grad)
    font = _load_font(int(size * 0.40))
    if font is not None:
        try:
            box = draw.textbbox((0, 0), initials, font=font)
            tw, th = box[2] - box[0], box[3] - box[1]
            draw.text(((size - tw) / 2 - box[0], (size - th) / 2 - box[1]),
                      initials, font=font, fill=(255, 255, 255, 255))
        except Exception:
            logger.exception('QR initials text render failed')
    return grad


def _load_photo(avatar_path: str, size: int):
    """Open the stored avatar file, center-crop to square, resize. None on failure."""
    from PIL import Image
    upload_base = current_app.config.get('UPLOAD_FOLDER', 'uploads')
    full = os.path.join(upload_base, avatar_path)
    if not os.path.exists(full):
        return None
    try:
        img = Image.open(full).convert('RGBA')
        return img.resize((size, size), Image.LANCZOS) if img.width == img.height \
            else _center_crop_square(img).resize((size, size), Image.LANCZOS)
    except Exception:
        logger.exception('QR photo load failed for %s', avatar_path)
        return None


def _center_crop_square(img):
    size = min(img.size)
    left = (img.width - size) // 2
    top = (img.height - size) // 2
    return img.crop((left, top, left + size, top + size))


def generate_bio_qr(profile) -> str | None:
    """Generate (idempotent) the bio QR for ``profile`` and persist qr_code_url.

    Returns the stored URL path (``/static/qr/{slug}.png``) or None on failure.
    """
    from datetime import datetime
    import qrcode
    from qrcode.constants import ERROR_CORRECT_H
    from PIL import Image
    from app.extensions import db

    slug = (profile.username or '').lower()
    if not slug:
        return None

    url = _bio_url(slug)

    qr = qrcode.QRCode(error_correction=ERROR_CORRECT_H, box_size=14, border=2)
    qr.add_data(url)
    qr.make(fit=True)
    qr_img = qr.make_image(fill_color=NAVY, back_color='white').convert('RGBA')
    qw, qh = qr_img.size

    # Avatar: photo if available, else initials fallback
    av = int(qw * AVATAR_PCT)
    avatar = None
    if profile.avatar_path:
        avatar = _load_photo(profile.avatar_path, av)
    if avatar is None:
        avatar = _make_initials_avatar(_initials(profile), av)
    avatar = _circular_crop(avatar.resize((av, av), Image.LANCZOS))

    # White rounded plate behind the avatar (preserves finder-pattern reads)
    pad = int(av * PLATE_PAD_PCT)
    plate = _white_rounded_plate(av + pad * 2)

    cx, cy = (qw - plate.width) // 2, (qh - plate.height) // 2
    qr_img.paste(plate, (cx, cy), plate)
    ax, ay = (qw - av) // 2, (qh - av) // 2
    qr_img.paste(avatar, (ax, ay), avatar)

    # Persist at 900×900 for crisp print downloads
    out_path = os.path.join(_qr_dir(), f'{slug}.png')
    qr_img.resize((QR_OUTPUT_SIZE, QR_OUTPUT_SIZE), Image.LANCZOS).save(out_path)

    profile.qr_code_url = f'/static/qr/{slug}.png'
    profile.qr_generated_at = datetime.utcnow()
    db.session.commit()
    logger.info('Generated bio QR for slug=%s → %s', slug, profile.qr_code_url)
    return profile.qr_code_url


def delete_qr(slug: str) -> None:
    """Delete a stored QR PNG (used on slug change). Best-effort."""
    if not slug:
        return
    try:
        path = os.path.join(_qr_dir(), f'{(slug or "").lower()}.png')
        if os.path.exists(path):
            os.remove(path)
            logger.info('Deleted stale bio QR for slug=%s', slug)
    except Exception:
        logger.exception('Failed to delete stale QR for slug=%s', slug)


def regenerate_for_profile(profile, old_slug: str | None = None) -> None:
    """Guarded regeneration for use by publish / profile-update triggers.

    Only regenerates when the user's bio page is published. Deletes the old
    PNG when the slug changed. Never raises — a QR failure must not break the
    caller's flow.
    """
    try:
        from app.models.bio_page import BioPage
        bp = BioPage.query.filter_by(user_id=profile.user_id).first()
        if not bp or bp.status != BioPage.STATUS_PUBLISHED:
            return
        new_slug = (profile.username or '').lower()
        if old_slug and old_slug.lower() != new_slug:
            delete_qr(old_slug)
        generate_bio_qr(profile)
    except Exception:
        logger.exception('regenerate_for_profile failed for user=%s',
                         getattr(profile, 'user_id', '?'))
        try:
            from app.extensions import db
            db.session.rollback()
        except Exception:
            pass
