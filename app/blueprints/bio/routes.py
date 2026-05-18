"""
SIM-PRD-BIO-001 — Bio page editor API (authenticated owner only).
"""
from __future__ import annotations

import json
import os
from datetime import datetime

from flask import jsonify, request, current_app
from flask_login import login_required, current_user

from app.blueprints.bio import bio_bp
from app.extensions import db
from app.models.bio_page import BioPage
from app.models.profile import UserProfile
from utils.id_gen import generate_id


def _get_or_create_bio_page(user) -> BioPage:
    """Return the user's bio page, auto-creating it if it doesn't exist."""
    bp = BioPage.query.filter_by(user_id=user.id).first()
    if bp:
        return bp

    profile = UserProfile.query.filter_by(user_id=user.id).first()
    slug = profile.username if profile else user.id

    bp = BioPage(
        id=generate_id(),
        user_id=user.id,
        slug=slug,
    )
    db.session.add(bp)
    db.session.commit()
    return bp


def _bio_page_with_context(bp: BioPage) -> dict:
    """Return bio page dict with assembled artifact context."""
    data = bp.to_dict()
    data['assembled'] = _assemble_context(bp.user_id, bp)
    return data


def _assemble_context(user_id: str, bp: BioPage | None = None) -> dict:
    """Assemble public-safe bio page content from artifacts and profile."""
    from app.models.user import User
    from app.models.agent_action import AgentAction
    from app.models.simulation import Simulation

    user = User.query.get(user_id)
    profile = UserProfile.query.filter_by(user_id=user_id).first()
    if not user or not profile:
        return {}

    ctx = {
        'full_name': profile.display_name or user.full_name or '',
        'professional_title': '',
        'positioning': profile.tagline or '',
        'about_text': profile.bio or '',
        'booking_url': profile.effective_booking_url(),
        'cta_label': 'Book a call',
        'hero_image_url': None,
        'service_tiers': [],
        'products': [],
        'speaking_topics': [],
        'blog_articles': [],
        'rate_card_url': None,
        'linkedin_url': profile.linkedin_url or '',
        'twitter_url': profile.twitter_url or '',
        'website_url': profile.website_url or '',
        'avatar_path': profile.avatar_path or '',
    }

    # Pull from most recent complete simulation's agent actions
    sim = Simulation.query.filter_by(
        user_id=user_id, status='complete',
    ).order_by(Simulation.created_at.desc()).first()

    if sim:
        for action_type in ('linkedin_optimization', 'rate_card', 'booking_page'):
            action = AgentAction.query.filter_by(
                simulation_id=sim.id,
                action_type=action_type,
                status='complete',
            ).order_by(AgentAction.completed_at.desc()).first()
            if not action or not action.artifact:
                continue
            text = action.artifact

            if action_type == 'linkedin_optimization':
                # Try to extract headline and about
                import re
                headline_m = re.search(
                    r'(?i)(?:headline|title)\s*[:\-]\s*(.+)', text
                )
                if headline_m and not ctx['professional_title']:
                    ctx['professional_title'] = headline_m.group(1).strip()[:200]
                # Use first 1800 chars as about fallback
                if not profile.bio:
                    ctx['about_text'] = text[:1800]

            elif action_type == 'rate_card':
                # Surface as raw text; tiers parsed best-effort
                ctx['_rate_card_raw'] = text[:3000]

            elif action_type == 'booking_page':
                if not ctx['booking_url']:
                    import re
                    m = re.search(r'https?://cal\.com/\S+', text)
                    if m:
                        ctx['booking_url'] = m.group(0)

    # Apply section overrides from bio page
    if bp:
        sections = bp.sections
        hero = sections.get('hero', {})
        if hero.get('is_custom_title') and hero.get('professional_title'):
            ctx['professional_title'] = hero['professional_title']
        if hero.get('is_custom_positioning') and hero.get('positioning'):
            ctx['positioning'] = hero['positioning']
        if hero.get('cta_url'):
            ctx['booking_url'] = hero['cta_url']
        if hero.get('cta_label'):
            ctx['cta_label'] = hero['cta_label']
        if hero.get('hero_image_url'):
            ctx['hero_image_url'] = hero['hero_image_url']

        about = sections.get('about', {})
        if about.get('is_custom') and about.get('bio_text'):
            ctx['about_text'] = about['bio_text']

    if not ctx['booking_url']:
        ctx['cta_label'] = 'Get in touch'

    return ctx


# ── GET /api/bio ───────────────────────────────────────────────────────────

@bio_bp.route('/api/bio', methods=['GET'])
@login_required
def get_bio_page():
    bp = _get_or_create_bio_page(current_user)
    return jsonify(_bio_page_with_context(bp))


# ── PUT /api/bio ───────────────────────────────────────────────────────────

@bio_bp.route('/api/bio', methods=['PUT'])
@login_required
def update_bio_page():
    bp = _get_or_create_bio_page(current_user)
    data = request.get_json(force=True, silent=True) or {}

    if 'sections' in data and isinstance(data['sections'], dict):
        current = bp.sections
        current.update(data['sections'])
        bp.sections = current

    if 'theme' in data and data['theme'] in (
        BioPage.THEME_DEFAULT, BioPage.THEME_DARK, BioPage.THEME_WARM
    ):
        bp.theme = data['theme']

    if 'chat_settings' in data and isinstance(data['chat_settings'], dict):
        current = bp.chat_settings
        current.update(data['chat_settings'])
        bp.chat_settings = current

    bp.updated_at = datetime.utcnow()
    db.session.commit()
    return jsonify(bp.to_dict())


# ── POST /api/bio/publish ──────────────────────────────────────────────────

@bio_bp.route('/api/bio/publish', methods=['POST'])
@login_required
def publish_bio_page():
    bp = _get_or_create_bio_page(current_user)
    bp.status = BioPage.STATUS_PUBLISHED
    bp.published_at = bp.published_at or datetime.utcnow()
    bp.updated_at = datetime.utcnow()
    db.session.commit()
    return jsonify({'status': bp.status, 'published_at': bp.published_at.isoformat()})


# ── POST /api/bio/unpublish ────────────────────────────────────────────────

@bio_bp.route('/api/bio/unpublish', methods=['POST'])
@login_required
def unpublish_bio_page():
    bp = _get_or_create_bio_page(current_user)
    bp.status = BioPage.STATUS_UNPUBLISHED
    bp.unpublished_at = datetime.utcnow()
    bp.updated_at = datetime.utcnow()
    db.session.commit()
    return jsonify({'status': bp.status})


# ── POST /api/bio/hero-image ───────────────────────────────────────────────

@bio_bp.route('/api/bio/hero-image', methods=['POST'])
@login_required
def upload_hero_image():
    if 'image' not in request.files:
        return jsonify({'error': 'No file provided'}), 400
    f = request.files['image']
    ext = (f.filename or '').rsplit('.', 1)[-1].lower()
    if ext not in ('jpg', 'jpeg', 'png', 'webp'):
        return jsonify({'error': 'JPEG, PNG, or WebP only'}), 400
    f.seek(0, 2)
    if f.tell() > 5 * 1024 * 1024:
        return jsonify({'error': 'File must be under 5 MB'}), 400
    f.seek(0)

    upload_base = current_app.config.get('UPLOAD_FOLDER', 'uploads')
    hero_dir = os.path.join(upload_base, 'bio_heroes', current_user.id)
    os.makedirs(hero_dir, exist_ok=True)
    filename = f'hero.{ext}'
    f.save(os.path.join(hero_dir, filename))
    rel_path = os.path.join('bio_heroes', current_user.id, filename)

    bp = _get_or_create_bio_page(current_user)
    s = bp.sections
    s.setdefault('hero', {})['hero_image_url'] = rel_path
    bp.sections = s
    db.session.commit()
    return jsonify({'hero_image_url': rel_path})


# ── Testimonials CRUD ──────────────────────────────────────────────────────

@bio_bp.route('/api/bio/testimonials', methods=['POST'])
@login_required
def add_testimonial():
    bp = _get_or_create_bio_page(current_user)
    data = request.get_json(force=True, silent=True) or {}

    quote = (data.get('quote') or '').strip()[:500]
    name = (data.get('name') or '').strip()[:100]
    if not quote or not name:
        return jsonify({'error': 'quote and name are required'}), 400

    testimonial = {
        'id': generate_id(),
        'quote': quote,
        'name': name,
        'title': (data.get('title') or '').strip()[:100] or None,
        'company': (data.get('company') or '').strip()[:100] or None,
        'photo_url': None,
        'order': len(bp.custom_testimonials),
    }
    items = bp.custom_testimonials
    items.append(testimonial)
    bp.custom_testimonials = items
    bp.updated_at = datetime.utcnow()
    db.session.commit()
    return jsonify(testimonial), 201


@bio_bp.route('/api/bio/testimonials/<tid>', methods=['PUT'])
@login_required
def update_testimonial(tid: str):
    bp = BioPage.query.filter_by(user_id=current_user.id).first_or_404()
    data = request.get_json(force=True, silent=True) or {}
    items = bp.custom_testimonials
    item = next((t for t in items if t['id'] == tid), None)
    if not item:
        return jsonify({'error': 'Not found'}), 404

    for field, max_len in (('quote', 500), ('name', 100), ('title', 100), ('company', 100)):
        if field in data:
            item[field] = (data[field] or '').strip()[:max_len] or None

    bp.custom_testimonials = items
    bp.updated_at = datetime.utcnow()
    db.session.commit()
    return jsonify(item)


@bio_bp.route('/api/bio/testimonials/<tid>', methods=['DELETE'])
@login_required
def delete_testimonial(tid: str):
    bp = BioPage.query.filter_by(user_id=current_user.id).first_or_404()
    items = [t for t in bp.custom_testimonials if t['id'] != tid]
    bp.custom_testimonials = items
    bp.updated_at = datetime.utcnow()
    db.session.commit()
    return jsonify({'ok': True})


@bio_bp.route('/api/bio/testimonials/reorder', methods=['PUT'])
@login_required
def reorder_testimonials():
    bp = BioPage.query.filter_by(user_id=current_user.id).first_or_404()
    data = request.get_json(force=True, silent=True) or {}
    order = data.get('order', [])  # list of testimonial IDs
    items_by_id = {t['id']: t for t in bp.custom_testimonials}
    reordered = []
    for i, tid in enumerate(order):
        if tid in items_by_id:
            items_by_id[tid]['order'] = i
            reordered.append(items_by_id[tid])
    # Append any that weren't in the order list
    seen = set(order)
    for t in bp.custom_testimonials:
        if t['id'] not in seen:
            reordered.append(t)
    bp.custom_testimonials = reordered
    bp.updated_at = datetime.utcnow()
    db.session.commit()
    return jsonify({'ok': True})


# ── GET /api/bio/preview ───────────────────────────────────────────────────

@bio_bp.route('/api/bio/preview', methods=['GET'])
@login_required
def bio_preview():
    """Return the assembled public context for the bio page editor preview."""
    bp = _get_or_create_bio_page(current_user)
    return jsonify(_assemble_context(current_user.id, bp))
