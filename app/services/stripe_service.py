from __future__ import annotations  # PEP 604 unions (str | None) on Python < 3.10
from flask import current_app
import logging

logger = logging.getLogger(__name__)


def _stripe():
    import stripe  # lazy — avoid slow import at startup
    stripe.api_key = current_app.config['STRIPE_SECRET_KEY']
    return stripe


def create_org_invoice(org, inv) -> str | None:
    """Create + send a Stripe invoice for an org credit purchase (SIM-PRD-ORG-001).

    Uses Stripe Invoicing (ACH/wire/card, net terms). Returns the Stripe invoice
    id, or None if Stripe is unconfigured/unavailable (the OrgInvoice record
    remains the source of truth so PO/manual invoices still work).
    """
    if not current_app.config.get('STRIPE_SECRET_KEY'):
        return None
    s = _stripe()
    customer = s.Customer.create(
        name=org.org_name,
        email=org.contact_email,
        metadata={'org_id': org.id},
    )
    s.InvoiceItem.create(
        customer=customer.id,
        amount=inv.amount_cents,
        currency='usd',
        description=f'{inv.credits} SimiSI simulation credits'
                    + (f' (PO {inv.po_number})' if inv.po_number else ''),
    )
    invoice = s.Invoice.create(
        customer=customer.id,
        collection_method='send_invoice',
        days_until_due=inv.net_terms,
        metadata={'org_id': org.id, 'org_invoice_id': inv.id,
                  'po_number': inv.po_number or ''},
    )
    try:
        invoice.finalize_invoice()
        invoice.send_invoice()
    except Exception as exc:  # finalize/send is best-effort
        logger.warning('org invoice finalize/send failed: %s', exc)
    return invoice.id


def create_payment_intent(
    user_id: str,
    simulation_id: str,
    amount_cents: int,
    base_price_cents: int = None,
    discount_percentage: int = 0,
) -> dict:
    """Create a Stripe PaymentIntent for simulation generation."""
    s = _stripe()
    base = base_price_cents if base_price_cents is not None else amount_cents
    desc = f'SimiSI Pro Simulation — {discount_percentage}% off (was ${base/100:.2f})' \
        if discount_percentage else 'SimiSI Pro Simulation'
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


def create_voice_training_checkout(user_id: str, success_url: str, cancel_url: str) -> dict:
    """Create a $4.99 Stripe Checkout Session for voice training add-on."""
    s = _stripe()
    session = s.checkout.Session.create(
        payment_method_types=['card'],
        line_items=[{
            'price_data': {
                'currency': 'usd',
                'product_data': {'name': 'Voice Training — SimiSI'},
                'unit_amount': 499,
            },
            'quantity': 1,
        }],
        mode='payment',
        success_url=success_url,
        cancel_url=cancel_url,
        metadata={'user_id': user_id, 'product': 'voice_training'},
    )
    return {'checkout_url': session.url, 'session_id': session.id}


FOUNDER_PRODUCTS = ('founder_track', 'founder_addon')


def founder_addon_eligible(user_id: str) -> bool:
    """Founder Track add-on price: the member already has a completed
    simulation or a published bio page (FOUNDER_PLAN.md WP3)."""
    if not user_id:
        return False
    from app.models.bio_page import BioPage
    from app.models.simulation import Simulation
    if Simulation.query.filter_by(user_id=user_id, status=Simulation.STATUS_COMPLETE).first() is not None:
        return True
    return BioPage.query.filter_by(user_id=user_id, status=BioPage.STATUS_PUBLISHED).first() is not None


def create_founder_checkout(venture, user, product: str, success_url: str = None, cancel_url: str = None) -> dict:
    """One-time Stripe Checkout Session for the Founder Track (FOUNDER_PLAN.md WP3).

    product is 'founder_track' (standalone price) or 'founder_addon' (the
    add-on price, only for members who are eligible). Prices come from
    founder_config.pricing(), never from the simulation price, sales or comps.
    Raises ValueError for an unknown product or an ineligible add-on."""
    from flask import url_for
    from app.services import founder_config
    if product not in FOUNDER_PRODUCTS:
        raise ValueError('Unknown Founder Track product.')
    if product == 'founder_addon' and not founder_addon_eligible(user.id):
        raise ValueError('The add-on price is for members with a completed simulation or a published bio page.')
    prices = founder_config.pricing()
    if prices.get('fee_mode') != 'one_time':
        raise ValueError('Only one-time Founder Track payments are supported.')
    amount = int(prices['addon_price_cents'] if product == 'founder_addon' else prices['price_cents'])
    if amount < 50:
        raise ValueError('The Founder Track price is below the card minimum.')
    if success_url is None:
        success_url = url_for('founder.checkout_success', venture_id=venture.id, _external=True) \
            + '?session_id={CHECKOUT_SESSION_ID}'
    if cancel_url is None:
        cancel_url = url_for('founder.checkout_page', venture_id=venture.id, cancelled=1, _external=True)
    extra = {'customer_email': user.email} if getattr(user, 'email', None) else {}
    s = _stripe()
    session = s.checkout.Session.create(
        payment_method_types=['card'],
        line_items=[{
            'price_data': {
                'currency': prices.get('currency') or 'usd',
                'product_data': {'name': 'SimiSI Founder Track' + (' (add-on)' if product == 'founder_addon' else '')
                                         + ' — ' + (venture.name or 'Venture')[:80]},
                'unit_amount': amount,
            },
            'quantity': 1,
        }],
        mode='payment',
        client_reference_id=venture.id,
        success_url=success_url,
        cancel_url=cancel_url,
        metadata={'product': product, 'venture_id': venture.id, 'user_id': user.id,
                  'amount_cents': str(amount)},
        **extra,
    )
    return {'checkout_url': session.url, 'session_id': session.id, 'amount_cents': amount, 'product': product}


def construct_webhook_event(payload: bytes, sig_header: str) -> object:
    s = _stripe()
    return s.Webhook.construct_event(
        payload, sig_header, current_app.config['STRIPE_WEBHOOK_SECRET']
    )
