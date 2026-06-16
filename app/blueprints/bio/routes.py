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
                import re
                headline_m = re.search(
                    r'(?i)(?:headline|title)\s*[:\-]\s*(.+)', text
                )
                if headline_m and not ctx['professional_title']:
                    ctx['professional_title'] = headline_m.group(1).strip()[:200]
                if not profile.bio:
                    ctx['about_text'] = text[:1800]

            elif action_type == 'rate_card':
                ctx['_rate_card_raw'] = text[:3000]

            elif action_type == 'booking_page':
                if not ctx['booking_url']:
                    import re
                    m = re.search(r'https?://cal\.com/\S+', text)
                    if m:
                        ctx['booking_url'] = m.group(0)

    # ── Simulation bio zones (all public simulations) ──────────────────────
    from app.models.simulation import SimulationLayer
    from app.models.profile import SimulationVisibility

    vis_records = SimulationVisibility.query.filter_by(
        user_id=user_id, is_public=True,
    ).order_by(SimulationVisibility.display_order.asc()).all()

    sim_bios = []
    sim_zones = []
    seen_zones: set = set()
    for vis in vis_records:
        zone_sim = Simulation.query.get(vis.simulation_id)
        if not zone_sim:
            continue
        layer1 = SimulationLayer.query.filter_by(
            simulation_id=zone_sim.id, layer_number=1,
        ).first()
        unique_services: list = []
        seen_svcs: set = set()
        for svc in (vis.services or []):
            svc_key = svc.strip().lower()
            if svc_key and svc_key not in seen_svcs:
                seen_svcs.add(svc_key)
                unique_services.append(svc.strip())
        availability = getattr(vis, 'availability', 'available') or 'available'
        narrative = layer1.ai_narrative if layer1 and layer1.ai_narrative else None
        label = zone_sim.expertise_zone or zone_sim.name or 'Expertise Area'
        # sim_bios: every individual simulation (no dedup)
        sim_bios.append({
            'id': zone_sim.id,
            'label': label,
            'narrative': narrative,
            'services': unique_services,
            'availability': availability,
        })
        # sim_zones: deduped by zone name (for summary cards)
        zone_key = label.strip().lower()
        if zone_key not in seen_zones:
            seen_zones.add(zone_key)
            sim_zones.append({
                'zone': label,
                'narrative': narrative,
                'services': unique_services,
                'availability': availability,
            })
    ctx['sim_bios'] = sim_bios
    ctx['sim_zones'] = sim_zones

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
    try:
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
    except Exception as exc:
        db.session.rollback()
        import logging as _log
        _log.getLogger(__name__).exception('update_bio_page failed: %s', exc)
        return jsonify({'error': str(exc)}), 500


# ── POST /api/bio/publish ──────────────────────────────────────────────────

@bio_bp.route('/api/bio/publish', methods=['POST'])
@login_required
def publish_bio_page():
    bp = _get_or_create_bio_page(current_user)
    is_first_publish = bp.published_at is None
    bp.status = BioPage.STATUS_PUBLISHED
    bp.published_at = bp.published_at or datetime.utcnow()
    bp.updated_at = datetime.utcnow()
    db.session.commit()
    # Emit activity event for connections' feeds
    try:
        from app.models.social import ActivityEvent
        from utils.id_gen import generate_id as _gid
        ev = ActivityEvent(
            id=_gid(),
            user_id=current_user.id,
            event_type=ActivityEvent.EVENT_BIO_PUBLISHED if is_first_publish
                       else ActivityEvent.EVENT_BIO_UPDATED,
        )
        ev.event_data = {'slug': bp.slug}
        db.session.add(ev)
        db.session.commit()
    except Exception:
        pass
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


# ── POST /api/bio/track (public, no auth) ─────────────────────────────────

@bio_bp.route('/api/bio/track', methods=['POST'])
def track_bio_view():
    """First-party visitor tracking — called silently by the bio page JS."""
    import hashlib
    from app.models.bio_page import BioPageVisit

    data = request.get_json(force=True, silent=True) or {}
    slug = (data.get('slug') or '').strip().lower()
    if not slug:
        return jsonify({'ok': False}), 400

    bp = BioPage.query.filter_by(slug=slug, status=BioPage.STATUS_PUBLISHED).first()
    if not bp:
        return jsonify({'ok': True}), 200

    visitor_hash = hashlib.sha256(
        ((request.remote_addr or '') + (request.headers.get('User-Agent') or '')).encode()
    ).hexdigest()[:32]

    visit = BioPageVisit(
        id=generate_id(),
        bio_page_id=bp.id,
        visitor_hash=visitor_hash,
        referrer=(data.get('referrer') or '')[:255],
        utm_source=(data.get('utm_source') or '')[:100],
    )
    try:
        db.session.add(visit)
        db.session.commit()
    except Exception:
        db.session.rollback()
    return jsonify({'ok': True})


# ── GET /api/bio/analytics ─────────────────────────────────────────────────

@bio_bp.route('/api/bio/analytics', methods=['GET'])
@login_required
def bio_analytics():
    """Visitor analytics for the owner's bio page dashboard."""
    from datetime import timedelta
    from sqlalchemy import func as sqlfunc
    from app.models.bio_page import BioPageVisit, BioChatSession

    bp = BioPage.query.filter_by(user_id=current_user.id).first()
    if not bp:
        return jsonify({'error': 'No bio page'}), 404

    now = datetime.utcnow()
    d7  = now - timedelta(days=7)
    d14 = now - timedelta(days=14)
    d30 = now - timedelta(days=30)

    visits_7d    = BioPageVisit.query.filter(
        BioPageVisit.bio_page_id == bp.id, BioPageVisit.created_at >= d7).count()
    visits_30d   = BioPageVisit.query.filter(
        BioPageVisit.bio_page_id == bp.id, BioPageVisit.created_at >= d30).count()
    visits_prev7 = BioPageVisit.query.filter(
        BioPageVisit.bio_page_id == bp.id,
        BioPageVisit.created_at >= d14, BioPageVisit.created_at < d7).count()

    unique_7d = db.session.query(
        sqlfunc.count(sqlfunc.distinct(BioPageVisit.visitor_hash))
    ).filter(
        BioPageVisit.bio_page_id == bp.id, BioPageVisit.created_at >= d7
    ).scalar() or 0

    chat_7d = BioChatSession.query.filter(
        BioChatSession.bio_page_id == bp.id,
        BioChatSession.created_at >= d7,
        BioChatSession.status != BioChatSession.STATUS_DELETED,
    ).count()

    leads_7d = BioChatSession.query.filter(
        BioChatSession.bio_page_id == bp.id,
        BioChatSession.created_at >= d7,
        BioChatSession.contact_id.isnot(None),
    ).count()

    refs = db.session.query(
        BioPageVisit.referrer,
        sqlfunc.count(BioPageVisit.id).label('cnt'),
    ).filter(
        BioPageVisit.bio_page_id == bp.id, BioPageVisit.created_at >= d30,
    ).group_by(BioPageVisit.referrer).order_by(
        sqlfunc.count(BioPageVisit.id).desc()
    ).limit(5).all()

    top_refs = [{'source': _classify_referrer(r.referrer), 'count': r.cnt} for r in refs]
    show_nudge = visits_7d > 20 and leads_7d < max(visits_7d * 0.1, 1)

    return jsonify({
        'views_7d': visits_7d,
        'views_30d': visits_30d,
        'views_trend': 'up' if visits_7d >= visits_prev7 else 'down',
        'unique_7d': unique_7d,
        'chat_sessions_7d': chat_7d,
        'leads_7d': leads_7d,
        'top_referrers': top_refs,
        'total_views': bp.view_count or 0,
        'show_upgrade_nudge': show_nudge,
    })


def _classify_referrer(ref: str) -> str:
    if not ref:
        return 'Direct'
    rl = ref.lower()
    if 'linkedin' in rl:
        return 'LinkedIn'
    if 'google' in rl:
        return 'Google'
    if 'twitter' in rl or 't.co' in rl:
        return 'Twitter / X'
    if 'facebook' in rl:
        return 'Facebook'
    return (ref[:40] + '…') if len(ref) > 40 else ref


# ── PUT /api/bio/settings ─────────────────────────────────────────────────

@bio_bp.route('/api/bio/settings', methods=['PUT'])
@login_required
def update_bio_settings():
    """Toggle show_badge and show_on_explore flags."""
    bp = _get_or_create_bio_page(current_user)
    data = request.get_json(force=True, silent=True) or {}

    if 'show_badge' in data:
        bp.show_badge = bool(data['show_badge'])
    if 'show_on_explore' in data:
        bp.show_on_explore = bool(data['show_on_explore'])

    bp.updated_at = datetime.utcnow()
    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        return jsonify({'error': str(exc)}), 500
    return jsonify(bp.to_dict())


# ── POST /api/bio/share-prompt-shown ──────────────────────────────────────

@bio_bp.route('/api/bio/share-prompt-shown', methods=['POST'])
@login_required
def mark_share_prompt_shown():
    """Mark the post-publish share prompt as shown so it never reappears."""
    bp = BioPage.query.filter_by(user_id=current_user.id).first()
    if bp and not bp.share_prompt_shown:
        bp.share_prompt_shown = True
        bp.updated_at = datetime.utcnow()
        db.session.commit()
    return jsonify({'ok': True})
