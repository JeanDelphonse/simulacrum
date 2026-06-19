import hashlib
import os
from datetime import datetime, timedelta

from flask import request, jsonify, render_template, current_app, send_from_directory, url_for
from flask_login import current_user

from app.blueprints.public import public_bp
from app.extensions import db
from app.models.profile import UserProfile, SimulationVisibility, ProfileInquiry
from app.models.user import User
from app.models.simulation import Simulation, SimulationLayer
from utils.id_gen import generate_id

_SUBJECTS = (
    'Consulting inquiry',
    'Workshop inquiry',
    'Speaking inquiry',
    'General inquiry',
    'Other',
)


@public_bp.route('/avatars/<path:filename>')
def serve_avatar(filename):
    upload_folder = current_app.config.get('UPLOAD_FOLDER', 'uploads')
    return send_from_directory(upload_folder, filename)


@public_bp.route('/u/<username>')
def profile_page(username):
    slug = username.lower()
    profile = UserProfile.query.filter_by(username=slug).first()

    if not profile:
        return render_template('public/profile_unpublished.html', username=username), 200

    user = User.query.get(profile.user_id)
    if not user or user.deleted_at:
        return render_template('public/profile_unpublished.html', username=username), 200

    is_owner = current_user.is_authenticated and current_user.id == profile.user_id

    # ── Bio page (SIM-PRD-BIO-001): render for published pages, or owner preview ──
    from app.models.bio_page import BioPage
    bio_page = BioPage.query.filter_by(user_id=profile.user_id).first()
    # Keep slug in sync with current username (slug may differ if username changed)
    if bio_page and bio_page.slug != slug:
        try:
            bio_page.slug = slug
            db.session.commit()
        except Exception:
            db.session.rollback()
    if bio_page and (bio_page.status == BioPage.STATUS_PUBLISHED or is_owner):
        from app.blueprints.bio.routes import _assemble_context
        ctx = _assemble_context(profile.user_id, bio_page)

        # Track view (skip owner's own views)
        if not is_owner:
            try:
                bio_page.view_count = (bio_page.view_count or 0) + 1
                db.session.commit()
            except Exception:
                db.session.rollback()

        return render_template(
            'public/bio_page.html',
            bio_page=bio_page,
            profile=profile,
            user=user,
            ctx=ctx,
            is_owner=is_owner,
            slug=slug,
            hide_owner_bar=request.args.get('embed') == '1',
        )

    # ── Fallback: legacy profile page ────────────────────────────────────
    if not profile.is_published and not is_owner:
        return render_template('public/profile_unpublished.html', username=username), 200

    vis_records = SimulationVisibility.query.filter_by(
        user_id=profile.user_id, is_public=True,
    ).order_by(SimulationVisibility.display_order.asc()).all()

    zone_cards = []
    sim_bios = []
    seen_zones = set()
    for vis in vis_records:
        sim = Simulation.query.get(vis.simulation_id)
        if not sim:
            continue
        layer1 = SimulationLayer.query.filter_by(
            simulation_id=sim.id, layer_number=1,
        ).first()
        if layer1 and layer1.ai_narrative:
            sim_bios.append({
                'id': sim.id,
                'label': sim.expertise_zone or sim.name or 'Simulation',
                'narrative': layer1.ai_narrative,
            })
        zone_key = (sim.expertise_zone or sim.name or '').strip().lower()
        if zone_key in seen_zones:
            continue
        seen_zones.add(zone_key)
        unique_services = []
        seen_svcs = set()
        for svc in (vis.services or []):
            svc_key = svc.strip().lower()
            if svc_key and svc_key not in seen_svcs:
                seen_svcs.add(svc_key)
                unique_services.append(svc.strip())
        zone_cards.append({
            'vis': vis,
            'sim': sim,
            'narrative': layer1.ai_narrative if layer1 else None,
            'unique_services': unique_services,
        })

    booking_url = profile.effective_booking_url()
    is_owner = current_user.is_authenticated and current_user.id == profile.user_id
    bio_sections = _parse_bio(profile.bio) if profile.bio else None

    return render_template(
        'public/profile.html',
        profile=profile,
        user=user,
        zone_cards=zone_cards,
        sim_bios=sim_bios,
        booking_url=booking_url,
        is_owner=is_owner,
        bio_sections=bio_sections,
        subjects=_SUBJECTS,
    )


@public_bp.route('/u/<username>/cta-click', methods=['POST'])
def bio_cta_click(username: str):
    """Track CTA button clicks on the bio page."""
    from app.models.bio_page import BioPage
    slug = username.lower()
    bp = BioPage.query.filter_by(slug=slug, status=BioPage.STATUS_PUBLISHED).first()
    if bp:
        try:
            bp.cta_click_count = (bp.cta_click_count or 0) + 1
            db.session.commit()
        except Exception:
            db.session.rollback()
    return jsonify({'ok': True})


@public_bp.route('/u/<username>/bio-page.json')
def bio_page_json(username: str):
    """Public JSON endpoint for the bio page editor live preview (same-origin fetch)."""
    from app.models.bio_page import BioPage
    from app.blueprints.bio.routes import _assemble_context
    slug = username.lower()
    bio_page = BioPage.query.filter_by(slug=slug).first()
    if not bio_page:
        return jsonify({'error': 'Not found'}), 404
    ctx = _assemble_context(bio_page.user_id, bio_page)
    return jsonify({
        'bio_page': bio_page.to_dict(),
        'context': ctx,
    })


@public_bp.route('/u/<username>/contact', methods=['POST'])
def contact_form(username):
    profile = UserProfile.query.filter_by(username=username.lower()).first()
    if not profile or not profile.is_published or not profile.show_contact_form:
        return jsonify({'error': 'Not found'}), 404

    ip = request.headers.get('X-Forwarded-For', request.remote_addr or '').split(',')[0].strip()
    ip_hash = hashlib.sha256(ip.encode()).hexdigest()

    cutoff = datetime.utcnow() - timedelta(hours=1)
    recent = ProfileInquiry.query.filter(
        ProfileInquiry.ip_hash == ip_hash,
        ProfileInquiry.profile_user_id == profile.user_id,
        ProfileInquiry.created_at >= cutoff,
    ).count()
    if recent >= 3:
        return jsonify({'error': 'Too many submissions. Please try again later.'}), 429

    data = request.get_json(force=True, silent=True) or {}
    visitor_name = (data.get('name') or '').strip()[:100]
    visitor_email = (data.get('email') or '').strip()[:255]
    subject = (data.get('subject') or 'General inquiry').strip()[:100]
    message = (data.get('message') or '').strip()[:1000]

    if not visitor_name or not visitor_email or '@' not in visitor_email or not message:
        return jsonify({'error': 'Name, email, and message are required'}), 400

    if subject not in _SUBJECTS:
        subject = 'General inquiry'

    inquiry = ProfileInquiry(
        id=generate_id(),
        profile_user_id=profile.user_id,
        visitor_name=visitor_name,
        visitor_email=visitor_email,
        subject=subject,
        message=message,
        ip_hash=ip_hash,
    )
    db.session.add(inquiry)
    db.session.commit()

    user = User.query.get(profile.user_id)
    if user:
        try:
            from app.services.email_service import send_profile_inquiry_email
            send_profile_inquiry_email(
                user.email, profile.display_name or user.full_name,
                visitor_name, visitor_email, subject, message,
            )
        except Exception:
            pass

    return jsonify({
        'ok': True,
        'message': f'Your message has been sent. {profile.display_name or "They"} will be in touch soon.',
    })


_EXPLORE_CATEGORIES = [
    'Technology', 'Finance', 'Marketing', 'Design',
    'Consulting', 'Healthcare', 'Legal', 'Education', 'Other',
]


@public_bp.route('/explore')
def explore():
    """Public explore directory — all published bio pages opted in."""
    from app.models.bio_page import BioPage, BioChatSession
    from app.models.simulation import Simulation
    from app.models.social import UserConnection

    category = request.args.get('category', 'all').lower().strip()

    rows = db.session.query(BioPage, UserProfile).join(
        UserProfile, BioPage.user_id == UserProfile.user_id,
    ).filter(
        BioPage.status == BioPage.STATUS_PUBLISHED,
        BioPage.show_on_explore == True,  # noqa: E712
    ).order_by(BioPage.view_count.desc()).all()

    # Pre-compute viewer's connection graph for degree badges (FR-SOC-05)
    viewer_first = set()
    viewer_second = set()
    if current_user.is_authenticated:
        viewer_first = set(UserConnection.first_degree_ids(current_user.id))
        viewer_second = UserConnection.second_degree_ids(current_user.id)

    cards = []
    for bp, profile in rows:
        sim = Simulation.query.filter_by(
            user_id=bp.user_id, status='complete',
        ).order_by(Simulation.created_at.desc()).first()
        zone = (sim.expertise_zone or 'Other') if sim else 'Other'

        if category != 'all':
            if _zone_to_category(zone).lower() != category:
                continue

        chat_count = BioChatSession.query.filter(
            BioChatSession.bio_page_id == bp.id,
            BioChatSession.status != BioChatSession.STATUS_DELETED,
        ).count()

        like_count = getattr(bp, 'like_count', 0) or 0
        lead_count = bp.contact_form_count or 0

        # FR-SOC-02 updated ranking: views×1 + likes×3 + chats×5 + leads×10
        engagement = (
            (bp.view_count or 0) * 1
            + like_count * 3
            + chat_count * 5
            + lead_count * 10
        )

        degree = None
        via_name = ''
        if current_user.is_authenticated and bp.user_id != current_user.id:
            if bp.user_id in viewer_first:
                degree = 1
            elif bp.user_id in viewer_second:
                degree = 2
                via_name = UserConnection.via_name(current_user.id, bp.user_id)

        cards.append({
            'slug': bp.slug,
            'display_name': profile.display_name or '',
            'tagline': (profile.tagline or '')[:120],
            'avatar_path': profile.avatar_path or '',
            'zone': zone,
            'category': _zone_to_category(zone),
            'chat_count': chat_count,
            'like_count': like_count,
            'view_count': bp.view_count or 0,
            'engagement': engagement,
            'degree': degree,
            'via_name': via_name,
        })

    # 1st-degree connections surface first within featured (FR-SOC-05)
    cards.sort(key=lambda c: (c['degree'] == 1, c['engagement']), reverse=True)
    featured = cards[:4]
    rest = cards[4:] if len(cards) > 4 else []

    return render_template(
        'public/explore.html',
        cards=rest,
        featured=featured,
        category=category,
        categories=_EXPLORE_CATEGORIES,
        total=len(cards),
        viewer_authenticated=current_user.is_authenticated,
    )


def _zone_to_category(zone: str) -> str:
    """Map free-text expertise zone to one of the explore category pills."""
    z = (zone or '').lower()
    for cat in _EXPLORE_CATEGORIES[:-1]:  # skip 'Other'
        if cat.lower() in z:
            return cat
    return 'Other'


@public_bp.route('/embed/<slug>.js')
def embed_badge_js(slug: str):
    """Floating badge embed script — injected into external sites."""
    from app.models.bio_page import BioPage
    bp = BioPage.query.filter_by(
        slug=slug.lower(), status=BioPage.STATUS_PUBLISHED,
    ).first()
    if not bp:
        return ('console.warn("Simulacrum: bio page not found — ' + slug + '");',
                404, {'Content-Type': 'application/javascript'})

    profile = UserProfile.query.filter_by(user_id=bp.user_id).first()
    name = profile.display_name if profile else slug
    avatar = (
        url_for('public.serve_avatar', filename=profile.avatar_path, _external=True)
        if (profile and profile.avatar_path) else ''
    )
    bio_url = request.url_root.rstrip('/') + '/u/' + slug.lower()

    js = render_template(
        'public/embed_badge.js',
        slug=slug.lower(),
        name=name,
        avatar_url=avatar,
        bio_url=bio_url,
    )
    return js, 200, {'Content-Type': 'application/javascript'}


@public_bp.route('/embed/card.js')
def embed_card_js():
    """Inline card embed script — reads data-simulacrum-card attributes on the host page."""
    base_url = request.url_root.rstrip('/')
    js = render_template('public/embed_card.js', base_url=base_url)
    return js, 200, {'Content-Type': 'application/javascript'}


def _parse_bio(bio_text: str) -> list:
    """Split Wikipedia-style bio into sections for rendering."""
    import re
    sections = []
    current_title = None
    current_paras = []

    for line in bio_text.split('\n'):
        line = line.strip()
        if not line:
            continue
        m = re.match(r'^\*\*(.+?)\*\*(.*)$', line)
        if m:
            if current_paras or current_title:
                sections.append({'title': current_title, 'text': ' '.join(current_paras)})
            current_title = m.group(1).strip()
            rest = m.group(2).strip()
            current_paras = [rest] if rest else []
        else:
            current_paras.append(line)

    if current_paras or current_title:
        sections.append({'title': current_title, 'text': ' '.join(current_paras)})

    if not sections:
        sections.append({'title': None, 'text': bio_text.strip()})

    return sections


# ── Simi help page (SIM-PRD-HELP-001) ─────────────────────────────────────────

@public_bp.route('/help/simi')
def simi_help():
    return render_template('public/simi_help.html')
