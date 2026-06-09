from flask import current_app
import logging

logger = logging.getLogger(__name__)


def _stripe():
    import stripe  # lazy — avoid slow import at startup
    stripe.api_key = current_app.config['STRIPE_SECRET_KEY']
    return stripe


def create_payment_intent(
    user_id: str,
    simulation_id: str,
    amount_cents: int = 69500,
    base_price_cents: int = None,
    discount_percentage: int = 0,
) -> dict:
    """Create a Stripe PaymentIntent for simulation generation."""
    s = _stripe()
    base = base_price_cents if base_price_cents is not None else amount_cents
    desc = f'Simulacrum Pro Simulation — {discount_percentage}% off (was ${base/100:.2f})' \
        if discount_percentage else 'Simulacrum Pro Simulation'
    intent = s.PaymentIntent.create(
        amount=amount_cents,
        currency='usd',
        metadata={
            'user_id': user_id,
            'simulation_id': simulation_id,
            'product': 'simulation_generation',
            'base_price_cents': str(base),
            'discount_percentage': str(discount_percentage),
            'discounted_price_cents': str(amount_cents),
        },
        description=desc,
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


def create_prospect_tier_checkout_session(
    user_id: str,
    simulation_id: str,
    upgrade_to_tier: int,
    delta_cents: int,
    tier_count: int,
    success_url: str,
    cancel_url: str,
) -> dict:
    """Create a Stripe Checkout Session for a per-simulation prospect tier upgrade."""
    s = _stripe()
    session = s.checkout.Session.create(
        payment_method_types=['card'],
        line_items=[{
            'price_data': {
                'currency': 'usd',
                'product_data': {
                    'name': f'Prospect Tier {upgrade_to_tier} — {tier_count} prospects per agent run',
                },
                'unit_amount': delta_cents,
            },
            'quantity': 1,
        }],
        mode='payment',
        success_url=success_url,
        cancel_url=cancel_url,
        metadata={
            'user_id': user_id,
            'simulation_id': simulation_id,
            'product': 'prospect_tier_upgrade',
            'upgrade_to_tier': str(upgrade_to_tier),
            'delta_cents': str(delta_cents),
        },
    )
    return {'checkout_url': session.url, 'session_id': session.id}


def construct_webhook_event(payload: bytes, sig_header: str) -> object:
    s = _stripe()
    return s.Webhook.construct_event(
        payload, sig_header, current_app.config['STRIPE_WEBHOOK_SECRET']
    )
