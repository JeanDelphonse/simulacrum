"""
Stripe Connect Standard — payment object creation on behalf of connected accounts.
All payment objects include the full Simulacrum metadata set (FR-STRIPE-03).
"""
import logging
from urllib.parse import urlencode

from flask import current_app

logger = logging.getLogger(__name__)

STRIPE_OAUTH_URL = 'https://connect.stripe.com/oauth/authorize'

# Action type → income type mapping (FR-STRIPE-03 / Section 2.3)
INCOME_TYPE_MAP = {
    'rate_card':                'consulting_fee',
    'consulting_proposal':      'project_fee',
    'sow_template':             'project_fee',
    'workshop_curriculum':      'workshop_ticket',
    'waitlist_landing_page':    'workshop_ticket',
    'sales_page':               'course_sale',
    'membership_structure':     'membership',
    'saas_product_spec':        'saas_subscription',
    'newsletter_monetization':  'newsletter_sponsorship',
}

# Action type → layer number mapping
LAYER_MAP = {
    'rate_card':                1,
    'consulting_proposal':      1,
    'sow_template':             1,
    'workshop_curriculum':      2,
    'waitlist_landing_page':    2,
    'sales_page':               3,
    'membership_structure':     3,
    'saas_product_spec':        4,
    'newsletter_monetization':  4,
}

PAYMENT_ACTION_TYPES = set(INCOME_TYPE_MAP.keys())


# ── OAuth helpers ─────────────────────────────────────────────────────────────

def get_oauth_url(state: str) -> str:
    params = {
        'response_type': 'code',
        'client_id': current_app.config['STRIPE_CLIENT_ID'],
        'scope': 'read_write',
        'state': state,
        'redirect_uri': f"{current_app.config['BASE_URL']}/api/integrations/stripe/callback",
    }
    return f"{STRIPE_OAUTH_URL}?{urlencode(params)}"


def exchange_code(code: str) -> dict:
    import stripe
    stripe.api_key = current_app.config['STRIPE_SECRET_KEY']
    response = stripe.OAuth.token(grant_type='authorization_code', code=code)
    return {
        'access_token':    response.access_token,
        'stripe_user_id':  response.stripe_user_id,   # acct_xxxx
        'scope':           response.scope,
    }


def deauthorize_account(stripe_account_id: str):
    import stripe
    stripe.api_key = current_app.config['STRIPE_SECRET_KEY']
    try:
        stripe.OAuth.deauthorize(
            client_id=current_app.config['STRIPE_CLIENT_ID'],
            stripe_user_id=stripe_account_id,
        )
    except Exception as exc:
        logger.warning('Stripe deauthorize failed for %s: %s', stripe_account_id, exc)


# ── Connected account Stripe client ──────────────────────────────────────────

def _connected_stripe(access_token: str):
    import stripe
    # Return module configured with the connected account's access token
    stripe.api_key = access_token
    return stripe


def _get_integration(user_id: str):
    from app.models.integration import UserIntegration
    integration = UserIntegration.query.filter_by(
        user_id=user_id, provider='stripe'
    ).first()
    if not integration or not integration.is_connected:
        raise StripeAuthRequired('stripe_auth_required')
    token = integration.decrypt_access_token()
    return integration, _connected_stripe(token)


# ── Simulacrum metadata builder ───────────────────────────────────────────────

def _meta(simulation_id, layer_number, action_type, action_id,
          artifact_id, income_type, user_id) -> dict:
    return {
        'simulacrum_simulation_id': simulation_id or '',
        'simulacrum_layer_number':  str(layer_number or ''),
        'simulacrum_action_type':   action_type or '',
        'simulacrum_action_id':     action_id or '',
        'simulacrum_artifact_id':   artifact_id or '',
        'simulacrum_income_type':   income_type or '',
        'simulacrum_user_id':       user_id or '',
    }


# ── Payment object creation ───────────────────────────────────────────────────

def create_stripe_product(user_id, product_name, description=None,
                          simulation_id=None, layer_number=None,
                          action_type=None, action_id=None,
                          artifact_id=None, income_type=None) -> dict:
    _, s = _get_integration(user_id)
    metadata = _meta(simulation_id, layer_number, action_type,
                     action_id, artifact_id, income_type, user_id)
    params = {'name': product_name, 'metadata': metadata}
    if description:
        params['description'] = description
    product = s.Product.create(**params)
    return {'product_id': product.id, 'name': product.name}


def create_stripe_price(user_id, product_id, amount_cents, currency='usd',
                        recurring_interval=None,
                        simulation_id=None, layer_number=None,
                        action_type=None, action_id=None,
                        artifact_id=None, income_type=None) -> dict:
    _, s = _get_integration(user_id)
    metadata = _meta(simulation_id, layer_number, action_type,
                     action_id, artifact_id, income_type, user_id)
    params = {
        'unit_amount': amount_cents,
        'currency': currency,
        'product': product_id,
        'metadata': metadata,
    }
    if recurring_interval:
        params['recurring'] = {'interval': recurring_interval}
    price = s.Price.create(**params)
    return {'price_id': price.id, 'amount': price.unit_amount, 'currency': price.currency}


def create_stripe_payment_link(user_id, product_name, amount_cents,
                                currency='usd', description=None,
                                simulation_id=None, layer_number=None,
                                action_type=None, action_id=None,
                                artifact_id=None, income_type=None) -> dict:
    _, s = _get_integration(user_id)
    metadata = _meta(simulation_id, layer_number, action_type,
                     action_id, artifact_id, income_type, user_id)

    product = s.Product.create(
        name=product_name,
        description=description or '',
        metadata=metadata,
    )
    price = s.Price.create(
        unit_amount=amount_cents,
        currency=currency,
        product=product.id,
        metadata=metadata,
    )
    link = s.PaymentLink.create(
        line_items=[{'price': price.id, 'quantity': 1}],
        metadata=metadata,
    )
    return {
        'payment_link_url': link.url,
        'payment_link_id': link.id,
        'product_id': product.id,
        'price_id': price.id,
    }


def create_stripe_invoice(user_id, customer_email, product_name,
                           amount_cents, currency='usd', description=None,
                           simulation_id=None, layer_number=None,
                           action_type=None, action_id=None,
                           artifact_id=None, income_type=None) -> dict:
    _, s = _get_integration(user_id)
    metadata = _meta(simulation_id, layer_number, action_type,
                     action_id, artifact_id, income_type, user_id)

    # Create or retrieve customer
    customers = s.Customer.list(email=customer_email, limit=1)
    if customers.data:
        customer = customers.data[0]
    else:
        customer = s.Customer.create(email=customer_email, metadata=metadata)

    # Create invoice item then invoice
    s.InvoiceItem.create(
        customer=customer.id,
        amount=amount_cents,
        currency=currency,
        description=description or product_name,
        metadata=metadata,
    )
    invoice = s.Invoice.create(
        customer=customer.id,
        collection_method='send_invoice',
        days_until_due=30,
        metadata=metadata,
    )
    finalized = s.Invoice.finalize_invoice(invoice.id)
    return {
        'invoice_id': finalized.id,
        'invoice_url': finalized.hosted_invoice_url,
        'invoice_pdf': finalized.invoice_pdf,
        'status': finalized.status,
    }


def create_stripe_checkout_session(user_id, product_name, amount_cents,
                                    currency='usd', description=None,
                                    success_url=None, cancel_url=None,
                                    simulation_id=None, layer_number=None,
                                    action_type=None, action_id=None,
                                    artifact_id=None, income_type=None) -> dict:
    base_url = current_app.config.get('BASE_URL', 'http://localhost:5000')
    _, s = _get_integration(user_id)
    metadata = _meta(simulation_id, layer_number, action_type,
                     action_id, artifact_id, income_type, user_id)

    product = s.Product.create(name=product_name, metadata=metadata)
    price = s.Price.create(
        unit_amount=amount_cents,
        currency=currency,
        product=product.id,
        metadata=metadata,
    )
    session = s.checkout.Session.create(
        line_items=[{'price': price.id, 'quantity': 1}],
        mode='payment',
        success_url=success_url or f'{base_url}/dashboard?payment=success',
        cancel_url=cancel_url or f'{base_url}/dashboard?payment=cancelled',
        metadata=metadata,
    )
    return {
        'checkout_url': session.url,
        'session_id': session.id,
        'product_id': product.id,
        'price_id': price.id,
    }


def create_stripe_subscription(user_id, product_name, amount_cents,
                                recurring_interval='month', currency='usd',
                                description=None,
                                simulation_id=None, layer_number=None,
                                action_type=None, action_id=None,
                                artifact_id=None, income_type=None) -> dict:
    _, s = _get_integration(user_id)
    metadata = _meta(simulation_id, layer_number, action_type,
                     action_id, artifact_id, income_type, user_id)

    product = s.Product.create(name=product_name, metadata=metadata)
    price = s.Price.create(
        unit_amount=amount_cents,
        currency=currency,
        product=product.id,
        recurring={'interval': recurring_interval},
        metadata=metadata,
    )
    link = s.PaymentLink.create(
        line_items=[{'price': price.id, 'quantity': 1}],
        metadata=metadata,
    )
    return {
        'payment_link_url': link.url,
        'payment_link_id': link.id,
        'product_id': product.id,
        'price_id': price.id,
        'recurring_interval': recurring_interval,
    }


# ── Income attribution from webhook event ────────────────────────────────────

def attribute_income_from_stripe_event(event_object: dict, stripe_account_id: str):
    """
    Read Simulacrum metadata from a Stripe payment event and create a
    LayerIncomeRecord. Also advances paying contact to 'client' stage.
    Called by the Connect webhook handler.
    """
    from app.extensions import db
    from app.models.income import LayerIncomeRecord
    from app.models.contact import Contact
    from app.models.integration import UserIntegration
    from utils.id_gen import generate_id

    meta = event_object.get('metadata') or {}
    simulation_id = meta.get('simulacrum_simulation_id')
    action_id     = meta.get('simulacrum_action_id')
    action_type   = meta.get('simulacrum_action_type')
    artifact_id   = meta.get('simulacrum_artifact_id')
    income_type   = meta.get('simulacrum_income_type')
    user_id       = meta.get('simulacrum_user_id')
    layer_number  = meta.get('simulacrum_layer_number')

    if not simulation_id or not user_id:
        logger.debug('Stripe Connect event missing simulacrum metadata — skipping attribution')
        return

    # Verify connected account belongs to this user
    integration = UserIntegration.query.filter_by(
        provider_account_id=stripe_account_id, provider='stripe'
    ).first()
    if not integration:
        logger.warning('No integration for Stripe account %s', stripe_account_id)
        return

    # Determine amount
    amount_cents = (
        event_object.get('amount_total')
        or event_object.get('amount_paid')
        or event_object.get('amount')
        or 0
    )
    amount = round(amount_cents / 100, 2) if amount_cents else 0
    currency = (event_object.get('currency') or 'usd').upper()

    source_ref = event_object.get('id', '')

    # Idempotency — skip if already recorded
    existing = LayerIncomeRecord.query.filter_by(source_ref=source_ref).first()
    if existing:
        return

    try:
        layer_num = int(layer_number) if layer_number else 0
    except (TypeError, ValueError):
        layer_num = 0

    record = LayerIncomeRecord(
        id=generate_id(),
        simulation_id=simulation_id,
        layer_number=layer_num,
        action_id=action_id or None,
        action_type=action_type or None,
        amount=amount,
        currency=currency,
        source=LayerIncomeRecord.SOURCE_STRIPE,
        source_ref=source_ref,
        description=f'{income_type or action_type or "payment"} via Stripe',
        recorded_by=user_id,
    )
    db.session.add(record)

    # Advance paying contact to 'client' if found
    customer_email = (
        (event_object.get('customer_details') or {}).get('email')
        or event_object.get('customer_email')
    )
    if customer_email:
        contact = Contact.query.filter_by(
            user_id=user_id, email=customer_email
        ).first()
        if contact and not contact.do_not_contact:
            contact.advance_stage('client', created_by='webhook',
                                  simulation_id=simulation_id,
                                  action_id=action_id)

    db.session.commit()
    logger.info('Income attributed: sim=%s amount=%s%s ref=%s',
                simulation_id, amount, currency, source_ref)


# ── Custom exception ──────────────────────────────────────────────────────────

class StripeAuthRequired(Exception):
    pass
