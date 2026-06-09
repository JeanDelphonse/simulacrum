from flask import request, jsonify
from flask_login import login_required, current_user
from app.blueprints.payments import payments_bp
from app.extensions import db
from app.services.stripe_service import construct_webhook_event
import logging

logger = logging.getLogger(__name__)


def _stripe_connect_required(user_id: str) -> bool:
    """Return True if the user has a connected Stripe account."""
    from app.models.integration import UserIntegration
    integration = UserIntegration.query.filter_by(
        user_id=user_id, provider='stripe'
    ).first()
    return bool(integration and integration.is_connected)


@payments_bp.route('/webhook', methods=['POST'])
def stripe_webhook():
    """Handle Stripe webhook events."""
    payload = request.get_data()
    sig_header = request.headers.get('Stripe-Signature')

    try:
        event = construct_webhook_event(payload, sig_header)
    except Exception as e:
        logger.error(f'Stripe webhook signature verification failed: {e}')
        return jsonify({'error': 'Invalid signature'}), 400

    from app.models.simulation import Simulation

    if event['type'] == 'payment_intent.succeeded':
        pi = event['data']['object']
        meta = pi.get('metadata') or {}
        sim_id = meta.get('simulation_id')
        connect_sim_id = meta.get('simulacrum_simulation_id')

        if sim_id and not connect_sim_id:
            # Platform payment for simulation generation
            sim = Simulation.query.get(sim_id)
            if sim:
                if not sim.stripe_charge_id:
                    sim.stripe_charge_id = pi.get('latest_charge')
                # Store discount fields from PaymentIntent metadata (FR-DISC-07)
                try:
                    sim.base_price_at_purchase_cents = int(meta.get('base_price_cents') or sim.amount_charged_cents or 0)
                    sim.discount_applied_percentage = int(meta.get('discount_percentage') or 0)
                    actual_paid = int(meta.get('discounted_price_cents') or pi.get('amount_received') or pi.get('amount') or 0)
                    if actual_paid:
                        sim.amount_charged_cents = actual_paid
                except Exception:
                    pass
                db.session.commit()
                try:
                    from app.blueprints.partners.routes import maybe_log_commission
                    charge_cents = pi.get('amount_received') or pi.get('amount', 0)
                    maybe_log_commission(sim_id, sim.user_id, charge_cents)
                except Exception as exc:
                    logger.warning(f'Commission logging failed for sim {sim_id}: {exc}')

        elif connect_sim_id:
            # Connect payment with simulacrum metadata — attribute income
            stripe_account = event.get('account')
            try:
                from app.services.stripe_connect_service import attribute_income_from_stripe_event
                attribute_income_from_stripe_event(pi, stripe_account)
            except Exception as exc:
                logger.error('Income attribution failed for payment_intent: %s', exc, exc_info=True)

    elif event['type'] == 'checkout.session.completed':
        session = event['data']['object']
        meta = session.get('metadata') or {}
        if meta.get('product') == 'prospect_tier_upgrade':
            sim_id = meta.get('simulation_id')
            try:
                upgrade_to_tier = int(meta.get('upgrade_to_tier', 0))
                delta_cents = int(meta.get('delta_cents', 0))
            except (TypeError, ValueError):
                upgrade_to_tier = delta_cents = 0
            if sim_id and upgrade_to_tier in (2, 3):
                sim = Simulation.query.get(sim_id)
                if sim and (sim.prospect_tier or 1) < upgrade_to_tier:
                    sim.prospect_tier = upgrade_to_tier
                    sim.prospect_tier_paid_cents = (sim.prospect_tier_paid_cents or 0) + delta_cents
                    db.session.commit()
                    logger.info(
                        'Prospect tier upgraded to %d for sim %s (+%d cents)',
                        upgrade_to_tier, sim_id, delta_cents,
                    )

    elif event['type'] == 'payment_intent.payment_failed':
        pi = event['data']['object']
        sim_id = pi.get('metadata', {}).get('simulation_id')
        if sim_id:
            sim = Simulation.query.get(sim_id)
            if sim:
                sim.status = Simulation.STATUS_ERROR
                sim.error_message = 'Payment failed'
                db.session.commit()

    elif event['type'] == 'charge.refunded':
        charge = event['data']['object']
        pi_id = charge.get('payment_intent')
        if pi_id:
            sim = Simulation.query.filter_by(stripe_payment_intent_id=pi_id).first()
            if sim:
                if sim.status == Simulation.STATUS_ERROR:
                    sim.status = Simulation.STATUS_REFUNDED
                db.session.commit()
                logger.info(f'Simulation {sim.id} marked refunded via charge.refunded webhook')
                # Mark any pending commissions for this simulation as refunded
                try:
                    from app.models.partner import Commission
                    Commission.query.filter_by(
                        simulation_id=sim.id,
                        status=Commission.STATUS_PENDING,
                    ).update({'status': Commission.STATUS_REFUNDED})
                    db.session.commit()
                except Exception as exc:
                    logger.warning(f'Commission refund marking failed for sim {sim.id}: {exc}')

    elif event['type'] == 'charge.dispute.created':
        charge = event['data']['object']
        pi_id = charge.get('payment_intent')
        if pi_id:
            sim = Simulation.query.filter_by(stripe_payment_intent_id=pi_id).first()
            if sim:
                AuditLog.log('stripe_dispute_created', resource_id=sim.id,
                             metadata={'charge_id': charge.get('id')})
                db.session.commit()
                logger.warning(f'Stripe dispute created for simulation {sim.id}')

    return jsonify({'received': True}), 200
