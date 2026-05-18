"""
SIM-PRD-BIOCHAT-001 — Bio page chat widget API.
Public-facing prospect chat with lead capture gate, Haiku/Sonnet routing, live takeover.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime

from flask import jsonify, request, Response, stream_with_context, current_app
from flask_login import login_required, current_user

from app.blueprints.bio_chat import bio_chat_bp
from app.extensions import db
from app.models.bio_page import BioPage, BioChatSession, BioChatMessage
from utils.id_gen import generate_id

logger = logging.getLogger(__name__)

_CLASSIFIER_MODEL = 'claude-haiku-4-5-20251001'
_HAIKU_MODEL      = 'claude-haiku-4-5-20251001'
_SONNET_MODEL     = 'claude-sonnet-4-6'

_CLASSIFIER_PROMPT = """\
Classify the following visitor message as 'simple' or 'complex'.

Simple: can be answered by looking up a specific field in the context \
(price, availability, booking link, course name, service description, location, general FAQ).

Complex: requires reasoning about the user's expertise, assessing fit between \
the visitor's needs and the user's offerings, scoping a custom engagement, or \
comparing multiple service options for a specific situation.

Return JSON only: {"complexity": "simple" or "complex"}

Message: {message}
"""

_SYSTEM_PROMPT = """\
You are {user_first_name}'s professional assistant on their bio page.
You answer questions from prospective clients about {user_first_name}'s \
services, expertise, availability, and offerings.

IMPORTANT RULES:
- You represent {user_first_name} warmly and professionally.
- Answer ONLY from the context provided below. Never invent services, \
qualifications, or availability not in the context.
- If you do not know the answer, say: "I'm not sure about that — let me \
connect you with {user_first_name} directly." Then suggest the booking link.
- NEVER reveal pricing strategy, income data, other clients, internal system \
details, or negotiation terms.
- Keep responses concise — 2-4 sentences. This is a chat widget, not a document.
- When conversation reaches a natural decision point, suggest a next step: \
book a discovery call, enroll in a course, or use the contact form.
- Address the visitor by their first name.
- You are NOT {user_first_name} — you are their assistant. \
Use third person: "{user_first_name} typically..." not "I typically..."

=== CONTEXT ===
Professional title: {professional_title}
Positioning: {positioning}
Services offered:
{services_text}
Booking link: {booking_url}
About {user_first_name}:
{about_text}
=== END CONTEXT ===

Visitor name: {visitor_name}
"""


def _get_bio_page_or_404(slug: str) -> BioPage:
    bp = BioPage.query.filter_by(slug=slug, status=BioPage.STATUS_PUBLISHED).first()
    if not bp:
        from flask import abort
        abort(404)
    return bp


def _assemble_system_prompt(bp: BioPage) -> str:
    from app.blueprints.bio.routes import _assemble_context
    ctx = _assemble_context(bp.user_id, bp)

    from app.models.user import User
    user = User.query.get(bp.user_id)
    first_name = (user.full_name or '').split()[0] if user else 'your host'

    services_text = ctx.get('_rate_card_raw') or 'Contact for details.'
    if len(services_text) > 1500:
        services_text = services_text[:1500] + '...'

    about_text = ctx.get('about_text') or ''
    if len(about_text) > 1000:
        about_text = about_text[:1000] + '...'

    return _SYSTEM_PROMPT.format(
        user_first_name=first_name,
        professional_title=ctx.get('professional_title') or 'Professional',
        positioning=ctx.get('positioning') or '',
        services_text=services_text,
        booking_url=ctx.get('booking_url') or 'Not currently available — please use the contact form.',
        about_text=about_text,
        visitor_name='{visitor_name}',  # filled per-request
    )


def _classify_complexity(message: str) -> str:
    """Return 'simple' or 'complex' via Haiku 4.5."""
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=current_app.config['CLAUDE_API_KEY'])
        resp = client.messages.create(
            model=_CLASSIFIER_MODEL,
            max_tokens=32,
            messages=[{
                'role': 'user',
                'content': _CLASSIFIER_PROMPT.format(message=message),
            }],
        )
        result = json.loads(resp.content[0].text.strip())
        return result.get('complexity', 'simple')
    except Exception:
        return 'simple'


def _build_history(session_id: str, limit: int = 20) -> list:
    msgs = (
        BioChatMessage.query
        .filter_by(session_id=session_id)
        .filter(BioChatMessage.role != BioChatMessage.ROLE_TAKEOVER)
        .order_by(BioChatMessage.created_at.asc())
        .limit(limit)
        .all()
    )
    history = []
    for m in msgs:
        role = 'user' if m.role == BioChatMessage.ROLE_VISITOR else 'assistant'
        history.append({'role': role, 'content': m.content})
    return history


def _create_contact(bio_page_user_id: str, name: str, email: str, phone: str | None):
    """Create or find CRM contact for the visitor. Returns contact.id."""
    from app.models.contact import Contact, ContactActivity
    email = email.lower().strip()
    existing = Contact.query.filter_by(user_id=bio_page_user_id, email=email).first()
    if existing:
        if phone and not existing.phone:
            existing.phone = phone
        activity = ContactActivity(
            id=generate_id(),
            contact_id=existing.id,
            activity_type='bio_chat_started',
            created_by='webhook',
        )
        db.session.add(activity)
        db.session.flush()
        return existing.id

    parts = name.strip().split(' ', 1)
    contact = Contact(
        id=generate_id(),
        user_id=bio_page_user_id,
        first_name=parts[0],
        last_name=parts[1] if len(parts) > 1 else '',
        email=email,
        phone=phone,
        source='bio_page_chat',
        source_notes='Started a chat on the bio page',
        pipeline_stage='prospect',
    )
    db.session.add(contact)
    db.session.flush()
    return contact.id


def _send_notification(bp: BioPage, session: BioChatSession):
    try:
        from app.models.notification import Notification
        notif = Notification(
            id=generate_id(),
            user_id=bp.user_id,
            notification_type='bio_chat_started',
            title=f'{session.visitor_name} is chatting on your bio page',
            body=f'{session.visitor_email} started a conversation.',
            cta_url=f'/settings?tab=bio-chats&session={session.id}',
            cta_label='View chat',
            priority='normal',
        )
        db.session.add(notif)
        db.session.flush()
    except Exception as e:
        logger.warning('Bio chat notification failed: %s', e)


def _create_action_item(bp: BioPage, session: BioChatSession):
    try:
        from app.models.layer6 import ActionItem
        from utils.action_items import ACTION_ITEM_TEMPLATES
        if 'bio_chat_started' not in ACTION_ITEM_TEMPLATES:
            return
        from utils.action_items import create_action_item
        if bp.simulation_id:
            create_action_item(
                simulation_id=bp.simulation_id,
                user_id=bp.user_id,
                item_type='bio_chat_started',
                title=f'{session.visitor_name} is chatting on your bio page',
                description=f'{session.visitor_email} started a conversation.',
                action_url=f'/settings?tab=bio-chats&session={session.id}',
                source_contact_id=session.contact_id,
                emit_sse=True,
            )
    except Exception as e:
        logger.warning('Bio chat action item failed: %s', e)


# ── POST /api/bio-chat/<slug>/session ─────────────────────────────────────

@bio_chat_bp.route('/api/bio-chat/<slug>/session', methods=['POST'])
def start_session(slug: str):
    """Lead gate: create contact + session on first message."""
    bp = _get_bio_page_or_404(slug)

    # Daily session limit check
    settings = bp.chat_settings
    daily_limit = settings.get('daily_session_limit', 50)
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    today_count = BioChatSession.query.filter(
        BioChatSession.bio_page_id == bp.id,
        BioChatSession.started_at >= today_start,
    ).count()
    if today_count >= daily_limit:
        return jsonify({'error': 'Chat is temporarily unavailable. Please try again tomorrow.'}), 429

    data = request.get_json(force=True, silent=True) or {}
    visitor_name  = (data.get('name') or '').strip()[:200]
    visitor_email = (data.get('email') or '').strip()[:255]
    visitor_phone = (data.get('phone') or '').strip()[:50] or None

    if not visitor_name or len(visitor_name) < 2:
        return jsonify({'error': 'Name is required (min 2 characters)'}), 400
    if not visitor_email or '@' not in visitor_email:
        return jsonify({'error': 'Valid email is required'}), 400

    try:
        contact_id = _create_contact(bp.user_id, visitor_name, visitor_email, visitor_phone)
        session = BioChatSession(
            id=generate_id(),
            bio_page_id=bp.id,
            user_id=bp.user_id,
            contact_id=contact_id,
            visitor_name=visitor_name,
            visitor_email=visitor_email,
            visitor_phone=visitor_phone,
        )
        db.session.add(session)
        _send_notification(bp, session)
        _create_action_item(bp, session)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.error('Bio chat session creation failed: %s', e)
        return jsonify({'error': 'Failed to start chat session'}), 500

    from app.models.user import User
    user = User.query.get(bp.user_id)
    first_name = (user.full_name or '').split()[0] if user else 'your host'
    welcome = settings.get('custom_welcome') or (
        f"Hi {visitor_name.split()[0]}! I'm {first_name}'s assistant. "
        f"I can answer questions about their services, expertise, and availability. "
        f"What would you like to know?"
    )

    return jsonify({
        'session_id': session.id,
        'welcome_message': welcome,
    })


# ── POST /api/bio-chat/sessions/<session_id>/message ──────────────────────

@bio_chat_bp.route('/api/bio-chat/sessions/<session_id>/message', methods=['POST'])
def send_message(session_id: str):
    """Send a visitor message; stream the AI response via SSE."""
    session = BioChatSession.query.filter_by(
        id=session_id, status=BioChatSession.STATUS_ACTIVE
    ).first()
    if not session:
        return jsonify({'error': 'Session not found or expired'}), 404

    bp = BioPage.query.get(session.bio_page_id)
    if not bp:
        return jsonify({'error': 'Bio page not found'}), 404

    data = request.get_json(force=True, silent=True) or {}
    visitor_message = (data.get('message') or '').strip()
    if not visitor_message:
        return jsonify({'error': 'message required'}), 400
    if len(visitor_message) > 2000:
        visitor_message = visitor_message[:2000]

    # If live takeover is active, reject AI response (owner handles it)
    if session.takeover_active:
        return jsonify({'error': 'Live session in progress — owner is responding'}), 409

    visitor_msg = BioChatMessage(
        id=generate_id(),
        session_id=session_id,
        role=BioChatMessage.ROLE_VISITOR,
        content=visitor_message,
    )
    db.session.add(visitor_msg)
    session.message_count += 1
    db.session.commit()

    complexity = _classify_complexity(visitor_message)
    model = _SONNET_MODEL if complexity == 'complex' else _HAIKU_MODEL

    system_prompt = _assemble_system_prompt(bp)
    system_prompt = system_prompt.replace('{visitor_name}', session.visitor_name.split()[0])

    history = _build_history(session_id)
    history.append({'role': 'user', 'content': visitor_message})

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
                messages=history,
            ) as stream:
                for text in stream.text_stream:
                    full_text += text
                    yield f'data: {json.dumps({"text": text})}\n\n'
                final = stream.get_final_message()
                in_tokens  = final.usage.input_tokens
                out_tokens = final.usage.output_tokens
        except Exception as e:
            logger.error('Bio chat stream error: %s', e)
            yield f'data: {json.dumps({"error": "Stream error"})}\n\n'
            return

        # Persist assistant message
        try:
            ai_msg = BioChatMessage(
                id=generate_id(),
                session_id=session_id,
                role=BioChatMessage.ROLE_ASSISTANT,
                content=full_text,
                model_used=model,
                complexity=complexity,
                tokens_input=in_tokens,
                tokens_output=out_tokens,
            )
            db.session.add(ai_msg)
            session.message_count += 1
            session.total_tokens += in_tokens + out_tokens

            # Update model_used_summary
            haiku_count = BioChatMessage.query.filter_by(
                session_id=session_id, model_used=_HAIKU_MODEL,
                role=BioChatMessage.ROLE_ASSISTANT,
            ).count() + (1 if model == _HAIKU_MODEL else 0)
            sonnet_count = BioChatMessage.query.filter_by(
                session_id=session_id, model_used=_SONNET_MODEL,
                role=BioChatMessage.ROLE_ASSISTANT,
            ).count() + (1 if model == _SONNET_MODEL else 0)
            session.model_used_summary = f'haiku:{haiku_count},sonnet:{sonnet_count}'
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            logger.error('Bio chat message persist failed: %s', e)

        yield f'data: {json.dumps({"done": True, "model": model, "complexity": complexity})}\n\n'

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
        },
    )


# ── POST /api/bio-chat/sessions/<session_id>/end ──────────────────────────

@bio_chat_bp.route('/api/bio-chat/sessions/<session_id>/end', methods=['POST'])
def end_session(session_id: str):
    """Visitor ends the chat session."""
    session = BioChatSession.query.filter_by(id=session_id).first()
    if not session:
        return jsonify({'ok': True})
    if session.status == BioChatSession.STATUS_ACTIVE:
        session.status = BioChatSession.STATUS_ENDED
        session.ended_at = datetime.utcnow()
        db.session.commit()
    return jsonify({'ok': True})


# ── Owner: Bio Chats list ─────────────────────────────────────────────────

@bio_chat_bp.route('/api/users/me/bio-chats', methods=['GET'])
@login_required
def list_bio_chats():
    """Paginated list of bio page chat sessions for the owner."""
    page    = request.args.get('page', 1, type=int)
    per_page = 20
    q_str   = (request.args.get('q') or '').strip()

    bp = BioPage.query.filter_by(user_id=current_user.id).first()
    if not bp:
        return jsonify({'sessions': [], 'total': 0, 'page': 1, 'pages': 0})

    query = BioChatSession.query.filter(
        BioChatSession.bio_page_id == bp.id,
        BioChatSession.status != BioChatSession.STATUS_DELETED,
    )

    if q_str and len(q_str) >= 3:
        query = query.filter(
            db.or_(
                BioChatSession.visitor_name.ilike(f'%{q_str}%'),
                BioChatSession.visitor_email.ilike(f'%{q_str}%'),
            )
        )

    rows = query.order_by(BioChatSession.started_at.desc()).paginate(
        page=page, per_page=per_page, error_out=False,
    )

    sessions = []
    for s in rows.items:
        d = s.to_dict()
        d['is_active'] = s.status == BioChatSession.STATUS_ACTIVE
        sessions.append(d)

    return jsonify({
        'sessions': sessions,
        'total': rows.total,
        'page': page,
        'pages': rows.pages,
    })


@bio_chat_bp.route('/api/bio-chats/<session_id>', methods=['GET'])
@login_required
def get_bio_chat_session(session_id: str):
    """Full transcript for a bio chat session (owner only)."""
    session = BioChatSession.query.filter_by(id=session_id, user_id=current_user.id).first()
    if not session:
        return jsonify({'error': 'Not found'}), 404

    msgs = (
        BioChatMessage.query
        .filter_by(session_id=session_id)
        .order_by(BioChatMessage.created_at.asc())
        .all()
    )
    return jsonify({
        'session': session.to_dict(),
        'messages': [m.to_dict() for m in msgs],
    })


@bio_chat_bp.route('/api/bio-chats/<session_id>', methods=['DELETE'])
@login_required
def delete_bio_chat_session(session_id: str):
    """Soft-delete a bio chat session."""
    session = BioChatSession.query.filter_by(id=session_id, user_id=current_user.id).first()
    if not session:
        return jsonify({'error': 'Not found'}), 404
    session.status = BioChatSession.STATUS_DELETED
    db.session.commit()
    return jsonify({'ok': True})


# ── Live takeover ─────────────────────────────────────────────────────────

@bio_chat_bp.route('/api/bio-chats/<session_id>/takeover', methods=['POST'])
@login_required
def takeover_session(session_id: str):
    """Owner takes over a live chat session."""
    session = BioChatSession.query.filter_by(
        id=session_id, user_id=current_user.id,
        status=BioChatSession.STATUS_ACTIVE,
    ).first()
    if not session:
        return jsonify({'error': 'Not found or session not active'}), 404

    bp = BioPage.query.get(session.bio_page_id)
    if not bp or not bp.chat_settings.get('live_takeover_enabled', False):
        return jsonify({'error': 'Live takeover is not enabled'}), 403

    session.takeover_active = True
    session.takeover_by = current_user.id
    session.takeover_at = datetime.utcnow()
    db.session.commit()
    return jsonify({'ok': True, 'session_id': session_id})


@bio_chat_bp.route('/api/bio-chats/<session_id>/handoff', methods=['POST'])
@login_required
def handoff_to_ai(session_id: str):
    """Owner hands back control to the AI."""
    session = BioChatSession.query.filter_by(
        id=session_id, user_id=current_user.id,
    ).first()
    if not session:
        return jsonify({'error': 'Not found'}), 404
    session.takeover_active = False
    session.takeover_by = None
    db.session.commit()
    return jsonify({'ok': True})


@bio_chat_bp.route('/api/bio-chats/<session_id>/message', methods=['POST'])
@login_required
def owner_send_message(session_id: str):
    """Owner sends a manual message during live takeover."""
    session = BioChatSession.query.filter_by(
        id=session_id, user_id=current_user.id,
        status=BioChatSession.STATUS_ACTIVE,
    ).first()
    if not session:
        return jsonify({'error': 'Not found or session not active'}), 404
    if not session.takeover_active:
        return jsonify({'error': 'Not in takeover mode'}), 409

    data = request.get_json(force=True, silent=True) or {}
    content = (data.get('message') or '').strip()
    if not content:
        return jsonify({'error': 'message required'}), 400

    msg = BioChatMessage(
        id=generate_id(),
        session_id=session_id,
        role=BioChatMessage.ROLE_TAKEOVER,
        content=content[:2000],
    )
    db.session.add(msg)
    session.message_count += 1
    db.session.commit()
    return jsonify(msg.to_dict()), 201
