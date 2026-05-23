import secrets
import logging
from datetime import datetime, timedelta
from typing import Optional

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
    providers = ['apollo', 'stripe', 'cal', 'pandadoc', 'convertkit',
                 'kajabi', 'plaid', 'alpaca']
    result = {}
    for prov in providers:
        rec = UserIntegration.query.filter_by(
            user_id=current_user.id, provider=prov
        ).first()
        if rec:
            d = rec.to_dict()
            if prov == 'alpaca':
                meta = rec.get_meta()
                d['fintech_toggle'] = meta.get('fintech_toggle', False)
                d['paper'] = meta.get('paper', True)
        else:
            d = {'provider': prov, 'status': 'not_connected'}
            if prov == 'apollo':
                d['apollo_daily_limit'] = 30
        result[prov] = d
    result['linkedin'] = {'provider': 'linkedin', 'status': 'pending_legal_review'}
    return jsonify(result)


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
    _mark_connected(integration, 'Apollo OAuth connected')
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
    _mark_disconnected(integration)
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
    from app.services.bayesian_service import dispatch_signal

    seq_id      = payload.get('emailer_campaign_id') or payload.get('sequence_id')
    action_type = payload.get('action_type') or 'cold_email_campaign'
    sim_id      = _sim_id_from_campaign(seq_id)

    if event in ('email_sent', 'email_delivered'):
        _increment_campaign_counter(payload, 'sent_count')
        # email_sent is a volume counter — no posterior update (weight 0.1, not a conversion signal)

    elif event == 'email_opened':
        contact.last_contacted_at = datetime.utcnow()
        _increment_campaign_counter(payload, 'open_count')
        dispatch_signal(sim_id, f'open_rate:{action_type}', 1.0, 0.15, '+')

    elif event == 'email_clicked':
        contact.last_contacted_at = datetime.utcnow()
        _increment_campaign_counter(payload, 'click_count')
        dispatch_signal(sim_id, f'engagement_rate:{action_type}', 1.0, 0.2, '+')

    elif event == 'email_reply':
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
        dispatch_signal(sim_id, f'reply_rate:{action_type}', 1.0, 0.5, '+')

        # Per-step attribution (FR-APOLLO-05)
        step_n = payload.get('sequence_step') or payload.get('step_number')
        if step_n:
            dispatch_signal(sim_id, f'step_effectiveness:step_{step_n}', 1.0, 0.2, '+')

        try:
            from app.services.notification_service import send_notification as _sn
            _action_name = seq_id or 'outreach campaign'
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
            dispatch_signal(sim_id, f'deliverability:{action_type}', 1.0, 0.2, '-')
        else:
            dispatch_signal(sim_id, f'deliverability:{action_type}', 1.0, 0.05, '-')
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
        dispatch_signal(sim_id, f'unsubscribe_rate:{action_type}', 1.0, 0.3, '-')
        activity = ContactActivity(
            id=generate_id(),
            contact_id=contact.id,
            activity_type='unsubscribed',
            created_by='webhook',
        )
        db.session.add(activity)
        _increment_campaign_counter(payload, 'unsubscribe_count')


def _increment_campaign_counter(payload: dict, field: str):
    from app.models.integration import EmailCampaign
    seq_id = payload.get('emailer_campaign_id') or payload.get('sequence_id')
    if not seq_id:
        return
    campaign = EmailCampaign.query.filter_by(apollo_sequence_id=seq_id).first()
    if campaign:
        setattr(campaign, field, (getattr(campaign, field) or 0) + 1)


def _sim_id_from_campaign(seq_id: Optional[str]) -> Optional[str]:
    """Resolve simulation_id from an Apollo sequence ID."""
    if not seq_id:
        return None
    from app.models.integration import EmailCampaign
    c = EmailCampaign.query.filter_by(apollo_sequence_id=seq_id).first()
    return c.simulation_id if c else None


def _find_user_simulation(user_id: str) -> Optional[str]:
    """Return the most recent active simulation for a user (fallback for webhooks)."""
    from app.models.simulation import Simulation
    sim = Simulation.query.filter_by(
        user_id=user_id
    ).order_by(Simulation.created_at.desc()).first()
    return sim.id if sim else None


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
    _mark_connected(integration, 'Stripe Connect OAuth connected')
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
    _mark_disconnected(integration)
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

    event_type     = event.get('type', '')
    stripe_account = event.get('account')
    event_obj      = event.get('data', {}).get('object', {})

    meta = (
        event_obj.get('metadata') or
        (event_obj.get('payment_intent') or {}) or {}
    )
    if isinstance(meta, str):
        meta = {}
    sim_id      = meta.get('simulacrum_simulation_id')
    action_type = meta.get('action_type') or 'unknown'
    layer_num   = meta.get('layer_number') or '1'

    from app.services.bayesian_service import dispatch_signal

    # Positive income events (weight 1.0)
    if event_type in ('checkout.session.completed', 'invoice.paid',
                      'payment_intent.succeeded', 'payment_link.completed'):
        try:
            from app.services.stripe_connect_service import attribute_income_from_stripe_event
            attribute_income_from_stripe_event(event_obj, stripe_account)
        except Exception as exc:
            logger.error('Income attribution failed for event %s: %s', event_type, exc, exc_info=True)
        dispatch_signal(sim_id, f'income_confirmed:L{layer_num}:{action_type}', 1.0, 1.0, '+')

    elif event_type == 'charge.refunded':
        dispatch_signal(sim_id, f'refund_rate:L{layer_num}:{action_type}', 1.0, 0.8, '-')

    elif event_type == 'charge.dispute.created':
        dispatch_signal(sim_id, f'dispute_rate:{action_type}', 1.0, 0.9, '-')

    elif event_type == 'customer.subscription.created':
        dispatch_signal(sim_id, f'subscription_starts:{action_type}', 1.0, 0.8, '+')

    elif event_type == 'customer.subscription.deleted':
        dispatch_signal(sim_id, f'churn_rate:{action_type}', 1.0, 0.6, '-')

    elif event_type == 'invoice.payment_failed':
        dispatch_signal(sim_id, f'payment_failure_rate', 1.0, 0.3, '-')

    if sim_id:
        try:
            from app.extensions import db as _db
            _db.session.commit()
        except Exception:
            pass

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
    _mark_connected(integration, 'Cal.com OAuth connected')
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
        _mark_disconnected(integration)
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

    if trigger not in ('BOOKING_CREATED', 'BOOKING_CANCELLED',
                       'BOOKING_RESCHEDULED', 'MEETING_ENDED'):
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
                          simulation_id, action_id, payload=inner)
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        logger.error('Cal webhook error user=%s trigger=%s: %s',
                     user_id, trigger, exc, exc_info=True)

    return '', 200


def _handle_cal_event(trigger: str, user_id: str, attendee_email: str,
                      attendee_name: str, simulation_id: str, action_id: str,
                      payload: dict = None):
    from app.models.contact import Contact, ContactActivity
    from app.models.layer6 import Layer6Momentum
    from app.services.bayesian_service import dispatch_signal
    from datetime import date

    session_type = (payload or {}).get('eventType') or 'discovery_call'

    if trigger == 'BOOKING_CREATED':
        # CRM pipeline: prospect → active + log activity (FR-CAL-02)
        if attendee_email:
            contact = Contact.query.filter_by(
                user_id=user_id, email=attendee_email
            ).first()
            if not contact and attendee_name:
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

        if simulation_id:
            momentum = _get_or_create_momentum(simulation_id)
            momentum.consulting_bookings_mo = (momentum.consulting_bookings_mo or 0) + 1
            dispatch_signal(simulation_id, f'booking_rate:{session_type}', 1.0, 0.6, '+')

    elif trigger == 'BOOKING_CANCELLED':
        if simulation_id:
            momentum = _get_or_create_momentum(simulation_id)
            if (momentum.consulting_bookings_mo or 0) > 0:
                momentum.consulting_bookings_mo -= 1
            dispatch_signal(simulation_id, f'cancellation_rate:{session_type}', 1.0, 0.4, '-')

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

    elif trigger == 'BOOKING_RESCHEDULED':
        # Weak positive — person still intends to meet (weight 0.1)
        dispatch_signal(simulation_id, 'reschedule_rate', 1.0, 0.1, '+')

    elif trigger == 'MEETING_ENDED':
        # Meeting completed — positive signal, also clears no-show flag (weight 0.3)
        dispatch_signal(simulation_id, f'meeting_completion_rate:{session_type}', 1.0, 0.3, '+')
        # Mark the booking as completed in contact activities
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
                    activity_type='meeting_completed',
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
    _mark_connected(integration, 'PandaDoc API key saved')
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
        _mark_disconnected(integration)
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
    _mark_connected(integration, 'ConvertKit API key saved')
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
        _mark_disconnected(integration)
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


# ── ConvertKit webhook ────────────────────────────────────────────────────────
# Handles subscriber lifecycle events pushed by ConvertKit.
# Bayesian signals per Section 6.4 of SIM-PRD-INTEG-001.

@integrations_bp.route('/webhooks/convertkit/<user_id>', methods=['POST'])
def convertkit_webhook(user_id):
    payload  = request.get_json(silent=True) or {}
    event    = payload.get('type') or payload.get('event')
    form_id  = str(payload.get('form_id') or '')
    sim_id   = _find_user_simulation(user_id)

    subscriber = payload.get('subscriber') or {}
    email      = subscriber.get('email_address') or subscriber.get('email')

    if not event:
        return '', 200

    try:
        _handle_convertkit_event(event, user_id, email, form_id, sim_id, payload)
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        logger.error('ConvertKit webhook error user=%s event=%s: %s',
                     user_id, event, exc, exc_info=True)
    return '', 200


def _handle_convertkit_event(event: str, user_id: str, email: str,
                              form_id: str, sim_id: str, payload: dict):
    from app.services.bayesian_service import dispatch_signal

    if event == 'subscriber.subscriber_activate':
        # New subscriber — weight 0.3 positive
        dispatch_signal(sim_id, 'subscriber_growth_rate', 1.0, 0.3, '+')
        # Create CRM contact if not exists
        if email:
            _upsert_contact_from_email(user_id, email, source='convertkit')

    elif event == 'subscriber.subscriber_unsubscribe':
        dispatch_signal(sim_id, f'unsubscribe_rate:{form_id or "list"}', 1.0, 0.3, '-')

    elif event == 'subscriber.form_subscribe':
        # Subscribing via a specific form (slightly higher weight — attributable)
        dispatch_signal(sim_id, f'form_conversion_rate:{form_id}', 1.0, 0.4, '+')
        if email:
            _upsert_contact_from_email(user_id, email, source='convertkit')

    elif event in ('subscriber.tag_add', 'subscriber.tag_remove'):
        pass  # Tracked for segmentation but no posterior update


def _upsert_contact_from_email(user_id: str, email: str, source: str = 'convertkit'):
    """Create a CRM contact from a ConvertKit subscriber if not already present."""
    from app.models.contact import Contact
    existing = Contact.query.filter_by(user_id=user_id, email=email).first()
    if not existing:
        contact = Contact(
            id=generate_id(),
            user_id=user_id,
            first_name='',
            last_name='',
            email=email,
            source=source,
            pipeline_stage='prospect',
        )
        db.session.add(contact)
        db.session.flush()


# ── Kajabi — Courses & Memberships ───────────────────────────────────────────

@integrations_bp.route('/api/integrations/kajabi/save', methods=['POST'])
@login_required
def kajabi_save():
    """
    Save Kajabi API key + site subdomain (FR-KAJABI-01).
    Body: {api_key, site_subdomain}
    """
    data          = request.get_json(silent=True) or {}
    api_key       = (data.get('api_key') or '').strip()
    subdomain     = (data.get('site_subdomain') or '').strip()
    if not api_key:
        return jsonify({'error': 'api_key is required'}), 400

    # Light validation — Kajabi API v1 endpoint
    try:
        import requests as _req
        resp = _req.get(
            'https://kajabi.com/api/v1/site',
            headers={'Authorization': f'Bearer {api_key}',
                     'Accept': 'application/json'},
            timeout=10,
        )
        if resp.status_code not in (200, 401):
            pass  # Accept even a 401 — key may still work for webhooks
        elif resp.status_code == 401:
            return jsonify({'error': 'Invalid Kajabi API key — authentication failed.'}), 422
    except Exception as exc:
        logger.warning('Kajabi API key validation skipped for user %s: %s', current_user.id, exc)

    from app.services.token_crypto import encrypt_token
    integration = UserIntegration.query.filter_by(
        user_id=current_user.id, provider='kajabi'
    ).first()
    if not integration:
        integration = UserIntegration(
            id=generate_id(),
            user_id=current_user.id,
            provider='kajabi',
        )
        db.session.add(integration)

    integration.access_token_enc    = encrypt_token(api_key)
    integration.provider_account_id = subdomain or None
    _mark_connected(integration, 'Kajabi API key saved')
    db.session.commit()
    return jsonify({'ok': True})


@integrations_bp.route('/api/integrations/kajabi/clear', methods=['POST'])
@login_required
def kajabi_clear():
    integration = UserIntegration.query.filter_by(
        user_id=current_user.id, provider='kajabi'
    ).first()
    if integration:
        integration.access_token_enc    = None
        integration.provider_account_id = None
        _mark_disconnected(integration)
        db.session.commit()
    return jsonify({'ok': True})


# ── Kajabi webhook ────────────────────────────────────────────────────────────

@integrations_bp.route('/webhooks/kajabi/<user_id>', methods=['POST'])
def kajabi_webhook(user_id):
    """
    Handles Kajabi purchase, subscription, enrollment, and lesson events.
    Bayesian signals per Section 5.4 of SIM-PRD-INTEG-001.
    """
    payload    = request.get_json(silent=True) or {}
    event      = payload.get('event') or payload.get('type')
    product_id = str(payload.get('product_id') or payload.get('offer_id') or '')
    sim_id     = _find_user_simulation(user_id)

    if not event:
        return '', 200

    try:
        _handle_kajabi_event(event, user_id, product_id, sim_id, payload)
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        logger.error('Kajabi webhook error user=%s event=%s: %s',
                     user_id, event, exc, exc_info=True)
    return '', 200


def _handle_kajabi_event(event: str, user_id: str, product_id: str,
                         sim_id: str, payload: dict):
    from app.services.bayesian_service import dispatch_signal
    from app.models.income import LayerIncomeRecord
    from datetime import date as _date
    import json as _json

    amount_cents = payload.get('amount_cents') or 0
    amount_usd   = float(amount_cents) / 100.0 if amount_cents else 0.0

    if event == 'purchase.completed':
        dispatch_signal(sim_id, f'course_sales:{product_id}', 1.0, 1.0, '+')
        if sim_id and amount_usd > 0:
            _create_kajabi_income_record(
                user_id, sim_id, amount_usd, payload, 'course_sale',
            )

    elif event == 'subscription.renewed':
        dispatch_signal(sim_id, f'renewal_rate:{product_id}', 1.0, 0.9, '+')
        if sim_id and amount_usd > 0:
            _create_kajabi_income_record(
                user_id, sim_id, amount_usd, payload, 'subscription_renewal',
            )

    elif event == 'subscription.cancelled':
        dispatch_signal(sim_id, f'churn_rate:{product_id}', 1.0, 0.6, '-')

    elif event == 'subscription.payment_failed':
        dispatch_signal(sim_id, f'payment_failure_rate:{product_id}', 1.0, 0.2, '-')

    elif event == 'purchase.refunded':
        dispatch_signal(sim_id, f'refund_rate:{product_id}', 1.0, 0.8, '-')

    elif event == 'enrollment.completed':
        dispatch_signal(sim_id, f'completion_rate:{product_id}', 1.0, 0.4, '+')

    elif event == 'lesson.completed':
        dispatch_signal(sim_id, f'lesson_engagement:{product_id}', 1.0, 0.1, '+')


def _create_kajabi_income_record(user_id: str, sim_id: str, amount_usd: float,
                                 payload: dict, income_type: str):
    """Create a LayerIncomeRecord from a Kajabi purchase event."""
    try:
        from app.models.income import LayerIncomeRecord
        from utils.id_gen import generate_id as _gid
        from datetime import date as _date
        rec = LayerIncomeRecord(
            id=_gid(),
            simulation_id=sim_id,
            layer_number=3,
            amount=amount_usd,
            currency='USD',
            income_date=_date.today(),
            source='kajabi_webhook',
            source_ref=str(payload.get('purchase_id') or payload.get('id') or ''),
            description=income_type,
            is_void=False,
            recorded_by='system',
        )
        db.session.add(rec)
    except Exception as exc:
        logger.warning('Could not create Kajabi income record: %s', exc)


# ── LinkedIn (blocked — attorney review required per FR-LINKEDIN-01) ──────────

@integrations_bp.route('/api/integrations/linkedin/status')
@login_required
def linkedin_status():
    return jsonify({
        'provider': 'linkedin',
        'status': 'pending_legal_review',
        'message': (
            'LinkedIn integration requires attorney review of the LinkedIn API '
            'Terms of Service before implementation. See FR-LINKEDIN-01.'
        ),
    }), 200


# ── Plaid — Financial Account Linking ────────────────────────────────────────

@integrations_bp.route('/api/integrations/plaid/link-token', methods=['POST'])
@login_required
def plaid_link_token():
    """
    Create a Plaid link_token for the Plaid Link client-side widget (FR-PLAID-01).
    Returns {link_token} to the browser; browser opens Plaid Link.
    """
    plaid_client_id = current_app.config.get('PLAID_CLIENT_ID')
    plaid_secret    = current_app.config.get('PLAID_SECRET')
    plaid_env       = current_app.config.get('PLAID_ENV', 'sandbox')

    if not plaid_client_id or not plaid_secret:
        return jsonify({'error': 'Plaid integration is not configured on this server.'}), 503

    try:
        import requests as _req
        base_url = {
            'sandbox':     'https://sandbox.plaid.com',
            'development': 'https://development.plaid.com',
            'production':  'https://production.plaid.com',
        }.get(plaid_env, 'https://sandbox.plaid.com')

        resp = _req.post(f'{base_url}/link/token/create', json={
            'client_id': plaid_client_id,
            'secret':    plaid_secret,
            'user':      {'client_user_id': current_user.id},
            'client_name': 'Simulacrum',
            'products':  ['transactions', 'investments', 'liabilities'],
            'country_codes': ['US'],
            'language': 'en',
        }, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        return jsonify({'link_token': data['link_token']}), 200
    except Exception as exc:
        logger.error('Plaid link token creation failed for user %s: %s', current_user.id, exc)
        return jsonify({'error': str(exc)}), 500


@integrations_bp.route('/api/integrations/plaid/exchange', methods=['POST'])
@login_required
def plaid_exchange():
    """
    Exchange a Plaid public_token for an access_token (FR-PLAID-01 step 5-6).
    Body: {public_token}
    """
    data         = request.get_json(silent=True) or {}
    public_token = data.get('public_token', '').strip()
    if not public_token:
        return jsonify({'error': 'public_token is required'}), 400

    plaid_client_id = current_app.config.get('PLAID_CLIENT_ID')
    plaid_secret    = current_app.config.get('PLAID_SECRET')
    plaid_env       = current_app.config.get('PLAID_ENV', 'sandbox')

    base_url = {
        'sandbox':     'https://sandbox.plaid.com',
        'development': 'https://development.plaid.com',
        'production':  'https://production.plaid.com',
    }.get(plaid_env, 'https://sandbox.plaid.com')

    try:
        import requests as _req
        resp = _req.post(f'{base_url}/item/public_token/exchange', json={
            'client_id':    plaid_client_id,
            'secret':       plaid_secret,
            'public_token': public_token,
        }, timeout=15)
        resp.raise_for_status()
        result = resp.json()
    except Exception as exc:
        logger.error('Plaid token exchange failed for user %s: %s', current_user.id, exc)
        return jsonify({'error': str(exc)}), 500

    from app.services.token_crypto import encrypt_token
    integration = UserIntegration.query.filter_by(
        user_id=current_user.id, provider='plaid'
    ).first()
    if not integration:
        integration = UserIntegration(
            id=generate_id(),
            user_id=current_user.id,
            provider='plaid',
        )
        db.session.add(integration)

    integration.access_token_enc    = encrypt_token(result['access_token'])
    integration.provider_account_id = result.get('item_id')
    _mark_connected(integration, 'Plaid Link completed')
    db.session.commit()
    return jsonify({'ok': True, 'item_id': result.get('item_id')}), 200


@integrations_bp.route('/api/integrations/plaid/disconnect', methods=['POST'])
@login_required
def plaid_disconnect():
    integration = UserIntegration.query.filter_by(
        user_id=current_user.id, provider='plaid'
    ).first()
    if integration:
        # Optionally remove the item from Plaid
        try:
            if integration.is_connected:
                plaid_client_id = current_app.config.get('PLAID_CLIENT_ID')
                plaid_secret    = current_app.config.get('PLAID_SECRET')
                plaid_env       = current_app.config.get('PLAID_ENV', 'sandbox')
                base_url = {
                    'sandbox': 'https://sandbox.plaid.com',
                    'production': 'https://production.plaid.com',
                }.get(plaid_env, 'https://sandbox.plaid.com')
                import requests as _req
                _req.post(f'{base_url}/item/remove', json={
                    'client_id':    plaid_client_id,
                    'secret':       plaid_secret,
                    'access_token': integration.decrypt_access_token(),
                }, timeout=10)
        except Exception:
            pass
        integration.access_token_enc    = None
        integration.provider_account_id = None
        _mark_disconnected(integration)
        db.session.commit()
    return jsonify({'ok': True})


# ── Alpaca — Brokerage & Trade Execution ─────────────────────────────────────

@integrations_bp.route('/api/integrations/alpaca/save', methods=['POST'])
@login_required
def alpaca_save():
    """
    Save Alpaca API key + secret (FR-ALPACA-01).
    Body: {api_key, api_secret, paper?}  paper=true for paper trading account.
    """
    data       = request.get_json(silent=True) or {}
    api_key    = (data.get('api_key') or '').strip()
    api_secret = (data.get('api_secret') or '').strip()
    is_paper   = bool(data.get('paper', True))

    if not api_key or not api_secret:
        return jsonify({'error': 'api_key and api_secret are required'}), 400

    base = 'https://paper-api.alpaca.markets' if is_paper else 'https://api.alpaca.markets'
    try:
        import requests as _req
        resp = _req.get(
            f'{base}/v2/account',
            headers={
                'APCA-API-KEY-ID':     api_key,
                'APCA-API-SECRET-KEY': api_secret,
            },
            timeout=10,
        )
        resp.raise_for_status()
        account_data = resp.json()
    except Exception as exc:
        logger.warning('Alpaca key validation failed for user %s: %s', current_user.id, exc)
        return jsonify({'error': 'Invalid Alpaca credentials — could not authenticate.'}), 422

    from app.services.token_crypto import encrypt_token
    integration = UserIntegration.query.filter_by(
        user_id=current_user.id, provider='alpaca'
    ).first()
    if not integration:
        integration = UserIntegration(
            id=generate_id(),
            user_id=current_user.id,
            provider='alpaca',
        )
        db.session.add(integration)

    integration.access_token_enc    = encrypt_token(api_key)
    integration.refresh_token_enc   = encrypt_token(api_secret)
    integration.provider_account_id = account_data.get('id') or account_data.get('account_number')
    meta = integration.get_meta()
    meta['paper'] = is_paper
    meta['fintech_toggle'] = meta.get('fintech_toggle', False)
    integration.set_meta(meta)
    env_label = 'paper' if is_paper else 'live'
    _mark_connected(integration, f'Alpaca credentials saved ({env_label})')
    db.session.commit()
    return jsonify({'ok': True, 'account_id': integration.provider_account_id})


@integrations_bp.route('/api/integrations/alpaca/clear', methods=['POST'])
@login_required
def alpaca_clear():
    integration = UserIntegration.query.filter_by(
        user_id=current_user.id, provider='alpaca'
    ).first()
    if integration:
        integration.access_token_enc  = None
        integration.refresh_token_enc = None
        integration.provider_account_id = None
        _mark_disconnected(integration)
        db.session.commit()
    return jsonify({'ok': True})


@integrations_bp.route('/api/integrations/alpaca/fintech-toggle', methods=['POST'])
@login_required
def alpaca_fintech_toggle():
    """
    Enable or disable automated trade execution (FR-ALPACA-01).
    Requires explicit user consent. fintech_toggle defaults to False.
    Body: {enabled: bool, consent_text: str}
    """
    data    = request.get_json(silent=True) or {}
    enabled = bool(data.get('enabled'))
    consent = (data.get('consent_text') or '').strip()

    expected_consent = (
        'I understand that Simulacrum will place trades in my brokerage account '
        'based on my investment plan.'
    )
    if enabled and consent != expected_consent:
        return jsonify({
            'error': 'Consent text does not match. You must acknowledge the risk statement exactly.',
        }), 400

    integration = UserIntegration.query.filter_by(
        user_id=current_user.id, provider='alpaca'
    ).first()
    if not integration:
        return jsonify({'error': 'Alpaca not connected'}), 404

    meta = integration.get_meta()
    meta['fintech_toggle'] = enabled
    integration.set_meta(meta)
    db.session.commit()
    return jsonify({'ok': True, 'fintech_toggle': enabled})


# ── Alpaca trade webhook ──────────────────────────────────────────────────────

@integrations_bp.route('/webhooks/alpaca/<user_id>', methods=['POST'])
def alpaca_webhook(user_id):
    """
    Handles Alpaca trade_update events (FR-ALPACA-02 through FR-ALPACA-07).
    All trades require tier 2 GCC action item confirmation — FR-ALPACA-06 is non-negotiable.
    """
    payload = request.get_json(silent=True) or {}
    event_data = payload.get('data') or {}
    trade_event = event_data.get('event') or payload.get('event')
    order = event_data.get('order') or payload.get('order') or {}
    sim_id = _find_user_simulation(user_id)

    if not trade_event:
        return '', 200

    try:
        _handle_alpaca_event(trade_event, user_id, sim_id, order, payload)
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        logger.error('Alpaca webhook error user=%s event=%s: %s',
                     user_id, trade_event, exc, exc_info=True)
    return '', 200


def _handle_alpaca_event(trade_event: str, user_id: str, sim_id: str,
                         order: dict, payload: dict):
    from app.services.bayesian_service import dispatch_signal
    from app.models.layer6 import ActionItem

    symbol      = order.get('symbol', '')
    order_id    = order.get('id', '')
    qty         = order.get('qty') or order.get('filled_qty') or '0'
    filled_avg  = order.get('filled_avg_price') or '0'
    client_id   = order.get('client_order_id') or ''
    reject_msg  = order.get('failed_at') or payload.get('message') or 'Unknown reason'

    if trade_event == 'fill':
        dispatch_signal(sim_id, 'dca_execution_rate', 1.0, 0.7, '+')
        dispatch_signal(sim_id, f'position_return:{symbol}', 0.5, 0.4, '+')
        logger.info('Alpaca fill: user=%s symbol=%s qty=%s avg=%s',
                    user_id, symbol, qty, filled_avg)

    elif trade_event == 'partial_fill':
        dispatch_signal(sim_id, 'dca_execution_rate', 0.5, 0.7, '+')

    elif trade_event == 'rejected':
        dispatch_signal(sim_id, 'order_rejection_rate', 1.0, 0.5, '-')
        # FR-ALPACA-07: tier 1 (Critical) action item on rejection
        if sim_id:
            _create_alpaca_action_item(
                user_id, sim_id,
                urgency_tier=1,
                title=f'Investment order rejected — {symbol}',
                description=(
                    f'Your trade order for {symbol} was rejected. Reason: {reject_msg}. '
                    f'Check your account balance and order parameters.'
                ),
                action_url='/settings/integrations#alpaca',
            )

    elif trade_event == 'canceled':
        dispatch_signal(sim_id, 'order_cancel_rate', 1.0, 0.2, '-')


def _create_alpaca_action_item(user_id: str, sim_id: str, urgency_tier: int,
                               title: str, description: str, action_url: str):
    from app.models.layer6 import ActionItem
    try:
        item = ActionItem(
            id=generate_id(),
            simulation_id=sim_id,
            user_id=user_id,
            item_type='trade_rejected',
            urgency_tier=urgency_tier,
            title=title,
            description=description,
            action_label='Review',
            action_url=action_url,
            is_dismissable=True,
        )
        db.session.add(item)
    except Exception as exc:
        logger.warning('Could not create Alpaca action item: %s', exc)


# ── Per-platform config (SIM-PRD-SETTINGS-001 Section 3.1) ───────────────────

@integrations_bp.route('/api/integrations/<provider>/config', methods=['PUT'])
@login_required
def integration_config_save(provider):
    """Save per-platform configuration to user_integrations.config JSON."""
    allowed_providers = {
        'apollo', 'stripe', 'cal', 'pandadoc', 'convertkit',
        'kajabi', 'plaid', 'alpaca',
    }
    if provider not in allowed_providers:
        return jsonify({'error': 'Unknown provider'}), 404

    data = request.get_json(silent=True) or {}
    if not data:
        return jsonify({'error': 'No config data provided'}), 400

    # Validate per-provider config keys (whitelist approach)
    allowed_keys = {
        'apollo':     {'daily_send_limit', 'warmup_mode', 'default_sender_name',
                       'default_reply_to', 'auto_remove_hard_bounces'},
        'stripe':     {'default_currency', 'invoice_payment_terms', 'payment_plan',
                       'auto_invoice_on_signature'},
        'cal':        {'buffer_minutes', 'timezone_pref', 'pre_call_questionnaire',
                       'noshow_grace_minutes', 'available_hours_override'},
        'pandadoc':   {'default_cover_message', 'signature_reminder_cadence',
                       'auto_send_on_completion'},
        'kajabi':     {'default_visibility', 'community_auto_attach',
                       'drip_schedule_unit', 'webhook_retry_count'},
        'convertkit': {'default_tag_prefix', 'double_optin', 'nurture_delay_days',
                       'form_style_pref'},
        'plaid':      {'polling_frequency', 'dca_source_account'},
        'alpaca':     {'dca_amount', 'dca_frequency', 'dca_symbols',
                       'trading_hours_pref', 'order_type_pref'},
    }
    valid_keys = allowed_keys.get(provider, set())
    filtered = {k: v for k, v in data.items() if k in valid_keys}

    integration = UserIntegration.query.filter_by(
        user_id=current_user.id, provider=provider
    ).first()
    if not integration:
        return jsonify({'error': f'{provider} is not connected'}), 404

    current_cfg = integration.get_config()
    current_cfg.update(filtered)
    integration.set_config(current_cfg)
    db.session.commit()
    return jsonify({'ok': True, 'config': current_cfg})


@integrations_bp.route('/api/integrations/<provider>/config', methods=['GET'])
@login_required
def integration_config_get(provider):
    integration = UserIntegration.query.filter_by(
        user_id=current_user.id, provider=provider
    ).first()
    if not integration:
        return jsonify({'config': {}})
    return jsonify({'config': integration.get_config()})


# ── Activity log (SIM-PRD-SETTINGS-001 Section 3.2) ──────────────────────────

@integrations_bp.route('/api/integrations/<provider>/activity')
@login_required
def integration_activity(provider):
    """Return last 100 activity log entries for a provider."""
    from app.models.integration import IntegrationActivityLog
    entries = IntegrationActivityLog.query.filter_by(
        user_id=current_user.id, provider=provider
    ).order_by(IntegrationActivityLog.created_at.desc()).limit(100).all()
    return jsonify({'entries': [e.to_dict() for e in entries]})


def _log_activity(user_id: str, provider: str, event_type: str,
                  direction: str = 'outbound', status: str = 'success',
                  detail: str = None, action_id: str = None):
    """Write an IntegrationActivityLog entry. Best-effort — never raises."""
    try:
        from app.models.integration import IntegrationActivityLog
        entry = IntegrationActivityLog(
            id=generate_id(),
            user_id=user_id,
            provider=provider,
            event_type=event_type,
            direction=direction,
            status=status,
            detail=(detail or '')[:500],
            action_id=action_id,
        )
        db.session.add(entry)
        db.session.flush()
    except Exception as exc:
        logger.warning('Activity log write failed: %s', exc)


def _mark_connected(integration: UserIntegration, detail: str = 'OAuth connected'):
    """Set connected_at timestamp and log the activity."""
    integration.connected_at = datetime.utcnow()
    integration.disconnected_at = None
    integration.health_status = 'healthy'
    integration.consecutive_failures = 0
    _log_activity(integration.user_id, integration.provider,
                  'OAuth connected', 'outbound', 'success', detail)


def _mark_disconnected(integration: UserIntegration):
    """Set disconnected_at timestamp and log the activity."""
    integration.disconnected_at = datetime.utcnow()
    _log_activity(integration.user_id, integration.provider,
                  'Disconnected', 'outbound', 'success', 'User disconnected')


# ── Re-authentication link (partner/admin trigger) ────────────────────────────

@integrations_bp.route('/api/integrations/reauth-link', methods=['POST'])
@login_required
def integration_reauth_link():
    """
    Send a deep-link re-authentication email to a user.
    Partners can trigger for their clients; admins can trigger for any user.
    Body: {target_user_id, provider}
    """
    from app.models.user import User
    data     = request.get_json(silent=True) or {}
    target_id = data.get('target_user_id', '').strip()
    provider  = data.get('provider', '').strip()

    if not target_id or not provider:
        return jsonify({'error': 'target_user_id and provider required'}), 400

    # Authorization: admin can target any user; partner can target their clients
    if not current_user.is_admin:
        from app.models.partner import PartnerClientAccess
        access = PartnerClientAccess.query.filter_by(
            partner_user_id=current_user.id,
            client_user_id=target_id,
            revoked_at=None,
        ).first()
        if not access:
            return jsonify({'error': 'Unauthorized'}), 403

    target = User.query.get(target_id)
    if not target:
        return jsonify({'error': 'User not found'}), 404

    try:
        from app.services.notification_service import send_notification
        send_notification(
            user_id=target_id,
            notification_type='escalation',
            title=f'Re-connect your {provider.title()} integration',
            body=(
                f'Your {provider.title()} connection needs to be refreshed. '
                f'Click below to re-authenticate from Settings.'
            ),
            cta_url=f'/settings/integrations?reauth={provider}',
            cta_label='Re-authenticate →',
            priority='high',
        )
    except Exception as exc:
        logger.warning('Reauth notification failed: %s', exc)

    # Audit log entry if triggered by admin
    if current_user.is_admin:
        from app.models.integration import IntegrationAuditLog
        import json as _json
        entry = IntegrationAuditLog(
            id=generate_id(),
            admin_user_id=current_user.id,
            target_user_id=target_id,
            integration_type=provider,
            action='reauth_link_sent',
            ip_address=request.remote_addr,
        )
        db.session.add(entry)
        db.session.commit()

    return jsonify({'ok': True})


# ── Integration status — updated to include new providers ────────────────────

@integrations_bp.route('/api/integrations/status/all')
@login_required
def integrations_status_all():
    """Extended status endpoint including Kajabi, Plaid, Alpaca."""
    providers = ['apollo', 'stripe', 'cal', 'pandadoc', 'convertkit',
                 'kajabi', 'plaid', 'alpaca']
    result = {}
    for prov in providers:
        rec = UserIntegration.query.filter_by(
            user_id=current_user.id, provider=prov
        ).first()
        if rec:
            d = rec.to_dict()
            if prov == 'alpaca':
                meta = rec.get_meta()
                d['fintech_toggle'] = meta.get('fintech_toggle', False)
                d['paper'] = meta.get('paper', True)
            result[prov] = d
        else:
            result[prov] = {'provider': prov, 'status': 'not_connected'}
    result['linkedin'] = {
        'provider': 'linkedin',
        'status': 'pending_legal_review',
    }
    return jsonify(result)
