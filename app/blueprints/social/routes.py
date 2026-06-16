"""
SIM-PRD-SOCIAL-001 — Social Network Layer routes.
Covers: likes, connections, activity feed, platform chat.
"""
from __future__ import annotations
import json
import logging
from datetime import datetime, timedelta

from flask import jsonify, request, render_template, Response, stream_with_context, current_app
from flask_login import login_required, current_user

from app.blueprints.social import social_bp
from app.extensions import db
from app.models.social import (
    BioPageLike, UserConnection, ActivityEvent,
    PlatformChat, PlatformChatMessage,
)
from app.models.bio_page import BioPage
from utils.id_gen import generate_id

logger = logging.getLogger(__name__)

_HAIKU_MODEL  = 'claude-haiku-4-5-20251001'
_SONNET_MODEL = 'claude-sonnet-4-6'

_PLATFORM_SYSTEM_PROMPT = """\
You are {owner_first_name}'s professional assistant.
You are chatting with {chatter_name}, an authenticated Simulacrum member.
Answer questions about {owner_first_name}'s services, expertise, and availability.

RULES:
- You represent {owner_first_name} warmly and professionally.
- Answer ONLY from the context provided. Never invent services or qualifications.
- Keep responses concise (2-4 sentences). Suggest concrete next steps.
- Address {chatter_first_name} by first name.
- You are {owner_first_name}'s assistant, not {owner_first_name} directly.
- Because {chatter_first_name} is already a Simulacrum member, do not ask for contact info.

=== CONTEXT ===
Professional title: {professional_title}
Positioning: {positioning}
Services:
{services_text}
Booking link: {booking_url}
About {owner_first_name}:
{about_text}
=== END CONTEXT ===
"""

# ── Bayesian signal helper ────────────────────────────────────────────────────

def _emit_bayesian_like(user_id: str, simulation_id: str):
    """FR-SOC-03: like velocity → positive signal for linkedin_optimization."""
    try:
        from app.models.bayesian import BayesianPosterior
        from app.extensions import db as _db
        week_ago = datetime.utcnow() - timedelta(days=7)
        weekly_likes = BioPageLike.query.join(
            BioPage, BioPageLike.bio_page_id == BioPage.id
        ).filter(
            BioPage.user_id == user_id,
            BioPageLike.created_at >= week_ago,
        ).count()
        signal_val = min(1.0, weekly_likes / 10.0)
        existing = BayesianPosterior.query.filter_by(
            user_id=user_id, simulation_id=simulation_id,
            signal_type='bio_like',
        ).first()
        if existing:
            existing.signal_value = signal_val
            existing.updated_at = datetime.utcnow()
        else:
            row = BayesianPosterior(
                id=generate_id(),
                user_id=user_id,
                simulation_id=simulation_id,
                signal_type='bio_like',
                signal_value=signal_val,
                weight=0.3,
                direction='+',
            )
            _db.session.add(row)
        _db.session.flush()
    except Exception as exc:
        logger.debug('Bayesian like signal skipped: %s', exc)


# ═══════════════════════════════════════════════════════
# Layer A — Likes
# ═══════════════════════════════════════════════════════

@social_bp.route('/api/bio/<slug>/like', methods=['POST'])
@login_required
def toggle_like(slug: str):
    """FR-SOC-01: Toggle like on a bio page. Returns new like status and count."""
    bp = BioPage.query.filter_by(slug=slug.lower(), status=BioPage.STATUS_PUBLISHED).first()
    if not bp:
        return jsonify({'error': 'Bio page not found'}), 404

    existing = BioPageLike.query.filter_by(
        bio_page_id=bp.id, user_id=current_user.id
    ).first()

    if existing:
        db.session.delete(existing)
        bp.like_count = max(0, (bp.like_count or 0) - 1)
        liked = False
    else:
        like = BioPageLike(
            id=generate_id(),
            bio_page_id=bp.id,
            user_id=current_user.id,
        )
        db.session.add(like)
        bp.like_count = (bp.like_count or 0) + 1
        liked = True

        # Emit activity event
        _emit_activity(bp.user_id, ActivityEvent.EVENT_LIKES_MILESTONE, {
            'slug': bp.slug,
            'like_count': bp.like_count,
        })

        # Milestone alerts at 10, 50, 100, 250, 500, 1000
        milestones = {10, 50, 100, 250, 500, 1000}
        if bp.like_count in milestones:
            try:
                from app.models.notification import Notification
                notif = Notification(
                    id=generate_id(),
                    user_id=bp.user_id,
                    notification_type='likes_milestone',
                    title=f'Your bio page reached {bp.like_count} likes!',
                    body='Your professional page is gaining momentum.',
                    cta_url=f'/u/{bp.slug}',
                    cta_label='View page',
                    priority='normal',
                )
                db.session.add(notif)
            except Exception:
                pass

    try:
        db.session.commit()
        # Emit Bayesian signal asynchronously (best-effort)
        if liked and bp.simulation_id:
            try:
                _emit_bayesian_like(bp.user_id, bp.simulation_id)
                db.session.commit()
            except Exception:
                db.session.rollback()
    except Exception:
        db.session.rollback()
        return jsonify({'error': 'Failed to update like'}), 500

    return jsonify({'liked': liked, 'like_count': bp.like_count})


@social_bp.route('/api/bio/<slug>/like', methods=['GET'])
@login_required
def get_like_status(slug: str):
    """Return whether the current user has liked a bio page."""
    bp = BioPage.query.filter_by(slug=slug.lower()).first()
    if not bp:
        return jsonify({'liked': False, 'like_count': 0})
    liked = BioPageLike.query.filter_by(
        bio_page_id=bp.id, user_id=current_user.id
    ).first() is not None
    return jsonify({'liked': liked, 'like_count': bp.like_count or 0})


# ═══════════════════════════════════════════════════════
# Layer B — Connections
# ═══════════════════════════════════════════════════════

@social_bp.route('/api/social/connect/<slug>', methods=['POST'])
@login_required
def toggle_connection(slug: str):
    """FR-SOC-04: Instant connect/disconnect. Returns connected status and count."""
    from app.models.profile import UserProfile
    profile = UserProfile.query.filter_by(username=slug.lower()).first()
    if not profile:
        return jsonify({'error': 'User not found'}), 404
    target_id = profile.user_id
    if target_id == current_user.id:
        return jsonify({'error': 'Cannot connect to yourself'}), 400

    a, b = UserConnection.canonical(current_user.id, target_id)
    existing = UserConnection.query.filter_by(user_a_id=a, user_b_id=b).first()

    target_user = db.session.get(current_user.__class__, target_id)
    if not target_user:
        return jsonify({'error': 'User not found'}), 404

    if existing:
        db.session.delete(existing)
        current_user.connection_count = max(0, (current_user.connection_count or 0) - 1)
        target_user.connection_count = max(0, (target_user.connection_count or 0) - 1)
        connected = False
    else:
        conn = UserConnection(id=generate_id(), user_a_id=a, user_b_id=b)
        db.session.add(conn)
        current_user.connection_count = (current_user.connection_count or 0) + 1
        target_user.connection_count = (target_user.connection_count or 0) + 1
        connected = True

        _emit_activity(current_user.id, ActivityEvent.EVENT_CONNECTION_MADE, {
            'connected_to': slug,
            'connected_to_id': target_id,
        })
        _emit_activity(target_id, ActivityEvent.EVENT_CONNECTION_MADE, {
            'connected_to': current_user.id,
        })

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        return jsonify({'error': 'Failed to update connection'}), 500

    return jsonify({
        'connected': connected,
        'connection_count': current_user.connection_count,
    })


@social_bp.route('/api/social/connection-status/<slug>', methods=['GET'])
@login_required
def connection_status(slug: str):
    """Return connection status between current user and target slug."""
    from app.models.profile import UserProfile
    profile = UserProfile.query.filter_by(username=slug.lower()).first()
    if not profile:
        return jsonify({'connected': False, 'degree': None})
    target_id = profile.user_id
    if target_id == current_user.id:
        return jsonify({'connected': False, 'degree': 'self'})

    connected = UserConnection.are_connected(current_user.id, target_id)
    if connected:
        return jsonify({'connected': True, 'degree': 1})

    # Check 2nd degree
    second = UserConnection.second_degree_ids(current_user.id)
    if target_id in second:
        via = UserConnection.via_name(current_user.id, target_id)
        return jsonify({'connected': False, 'degree': 2, 'via': via})

    return jsonify({'connected': False, 'degree': None})


# ═══════════════════════════════════════════════════════
# Layer C — Activity Feed
# ═══════════════════════════════════════════════════════

def _emit_activity(user_id: str, event_type: str, metadata: dict):
    try:
        event = ActivityEvent(
            id=generate_id(),
            user_id=user_id,
            event_type=event_type,
        )
        event.event_data = metadata
        db.session.add(event)
        db.session.flush()
    except Exception as exc:
        logger.debug('Activity event emit failed: %s', exc)


@social_bp.route('/feed')
@login_required
def activity_feed():
    """FR-SOC-09: Read-only activity feed from 1st-degree connections."""
    page = request.args.get('page', 1, type=int)
    per_page = 20
    cutoff = datetime.utcnow() - timedelta(days=30)

    first_ids = UserConnection.first_degree_ids(current_user.id)

    if first_ids:
        from sqlalchemy import or_
        events_q = ActivityEvent.query.filter(
            ActivityEvent.user_id.in_(first_ids),
            ActivityEvent.created_at >= cutoff,
        ).order_by(ActivityEvent.created_at.desc())
    else:
        events_q = ActivityEvent.query.filter(ActivityEvent.id == None)  # empty

    paginated = events_q.paginate(page=page, per_page=per_page, error_out=False)

    # Enrich events with profile data
    from app.models.profile import UserProfile
    enriched = []
    for ev in paginated.items:
        profile = UserProfile.query.filter_by(user_id=ev.user_id).first()
        enriched.append({
            'event': ev.to_dict(),
            'actor_name': profile.display_name if profile else 'A connection',
            'actor_avatar': profile.avatar_path if profile else None,
            'actor_slug': profile.username if profile else None,
        })

    return render_template(
        'social/feed.html',
        events=enriched,
        page=page,
        has_more=paginated.has_next,
        connection_count=len(first_ids),
    )


@social_bp.route('/api/feed')
@login_required
def api_feed():
    """JSON feed for lazy-load pagination."""
    page = request.args.get('page', 1, type=int)
    per_page = 20
    cutoff = datetime.utcnow() - timedelta(days=30)

    first_ids = UserConnection.first_degree_ids(current_user.id)
    if first_ids:
        events_q = ActivityEvent.query.filter(
            ActivityEvent.user_id.in_(first_ids),
            ActivityEvent.created_at >= cutoff,
        ).order_by(ActivityEvent.created_at.desc())
    else:
        return jsonify({'events': [], 'has_more': False})

    paginated = events_q.paginate(page=page, per_page=per_page, error_out=False)

    from app.models.profile import UserProfile
    enriched = []
    for ev in paginated.items:
        profile = UserProfile.query.filter_by(user_id=ev.user_id).first()
        d = ev.to_dict()
        d['actor_name'] = profile.display_name if profile else 'A connection'
        d['actor_avatar'] = profile.avatar_path if profile else None
        d['actor_slug'] = profile.username if profile else None
        enriched.append(d)

    return jsonify({'events': enriched, 'has_more': paginated.has_next})


# ═══════════════════════════════════════════════════════
# Layer D — Platform Chat
# ═══════════════════════════════════════════════════════

def _get_or_create_platform_chat(owner_user_id: str, bio_page_id: str,
                                  chatter_user_id: str) -> PlatformChat:
    """Find existing chat or create a new one. FR-SOC-13."""
    a_uid, b_uid = owner_user_id, chatter_user_id
    chat = PlatformChat.query.filter_by(
        owner_user_id=a_uid, chatter_user_id=b_uid
    ).first()
    if not chat:
        chat = PlatformChat(
            id=generate_id(),
            owner_user_id=a_uid,
            bio_page_id=bio_page_id,
            chatter_user_id=b_uid,
        )
        db.session.add(chat)
        db.session.flush()
        # Auto-create CRM contact for owner. FR-SOC-12.
        _ensure_platform_chat_contact(chat)
    return chat


def _ensure_platform_chat_contact(chat: PlatformChat):
    """Create or find CRM contact in owner's CRM. source='platform_chat'. FR-SOC-12."""
    try:
        from app.models.contact import Contact, ContactActivity
        from app.models.user import User
        chatter = User.query.get(chat.chatter_user_id)
        if not chatter:
            return
        email = chatter.email.lower().strip()
        existing = Contact.query.filter_by(
            user_id=chat.owner_user_id, email=email
        ).first()
        if existing:
            if not chat.contact_id:
                chat.contact_id = existing.id
            activity = ContactActivity(
                id=generate_id(),
                contact_id=existing.id,
                activity_type='platform_chat_started',
                created_by='platform',
            )
            db.session.add(activity)
            db.session.flush()
            return
        parts = chatter.full_name.strip().split(' ', 1)
        contact = Contact(
            id=generate_id(),
            user_id=chat.owner_user_id,
            first_name=parts[0],
            last_name=parts[1] if len(parts) > 1 else '',
            email=email,
            source='platform_chat',
            source_notes='Started a platform chat via Simulacrum',
            pipeline_stage='prospect',
        )
        db.session.add(contact)
        db.session.flush()
        chat.contact_id = contact.id
    except Exception as exc:
        logger.warning('Platform chat contact creation failed: %s', exc)


def _build_platform_history(chat_id: str, limit: int = 20) -> list:
    msgs = (
        PlatformChatMessage.query
        .filter_by(chat_id=chat_id)
        .order_by(PlatformChatMessage.created_at.asc())
        .limit(limit)
        .all()
    )
    return [{'role': m.role, 'content': m.content} for m in msgs]


def _assemble_platform_system_prompt(bp: BioPage, chatter_name: str) -> str:
    from app.blueprints.bio.routes import _assemble_context
    from app.models.user import User
    ctx = _assemble_context(bp.user_id, bp)
    owner = User.query.get(bp.user_id)
    owner_first = (owner.full_name or '').split()[0] if owner else 'your host'
    chatter_first = (chatter_name or '').split()[0] or 'there'

    services_text = (ctx.get('_rate_card_raw') or 'Contact for details.')[:1500]
    about_text = (ctx.get('about_text') or '')[:1000]

    return _PLATFORM_SYSTEM_PROMPT.format(
        owner_first_name=owner_first,
        chatter_name=chatter_name,
        chatter_first_name=chatter_first,
        professional_title=ctx.get('professional_title') or 'Professional',
        positioning=ctx.get('positioning') or '',
        services_text=services_text,
        booking_url=ctx.get('booking_url') or 'Use the contact form to get started.',
        about_text=about_text,
    )


@social_bp.route('/api/platform-chat/<slug>/start', methods=['POST'])
@login_required
def start_platform_chat(slug: str):
    """
    FR-SOC-11, FR-SOC-12: Start or resume a platform chat with owner at slug.
    Returns chat_id + existing message history.
    """
    from app.models.profile import UserProfile
    from app.models.user import User
    profile = UserProfile.query.filter_by(username=slug.lower()).first()
    if not profile:
        return jsonify({'error': 'User not found'}), 404

    owner_id = profile.user_id
    if owner_id == current_user.id:
        return jsonify({'error': 'Cannot chat with yourself'}), 400

    bp = BioPage.query.filter_by(
        user_id=owner_id, status=BioPage.STATUS_PUBLISHED
    ).first()
    if not bp:
        return jsonify({'error': 'This user has no published bio page'}), 404

    try:
        chat = _get_or_create_platform_chat(owner_id, bp.id, current_user.id)
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        logger.error('start_platform_chat failed: %s', exc)
        return jsonify({'error': 'Failed to start chat'}), 500

    owner = User.query.get(owner_id)
    owner_first = (owner.full_name or '').split()[0] if owner else slug
    chatter_first = (current_user.full_name or '').split()[0]

    messages = _build_platform_history(chat.id)

    welcome = None
    if not messages:
        welcome = (
            f"Hi {chatter_first}! I'm {owner_first}'s assistant. "
            f"I can answer questions about their services, expertise, and availability. "
            f"What would you like to know?"
        )

    return jsonify({
        'chat_id': chat.id,
        'owner_slug': slug,
        'owner_name': owner.full_name if owner else slug,
        'messages': messages,
        'welcome_message': welcome,
    })


@social_bp.route('/api/platform-chat/<chat_id>/message', methods=['POST'])
@login_required
def send_platform_message(chat_id: str):
    """FR-SOC-11: Send a message; stream AI response via SSE."""
    chat = PlatformChat.query.filter_by(
        id=chat_id, chatter_user_id=current_user.id,
        status=PlatformChat.STATUS_ACTIVE,
    ).first()
    if not chat:
        return jsonify({'error': 'Chat not found'}), 404

    bp = BioPage.query.get(chat.bio_page_id)
    if not bp:
        return jsonify({'error': 'Bio page not found'}), 404

    data = request.get_json(force=True, silent=True) or {}
    user_message = (data.get('message') or '').strip()[:2000]
    if not user_message:
        return jsonify({'error': 'message required'}), 400

    user_msg = PlatformChatMessage(
        id=generate_id(),
        chat_id=chat_id,
        role=PlatformChatMessage.ROLE_USER,
        content=user_message,
    )
    db.session.add(user_msg)
    chat.message_count += 1
    chat.last_message_at = datetime.utcnow()
    db.session.commit()

    system_prompt = _assemble_platform_system_prompt(bp, current_user.full_name or 'there')
    history = _build_platform_history(chat_id)
    history.append({'role': 'user', 'content': user_message})

    # Reuse bio chat tools (same engine)
    from app.blueprints.bio_chat.routes import CHAT_TOOLS, _execute_chat_tool, _classify_complexity

    # Wrap execute_chat_tool for platform context (no BioChatSession — use a stub)
    class _PlatformSessionStub:
        visitor_name = current_user.full_name or 'Member'
        visitor_email = current_user.email

    stub_session = _PlatformSessionStub()

    complexity = _classify_complexity(user_message)
    model = _SONNET_MODEL if complexity == 'complex' else _HAIKU_MODEL

    def generate():
        import anthropic
        client = anthropic.Anthropic(api_key=current_app.config['CLAUDE_API_KEY'])
        full_text = ''
        in_tokens = out_tokens = 0

        try:
            with client.messages.stream(
                model=model,
                max_tokens=512,
                system=system_prompt,
                tools=CHAT_TOOLS,
                messages=history,
            ) as stream:
                for text in stream.text_stream:
                    full_text += text
                    yield f'data: {json.dumps({"text": text})}\n\n'
                first_msg = stream.get_final_message()
                in_tokens = first_msg.usage.input_tokens
                out_tokens = first_msg.usage.output_tokens

            if first_msg.stop_reason == 'tool_use':
                tool_results = []
                for block in first_msg.content:
                    if block.type == 'tool_use':
                        yield f'data: {json.dumps({"tool_call": block.name})}\n\n'
                        result_str = _execute_chat_tool(block.name, block.input, stub_session, bp)
                        tool_results.append({
                            'type': 'tool_result',
                            'tool_use_id': block.id,
                            'content': result_str,
                        })
                        # Record tool call
                        tc = chat.tool_calls
                        tc.append({'name': block.name, 'input': block.input, 'at': datetime.utcnow().isoformat()})
                        chat.tool_calls = tc

                follow_messages = list(history) + [
                    {'role': 'assistant', 'content': first_msg.content},
                    {'role': 'user', 'content': tool_results},
                ]
                with client.messages.stream(
                    model=model,
                    max_tokens=512,
                    system=system_prompt,
                    messages=follow_messages,
                ) as stream2:
                    for text in stream2.text_stream:
                        full_text += text
                        yield f'data: {json.dumps({"text": text})}\n\n'
                    final2 = stream2.get_final_message()
                    out_tokens += final2.usage.output_tokens

        except GeneratorExit:
            return
        except Exception as exc:
            logger.error('Platform chat stream error: %s', exc)
            yield f'data: {json.dumps({"error": "Stream error"})}\n\n'
            return

        try:
            ai_msg = PlatformChatMessage(
                id=generate_id(),
                chat_id=chat_id,
                role=PlatformChatMessage.ROLE_ASSISTANT,
                content=full_text,
                model_used=model,
                tokens_input=in_tokens,
                tokens_output=out_tokens,
            )
            db.session.add(ai_msg)
            chat.message_count += 1
            chat.total_tokens += in_tokens + out_tokens
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            logger.error('Platform chat persist failed: %s', exc)

        yield f'data: {json.dumps({"done": True, "model": model})}\n\n'

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
    )


@social_bp.route('/api/platform-chat/<chat_id>/messages', methods=['GET'])
@login_required
def get_platform_chat_messages(chat_id: str):
    """FR-SOC-13: Full message history for a platform chat."""
    chat = PlatformChat.query.filter(
        PlatformChat.id == chat_id,
        db.or_(
            PlatformChat.chatter_user_id == current_user.id,
            PlatformChat.owner_user_id == current_user.id,
        ),
    ).first()
    if not chat:
        return jsonify({'error': 'Not found'}), 404

    msgs = (
        PlatformChatMessage.query
        .filter_by(chat_id=chat_id)
        .order_by(PlatformChatMessage.created_at.asc())
        .all()
    )
    return jsonify({
        'chat': chat.to_dict(),
        'messages': [m.to_dict() for m in msgs],
    })


@social_bp.route('/my-chats')
@login_required
def my_chats():
    """FR-SOC-13: My Chats page — all active platform chats for the current user."""
    chats = (
        PlatformChat.query
        .filter_by(chatter_user_id=current_user.id)
        .order_by(PlatformChat.updated_at.desc())
        .limit(50)
        .all()
    )

    from app.models.profile import UserProfile
    from app.models.user import User
    enriched = []
    for c in chats:
        owner = User.query.get(c.owner_user_id)
        profile = UserProfile.query.filter_by(user_id=c.owner_user_id).first()
        last_msg = (
            PlatformChatMessage.query
            .filter_by(chat_id=c.id)
            .order_by(PlatformChatMessage.created_at.desc())
            .first()
        )
        enriched.append({
            'chat': c.to_dict(),
            'owner_name': owner.full_name if owner else 'Unknown',
            'owner_avatar': profile.avatar_path if profile else None,
            'owner_slug': profile.username if profile else None,
            'last_message': last_msg.content[:100] if last_msg else '',
            'last_message_role': last_msg.role if last_msg else None,
            'last_message_at': last_msg.created_at.isoformat() if last_msg else c.created_at.isoformat(),
        })

    return render_template('social/my_chats.html', chats=enriched)
