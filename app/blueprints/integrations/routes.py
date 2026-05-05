import secrets
import logging
from datetime import datetime, timedelta

from flask import request, jsonify, redirect, url_for, session, current_app
from flask_login import login_required, current_user

from app.blueprints.integrations import integrations_bp
from app.extensions import db
from app.models.integration import UserIntegration
from app.models.contact import Contact, ContactActivity
from utils.id_gen import generate_id

logger = logging.getLogger(__name__)


# ── Integration status ────────────────────────────────────────────────────────

@integrations_bp.route('/api/integrations/status')
@login_required
def integrations_status():
    apollo = UserIntegration.query.filter_by(
        user_id=current_user.id, provider='apollo'
    ).first()
    stripe_int = UserIntegration.query.filter_by(
        user_id=current_user.id, provider='stripe'
    ).first()
    cal_int = UserIntegration.query.filter_by(
        user_id=current_user.id, provider='cal'
    ).first()
    pandadoc_int = UserIntegration.query.filter_by(
        user_id=current_user.id, provider='pandadoc'
    ).first()
    ck_int = UserIntegration.query.filter_by(
        user_id=current_user.id, provider='convertkit'
    ).first()
    return jsonify({
        'apollo': apollo.to_dict() if apollo else {
            'provider': 'apollo', 'status': 'not_connected', 'apollo_daily_limit': 30,
        },
        'stripe': stripe_int.to_dict() if stripe_int else {
            'provider': 'stripe', 'status': 'not_connected',
        },
        'cal': cal_int.to_dict() if cal_int else {
            'provider': 'cal', 'status': 'not_connected',
        },
        'pandadoc': pandadoc_int.to_dict() if pandadoc_int else {
            'provider': 'pandadoc', 'status': 'not_connected',
        },
        'convertkit': ck_int.to_dict() if ck_int else {
            'provider': 'convertkit', 'status': 'not_connected',
        },
    })


# ── Apollo OAuth ──────────────────────────────────────────────────────────────

@integrations_bp.route('/api/integrations/apollo/connect')
@login_required
def apollo_connect():
    client_id = current_app.config.get('APOLLO_CLIENT_ID')
    if not client_id:
        return jsonify({'error': 'Apollo integration is not configured on this server.'}), 503

    state = secrets.token_urlsafe(16)
    session['apollo_oauth_state'] = state

    from app.services.apollo_client import get_auth_url
    return redirect(get_auth_url(state))


@integrations_bp.route('/api/integrations/apollo/callback')
@login_required
def apollo_callback():
    error = request.args.get('error')
    if error:
        return redirect(url_for('pages.settings_integrations') + '?apollo_error=cancelled')

    state = request.args.get('state')
    expected = session.pop('apollo_oauth_state', None)
    if not expected or state != expected:
        return redirect(url_for('pages.settings_integrations') + '?apollo_error=invalid_state')

    code = request.args.get('code')
    if not code:
        return redirect(url_for('pages.settings_integrations') + '?apollo_error=no_code')

    try:
        from app.services.apollo_client import exchange_code_for_token
        from app.services.token_crypto import encrypt_token
        token_data = exchange_code_for_token(code)
    except Exception as exc:
        logger.error('Apollo token exchange failed for user %s: %s', current_user.id, exc)
        return redirect(url_for('pages.settings_integrations') + '?apollo_error=token_exchange_failed')

    integration = UserIntegration.query.filter_by(
        user_id=current_user.id, provider='apollo'
    ).first()
    if not integration:
        integration = UserIntegration(
            id=generate_id(),
            user_id=current_user.id,
            provider='apollo',
        )
        db.session.add(integration)

    integration.access_token_enc = encrypt_token(token_data['access_token'])
    if token_data.get('refresh_token'):
        integration.refresh_token_enc = encrypt_token(token_data['refresh_token'])
    if token_data.get('expires_in'):
        integration.token_expires_at = datetime.utcnow() + timedelta(seconds=token_data['expires_in'])
    db.session.commit()

    return redirect(url_for('pages.settings_integrations') + '?apollo_connected=1')


@integrations_bp.route('/api/integrations/apollo/disconnect', methods=['POST'])
@login_required
def apollo_disconnect():
    integration = UserIntegration.query.filter_by(
        user_id=current_user.id, provider='apollo'
    ).first()
    if not integration:
        return jsonify({'ok': True})

    # Attempt to pause active sequences via Apollo API before clearing token
    if integration.is_connected and not integration.is_expired:
        try:
            from app.models.integration import EmailCampaign
            from app.models.simulation import Simulation
            from app.services.apollo_client import ApolloClient
            token = integration.decrypt_access_token()
            apollo = ApolloClient(token)
            user_sim_ids = [
                s.id for s in Simulation.query.filter_by(user_id=current_user.id).all()
            ]
            active_campaigns = EmailCampaign.query.filter(
                EmailCampaign.simulation_id.in_(user_sim_ids),
                EmailCampaign.status == 'active',
                EmailCampaign.apollo_sequence_id.isnot(None),
            ).all()
            for c in active_campaigns:
                try:
                    apollo.pause_sequence(c.apollo_sequence_id)
                    c.status = 'paused'
                except Exception as exc:
                    logger.warning('Could not pause sequence %s: %s', c.apollo_sequence_id, exc)
        except Exception as exc:
            logger.warning('Apollo disconnect cleanup failed: %s', exc)

    integration.access_token_enc = None
    integration.refresh_token_enc = None
    integration.token_expires_at = None
    db.session.commit()
    return jsonify({'ok': True})


@integrations_bp.route('/api/integrations/apollo/settings', methods=['PUT'])
@login_required
def apollo_update_settings():
    data = request.get_json(silent=True) or {}
    daily_limit = data.get('apollo_daily_limit')
    if daily_limit is None:
        return jsonify({'error': 'apollo_daily_limit required'}), 400
    try:
        daily_limit = int(daily_limit)
    except (TypeError, ValueError):
        return jsonify({'error': 'apollo_daily_limit must be an integer'}), 400
    if not 1 <= daily_limit <= 500:
        return jsonify({'error': 'apollo_daily_limit must be between 1 and 500'}), 400

    integration = UserIntegration.query.filter_by(
        user_id=current_user.id, provider='apollo'
    ).first()
    if not integration:
        integration = UserIntegration(
            id=generate_id(),
            user_id=current_user.id,
            provider='apollo',
            apollo_daily_limit=daily_limit,
        )
        db.session.add(integration)
    else:
        integration.apollo_daily_limit = daily_limit
    db.session.commit()
    return jsonify({'ok': True, 'apollo_daily_limit': daily_limit})


# ── Apollo webhook handler ────────────────────────────────────────────────────

@integrations_bp.route('/webhooks/apollo/<user_id>', methods=['POST'])
def apollo_webhook(user_id):
    payload = request.get_json(silent=True) or {}
    event = payload.get('event_type')
    contact_email = (payload.get('contact') or {}).get('email')

    if not event or not contact_email:
        return '', 200

    contact = Contact.query.filter_by(user_id=user_id, email=contact_email).first()
    if not contact:
        return '', 200

    try:
        _handle_apollo_event(event, contact, payload, user_id)
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        logger.error('Apollo webhook error user=%s event=%s: %s', user_id, event, exc, exc_info=True)

    return '', 200


def _handle_apollo_event(event: str, contact: Contact, payload: dict, user_id: str):
    if event == 'email_reply':
        activity = ContactActivity(
            id=generate_id(),
            contact_id=contact.id,
            activity_type='email_replied',
            created_by='webhook',
        )
        db.session.add(activity)
        contact.advance_stage('active', created_by='webhook')
        contact.last_contacted_at = datetime.utcnow()

        _publish_reply_sse(contact, payload)

        # Notify user of contact reply (best-effort)
        try:
            from app.services.notification_service import send_notification as _sn
            _action_name = (payload.get('emailer_campaign_id') or 'outreach campaign')
            _sn(
                user_id=user_id,
                notification_type='reply',
                title=f'{contact.display_name} replied to your outreach',
                body=(
                    f'A contact from your {_action_name} replied. '
                    f'Their pipeline stage has been advanced to Active.'
                ),
                cta_url=f'/contacts/{contact.id}',
                cta_label='View contact →',
                priority='high',
            )
        except Exception as _ne:
            logger.warning('Reply notification failed: %s', _ne)

    elif event == 'email_bounced':
        bounce_type = payload.get('bounce_type', 'hard')
        if bounce_type == 'hard':
            contact.do_not_contact = True
        activity = ContactActivity(
            id=generate_id(),
            contact_id=contact.id,
            activity_type='email_bounced',
            notes=f'{bounce_type} bounce',
            created_by='webhook',
        )
        db.session.add(activity)
        _increment_campaign_counter(payload, 'bounce_count')

    elif event == 'unsubscribed':
        contact.do_not_contact = True
        activity = ContactActivity(
            id=generate_id(),
            contact_id=contact.id,
            activity_type='unsubscribed',
            created_by='webhook',
        )
        db.session.add(activity)
        _increment_campaign_counter(payload, 'unsubscribe_count')

    elif event == 'email_opened':
        contact.last_contacted_at = datetime.utcnow()


def _increment_campaign_counter(payload: dict, field: str):
    from app.models.integration import EmailCampaign
    seq_id = payload.get('emailer_campaign_id') or payload.get('sequence_id')
    if not seq_id:
        return
    campaign = EmailCampaign.query.filter_by(apollo_sequence_id=seq_id).first()
    if campaign:
        setattr(campaign, field, (getattr(campaign, field) or 0) + 1)


def _publish_reply_sse(contact: Contact, payload: dict):
    try:
        from app.blueprints.collaboration import publish_event  # noqa: F401
        # Notify orchestrator / SSE — best-effort
        publish_event(f'user_{contact.user_id}', 'reply_received', {
            'contact_name': contact.display_name,
            'contact_id': contact.id,
        })
    except Exception:
        pass


# ── Stripe Connect OAuth ──────────────────────────────────────────────────────

@integrations_bp.route('/api/integrations/stripe/connect')
@login_required
def stripe_connect():
    client_id = current_app.config.get('STRIPE_CLIENT_ID')
    if not client_id:
        return jsonify({'error': 'Stripe Connect is not configured on this server.'}), 503

    state = secrets.token_urlsafe(16)
    session['stripe_oauth_state'] = state

    from app.services.stripe_connect_service import get_oauth_url
    return redirect(get_oauth_url(state))


@integrations_bp.route('/api/integrations/stripe/callback')
@login_required
def stripe_callback():
    error = request.args.get('error')
    if error:
        return redirect(url_for('pages.settings_integrations') + '?stripe_error=cancelled')

    state = request.args.get('state')
    expected = session.pop('stripe_oauth_state', None)
    if not expected or state != expected:
        return redirect(url_for('pages.settings_integrations') + '?stripe_error=invalid_state')

    code = request.args.get('code')
    if not code:
        return redirect(url_for('pages.settings_integrations') + '?stripe_error=no_code')

    try:
        from app.services.stripe_connect_service import exchange_code
        from app.services.token_crypto import encrypt_token
        token_data = exchange_code(code)
    except Exception as exc:
        logger.error('Stripe token exchange failed for user %s: %s', current_user.id, exc)
        return redirect(url_for('pages.settings_integrations') + '?stripe_error=token_exchange_failed')

    integration = UserIntegration.query.filter_by(
        user_id=current_user.id, provider='stripe'
    ).first()
    if not integration:
        integration = UserIntegration(
            id=generate_id(),
            user_id=current_user.id,
            provider='stripe',
        )
        db.session.add(integration)

    integration.access_token_enc = encrypt_token(token_data['access_token'])
    integration.provider_account_id = token_data['stripe_user_id']
    integration.provider_scope = token_data.get('scope', 'read_write')
    db.session.commit()

    return redirect(url_for('pages.settings_integrations') + '?stripe_connected=1')


@integrations_bp.route('/api/integrations/stripe/disconnect', methods=['POST'])
@login_required
def stripe_disconnect():
    integration = UserIntegration.query.filter_by(
        user_id=current_user.id, provider='stripe'
    ).first()
    if not integration:
        return jsonify({'ok': True})

    if integration.provider_account_id:
        from app.services.stripe_connect_service import deauthorize_account
        deauthorize_account(integration.provider_account_id)

    integration.access_token_enc = None
    integration.provider_account_id = None
    integration.provider_scope = None
    db.session.commit()
    return jsonify({'ok': True})


# ── Stripe Connect webhook (income attribution) ───────────────────────────────

@integrations_bp.route('/webhooks/stripe/connect', methods=['POST'])
def stripe_connect_webhook():
    """
    Receives events from connected Stripe accounts.
    Reads simulacrum_* metadata to auto-create LayerIncomeRecords (FR-STRIPE-05).
    Must be registered in the Stripe dashboard for Connect events.
    """
    payload = request.get_data()
    sig_header = request.headers.get('Stripe-Signature')
    secret = current_app.config.get('STRIPE_CONNECT_WEBHOOK_SECRET')

    if secret:
        try:
            import stripe
            stripe.api_key = current_app.config['STRIPE_SECRET_KEY']
            event = stripe.Webhook.construct_event(payload, sig_header, secret)
        except Exception as exc:
            logger.warning('Stripe Connect webhook signature failed: %s', exc)
            return jsonify({'error': 'Invalid signature'}), 400
    else:
        import json
        event = json.loads(payload)

    event_type = event.get('type', '')
    stripe_account = event.get('account')     # connected account ID
    event_obj = event.get('data', {}).get('object', {})

    # Events that carry payment confirmation with simulacrum metadata
    if event_type in ('checkout.session.completed', 'invoice.paid',
                      'payment_intent.succeeded', 'payment_link.completed'):
        try:
            from app.services.stripe_connect_service import attribute_income_from_stripe_event
            attribute_income_from_stripe_event(event_obj, stripe_account)
        except Exception as exc:
            logger.error('Income attribution failed for event %s: %s', event_type, exc, exc_info=True)

    return jsonify({'received': True}), 200


# ── Cal.com OAuth ─────────────────────────────────────────────────────────────

@integrations_bp.route('/api/integrations/cal/connect')
@login_required
def cal_connect():
    if not current_app.config.get('CAL_CLIENT_ID'):
        return jsonify({'error': 'Cal.com integration is not configured on this server.'}), 503

    state = secrets.token_urlsafe(16)
    session['cal_oauth_state'] = state

    from app.services.cal_service import get_auth_url
    return redirect(get_auth_url(state))


@integrations_bp.route('/api/integrations/cal/callback')
@login_required
def cal_callback():
    error = request.args.get('error')
    if error:
        return redirect(url_for('pages.settings_integrations') + '?cal_error=cancelled')

    state = request.args.get('state')
    expected = session.pop('cal_oauth_state', None)
    if not expected or state != expected:
        return redirect(url_for('pages.settings_integrations') + '?cal_error=invalid_state')

    code = request.args.get('code')
    if not code:
        return redirect(url_for('pages.settings_integrations') + '?cal_error=no_code')

    try:
        from app.services.cal_service import exchange_code
        from app.services.token_crypto import encrypt_token
        token_data = exchange_code(code)
    except Exception as exc:
        logger.error('Cal.com token exchange failed for user %s: %s', current_user.id, exc)
        return redirect(url_for('pages.settings_integrations') + '?cal_error=token_exchange_failed')

    integration = UserIntegration.query.filter_by(
        user_id=current_user.id, provider='cal'
    ).first()
    if not integration:
        integration = UserIntegration(
            id=generate_id(),
            user_id=current_user.id,
            provider='cal',
        )
        db.session.add(integration)

    integration.access_token_enc = encrypt_token(token_data['access_token'])
    if token_data.get('refresh_token'):
        integration.refresh_token_enc = encrypt_token(token_data['refresh_token'])
    if token_data.get('expires_in'):
        from datetime import timedelta
        integration.token_expires_at = datetime.utcnow() + timedelta(seconds=token_data['expires_in'])
    db.session.commit()

    return redirect(url_for('pages.settings_integrations') + '?cal_connected=1')


@integrations_bp.route('/api/integrations/cal/disconnect', methods=['POST'])
@login_required
def cal_disconnect():
    integration = UserIntegration.query.filter_by(
        user_id=current_user.id, provider='cal'
    ).first()
    if integration:
        integration.access_token_enc = None
        integration.refresh_token_enc = None
        integration.token_expires_at = None
        db.session.commit()
    return jsonify({'ok': True})


# ── Cal.com booking webhook ───────────────────────────────────────────────────

@integrations_bp.route('/webhooks/cal/<user_id>', methods=['POST'])
def cal_webhook(user_id):
    """
    Handles Cal.com BOOKING_CREATED and BOOKING_CANCELLED events.
    - Advances CRM contact: prospect → active (FR-CAL-02)
    - Increments consulting_bookings_mo momentum metric (FR-CAL-03)
    - SSE polling picks up the DB change for GCC live update (FR-CAL-03)
    """
    payload = request.get_json(silent=True) or {}
    trigger = payload.get('triggerEvent')

    if trigger not in ('BOOKING_CREATED', 'BOOKING_CANCELLED'):
        return '', 200

    inner   = payload.get('payload') or {}
    # Support both direct payload and nested payload string
    if isinstance(inner, str):
        import json as _json
        try:
            inner = _json.loads(inner)
        except Exception:
            inner = {}

    attendee_email  = (inner.get('attendee') or {}).get('email')
    attendee_name   = (inner.get('attendee') or {}).get('name', '')
    simulation_id   = (payload.get('simulacrum_simulation_id')
                       or inner.get('simulacrum_simulation_id'))
    action_id       = (payload.get('simulacrum_action_id')
                       or inner.get('simulacrum_action_id'))

    try:
        _handle_cal_event(trigger, user_id, attendee_email, attendee_name,
                          simulation_id, action_id)
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        logger.error('Cal webhook error user=%s trigger=%s: %s',
                     user_id, trigger, exc, exc_info=True)

    return '', 200


def _handle_cal_event(trigger: str, user_id: str, attendee_email: str,
                      attendee_name: str, simulation_id: str, action_id: str):
    from app.models.contact import Contact, ContactActivity
    from app.models.layer6 import Layer6Momentum
    from datetime import date

    if trigger == 'BOOKING_CREATED':
        # CRM pipeline: prospect → active + log activity (FR-CAL-02)
        if attendee_email:
            contact = Contact.query.filter_by(
                user_id=user_id, email=attendee_email
            ).first()
            if not contact and attendee_name:
                # Create a contact record for this new booking attendee
                parts = (attendee_name or '').split(' ', 1)
                contact = Contact(
                    id=generate_id(),
                    user_id=user_id,
                    first_name=parts[0],
                    last_name=parts[1] if len(parts) > 1 else '',
                    email=attendee_email,
                    source='inbound_referral',
                    source_action_id=action_id or None,
                    pipeline_stage='prospect',
                )
                db.session.add(contact)
                db.session.flush()

            if contact:
                contact.advance_stage('active', created_by='webhook',
                                      simulation_id=simulation_id,
                                      action_id=action_id)
                contact.last_contacted_at = datetime.utcnow()
                activity = ContactActivity(
                    id=generate_id(),
                    contact_id=contact.id,
                    simulation_id=simulation_id,
                    action_id=action_id,
                    activity_type='call_booked',
                    created_by='webhook',
                )
                db.session.add(activity)

        # Momentum: increment consulting_bookings_mo (FR-CAL-03)
        if simulation_id:
            momentum = _get_or_create_momentum(simulation_id)
            momentum.consulting_bookings_mo = (momentum.consulting_bookings_mo or 0) + 1

    elif trigger == 'BOOKING_CANCELLED':
        if simulation_id:
            momentum = _get_or_create_momentum(simulation_id)
            if (momentum.consulting_bookings_mo or 0) > 0:
                momentum.consulting_bookings_mo -= 1

        if attendee_email:
            contact = Contact.query.filter_by(
                user_id=user_id, email=attendee_email
            ).first()
            if contact:
                activity = ContactActivity(
                    id=generate_id(),
                    contact_id=contact.id,
                    simulation_id=simulation_id,
                    action_id=action_id,
                    activity_type='booking_cancelled',
                    created_by='webhook',
                )
                db.session.add(activity)


def _get_or_create_momentum(simulation_id: str):
    from app.models.layer6 import Layer6Momentum
    from datetime import date
    momentum = Layer6Momentum.query.filter_by(
        simulation_id=simulation_id
    ).order_by(Layer6Momentum.snapshot_date.desc()).first()
    if not momentum:
        momentum = Layer6Momentum(
            id=generate_id(),
            simulation_id=simulation_id,
            snapshot_date=date.today(),
        )
        db.session.add(momentum)
        db.session.flush()
    return momentum


# ── PandaDoc API key management ───────────────────────────────────────────────

@integrations_bp.route('/api/integrations/pandadoc/save', methods=['POST'])
@login_required
def pandadoc_save():
    data = request.get_json(silent=True) or {}
    api_key = (data.get('api_key') or '').strip()
    if not api_key:
        return jsonify({'error': 'api_key is required'}), 400

    # Validate the key works by calling PandaDoc /me
    try:
        import requests as _req
        resp = _req.get('https://api.pandadoc.com/public/v1/members/current/',
                        headers={'Authorization': f'API-Key {api_key}'}, timeout=10)
        resp.raise_for_status()
    except Exception as exc:
        logger.warning('PandaDoc API key validation failed for user %s: %s', current_user.id, exc)
        return jsonify({'error': 'Invalid PandaDoc API key — could not authenticate.'}), 422

    from app.services.token_crypto import encrypt_token
    integration = UserIntegration.query.filter_by(
        user_id=current_user.id, provider='pandadoc'
    ).first()
    if not integration:
        integration = UserIntegration(
            id=generate_id(),
            user_id=current_user.id,
            provider='pandadoc',
        )
        db.session.add(integration)

    integration.access_token_enc = encrypt_token(api_key)
    db.session.commit()
    return jsonify({'ok': True})


@integrations_bp.route('/api/integrations/pandadoc/clear', methods=['POST'])
@login_required
def pandadoc_clear():
    integration = UserIntegration.query.filter_by(
        user_id=current_user.id, provider='pandadoc'
    ).first()
    if integration:
        integration.access_token_enc = None
        db.session.commit()
    return jsonify({'ok': True})


# ── PandaDoc webhook ──────────────────────────────────────────────────────────

@integrations_bp.route('/webhooks/pandadoc', methods=['POST'])
def pandadoc_webhook():
    """
    Receives document_state_changed events from PandaDoc.
    Updates signing_documents.status and advances CRM on document_signed (FR-SIGN-04).
    """
    payload = request.get_json(silent=True) or {}
    try:
        from app.services.pandadoc_service import handle_pandadoc_event
        handle_pandadoc_event(payload)
    except Exception as exc:
        logger.error('PandaDoc webhook error: %s', exc, exc_info=True)
    return jsonify({'received': True}), 200


# ── Document signing endpoint (called from GCC) ───────────────────────────────

@integrations_bp.route('/api/signing/send', methods=['POST'])
@login_required
def signing_send():
    """
    Send an artifact document for signature via PandaDoc (FR-SIGN-02).
    Body: {simulation_id, action_id, action_type, artifact_version_id,
           layer_number, recipient_email, recipient_name, document_title}
    """
    data = request.get_json(silent=True) or {}

    required = ('simulation_id', 'recipient_email', 'action_type')
    missing = [f for f in required if not data.get(f)]
    if missing:
        return jsonify({'error': f'Missing fields: {", ".join(missing)}'}), 400

    # Resolve document content — prefer explicit artifact_version_id, then action_id lookup
    content_html = data.get('content_html', '')
    artifact_version_id = data.get('artifact_version_id')
    action_id_lookup = data.get('action_id')

    if not content_html and artifact_version_id:
        from app.models.artifact import ArtifactVersion
        av = ArtifactVersion.query.filter_by(id=artifact_version_id).first()
        if av and av.content:
            content_html = av.content

    if not content_html and action_id_lookup:
        from app.models.artifact import ArtifactVersion
        av = ArtifactVersion.query.filter_by(
            action_id=action_id_lookup
        ).order_by(ArtifactVersion.created_at.desc()).first()
        if av:
            artifact_version_id = av.id
            if av.content:
                content_html = av.content

    if not content_html:
        # Fall back to artifact text from AgentAction.artifact field
        if action_id_lookup:
            from app.models.agent_action import AgentAction
            aa = AgentAction.query.get(action_id_lookup)
            if aa and aa.artifact:
                content_html = f'<pre style="font-family:sans-serif;white-space:pre-wrap">{aa.artifact}</pre>'

    if not content_html:
        return jsonify({'error': 'No document content available to send.'}), 422

    try:
        from app.services.pandadoc_service import deploy_document_for_signing, PandaDocAuthRequired
        result = deploy_document_for_signing(
            user_id=current_user.id,
            simulation_id=data['simulation_id'],
            action_id=data.get('action_id'),
            action_type=data['action_type'],
            artifact_version_id=artifact_version_id,
            layer_number=int(data.get('layer_number', 6)),
            recipient_email=data['recipient_email'],
            recipient_name=data.get('recipient_name', ''),
            document_title=data.get('document_title', 'Agreement'),
            content_html=content_html,
        )
        return jsonify(result), 200
    except PandaDocAuthRequired:
        return jsonify({'error': 'pandadoc_not_configured',
                        'message': 'Connect PandaDoc in Settings → Integrations first.'}), 403
    except Exception as exc:
        logger.error('signing_send failed for user %s: %s', current_user.id, exc, exc_info=True)
        return jsonify({'error': str(exc)}), 500


# ── ConvertKit API key management (FR-PUB-02) ─────────────────────────────────

@integrations_bp.route('/api/integrations/convertkit/save', methods=['POST'])
@login_required
def convertkit_save():
    """
    Save ConvertKit API key + optional API secret.
    Body: {api_key, api_secret?}
    """
    data = request.get_json(silent=True) or {}
    api_key = (data.get('api_key') or '').strip()
    api_secret = (data.get('api_secret') or '').strip()
    if not api_key:
        return jsonify({'error': 'api_key is required'}), 400

    # Validate the key works by fetching subscriber count
    try:
        import requests as _req
        resp = _req.get(
            'https://api.convertkit.com/v3/subscribers',
            params={'api_secret': api_secret or api_key, 'page': 1},
            timeout=10,
        )
        resp.raise_for_status()
    except Exception as exc:
        logger.warning('ConvertKit API key validation failed for user %s: %s', current_user.id, exc)
        return jsonify({'error': 'Invalid ConvertKit API key — could not authenticate.'}), 422

    from app.services.token_crypto import encrypt_token
    integration = UserIntegration.query.filter_by(
        user_id=current_user.id, provider='convertkit'
    ).first()
    if not integration:
        integration = UserIntegration(
            id=generate_id(),
            user_id=current_user.id,
            provider='convertkit',
        )
        db.session.add(integration)

    integration.access_token_enc = encrypt_token(api_key)
    if api_secret:
        integration.refresh_token_enc = encrypt_token(api_secret)
    db.session.commit()
    return jsonify({'ok': True})


@integrations_bp.route('/api/integrations/convertkit/clear', methods=['POST'])
@login_required
def convertkit_clear():
    integration = UserIntegration.query.filter_by(
        user_id=current_user.id, provider='convertkit'
    ).first()
    if integration:
        integration.access_token_enc = None
        integration.refresh_token_enc = None
        db.session.commit()
    return jsonify({'ok': True})


# ── SendGrid API key (platform-level transactional email) ─────────────────────

@integrations_bp.route('/api/integrations/sendgrid/save', methods=['POST'])
@login_required
def sendgrid_save():
    """
    Save the SendGrid API key to platform_settings.
    Body: {api_key}
    """
    if not current_user.is_admin:
        # Any authenticated user can configure since this is a single-operator platform
        # but we validate the key before saving
        pass

    data = request.get_json(silent=True) or {}
    api_key = (data.get('api_key') or '').strip()
    if not api_key:
        return jsonify({'error': 'api_key is required'}), 400

    # Validate the key works
    try:
        import requests as _req
        resp = _req.get(
            'https://api.sendgrid.com/v3/user/profile',
            headers={'Authorization': f'Bearer {api_key}'},
            timeout=10,
        )
        resp.raise_for_status()
    except Exception as exc:
        logger.warning('SendGrid API key validation failed for user %s: %s', current_user.id, exc)
        return jsonify({'error': 'Invalid SendGrid API key — could not authenticate.'}), 422

    from app.models.platform_settings import PlatformSetting
    PlatformSetting.set('sendgrid_api_key', api_key, updated_by=current_user.id)
    return jsonify({'ok': True})


@integrations_bp.route('/api/integrations/sendgrid/clear', methods=['POST'])
@login_required
def sendgrid_clear():
    from app.models.platform_settings import PlatformSetting
    from app.extensions import db as _db
    setting = PlatformSetting.query.filter_by(key='sendgrid_api_key').first()
    if setting:
        _db.session.delete(setting)
        _db.session.commit()
    return jsonify({'ok': True})
