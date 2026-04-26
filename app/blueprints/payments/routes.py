from flask import request, jsonify
from flask_login import login_required, current_user
from app.blueprints.payments import payments_bp
from app.extensions import db
from app.services.stripe_service import construct_webhook_event
import logging

logger = logging.getLogger(__name__)


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
        # Generation is triggered by the client-side confirm-payment endpoint,
        # not here — updating charge_id only to keep the record accurate.
        pi = event['data']['object']
        sim_id = pi.get('metadata', {}).get('simulation_id')
        if sim_id:
            sim = Simulation.query.get(sim_id)
            if sim:
                if not sim.stripe_charge_id:
                    sim.stripe_charge_id = pi.get('latest_charge')
                db.session.commit()
                # Log partner commission if this user was referred
                try:
                    from app.blueprints.partners.routes import maybe_log_commission
                    charge_cents = pi.get('amount_received') or pi.get('amount', 0)
                    maybe_log_commission(sim_id, sim.user_id, charge_cents)
                except Exception as exc:
                    logger.warning(f'Commission logging failed for sim {sim_id}: {exc}')

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
