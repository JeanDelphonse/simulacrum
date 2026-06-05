from flask import current_app
import logging

logger = logging.getLogger(__name__)


def _stripe():
    import stripe  # lazy — avoid slow import at startup
    stripe.api_key = current_app.config['STRIPE_SECRET_KEY']
    return stripe


def create_payment_intent(user_id: str, simulation_id: str, amount_cents: int = 69500) -> dict:
    """Create a Stripe PaymentIntent for $695.00 simulation generation."""
    s = _stripe()
    intent = s.PaymentIntent.create(
        amount=amount_cents,
        currency='usd',
        metadata={
            'user_id': user_id,
            'simulation_id': simulation_id,
            'product': 'simulation_generation',
        },
        description=f'Simulacrum Simulation Generation — {simulation_id}',
    )
    return {
        'payment_intent_id': intent.id,
        'client_secret': intent.client_secret,
        'amount': intent.amount,
        'status': intent.status,
    }


def confirm_payment_intent(payment_intent_id: str) -> dict:
    """Retrieve and verify a PaymentIntent status."""
    s = _stripe()
    intent = s.PaymentIntent.retrieve(payment_intent_id)
    return {
        'id': intent.id,
        'status': intent.status,
        'amount': intent.amount,
        'charge_id': intent.latest_charge,
    }


def issue_refund(payment_intent_id: str, reason: str = 'Simulation generation failed') -> dict:
    """Issue a full refund for a PaymentIntent."""
    s = _stripe()
    intent = s.PaymentIntent.retrieve(payment_intent_id)
    if not intent.latest_charge:
        raise ValueError('No charge found on PaymentIntent to refund')
    refund = s.Refund.create(
        charge=intent.latest_charge,
        reason='duplicate',
        metadata={'reason': reason},
    )
    return {
        'refund_id': refund.id,
        'status': refund.status,
        'amount': refund.amount,
    }


def construct_webhook_event(payload: bytes, sig_header: str) -> object:
    s = _stripe()
    return s.Webhook.construct_event(
        payload, sig_header, current_app.config['STRIPE_WEBHOOK_SECRET']
    )
