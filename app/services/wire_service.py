"""
Artifact-to-Integration Wiring — SIM-PRD-WIRE-001.

deploy_to_integration() reads an artifact's action_type, determines which
integrations to call via AGENT_INTEGRATION_MAP, and executes each step
independently. Failure of one integration never blocks the others.

FR-WIRE-01: every action_type has a defined deployment chain here.
FR-WIRE-10: partial deployment is acceptable — failed steps escalate.
"""
from __future__ import annotations
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

# Integration provider keys — match UserIntegration.provider values
APOLLO     = 'apollo'
CALCOM     = 'cal.com'
CONVERTKIT = 'convertkit'
PANDADOC   = 'pandadoc'
STRIPE     = 'stripe'
KAJABI     = 'kajabi'
LINKEDIN   = 'linkedin'

# ---------------------------------------------------------------------------
# Deployment chain map  (FR-WIRE-01)
# ---------------------------------------------------------------------------
# Each entry is a list of provider keys.  The orchestrator deploys each
# independently; missing or failed integrations escalate to GCC action items.

AGENT_INTEGRATION_MAP: dict[str, list[str]] = {
    # ── L1 Active Income ─────────────────────────────────────────────────
    'cold_email_campaign':          [APOLLO, CONVERTKIT],
    'consulting_outreach':          [APOLLO, PANDADOC, CALCOM],
    'outreach_email':               [APOLLO],
    'referral_network':             [APOLLO],
    'booking_page':                 [CALCOM],
    'consulting_proposal':          [PANDADOC, STRIPE, CALCOM, LINKEDIN],
    'consulting_agreement':         [PANDADOC, STRIPE],
    'rate_card':                    [PANDADOC],
    'social_proof':                 [APOLLO],
    'linkedin_optimize':            [LINKEDIN],

    # ── L2 Leveraged Delivery ─────────────────────────────────────────────
    'speaking_proposals':           [APOLLO, CALCOM, CONVERTKIT, LINKEDIN],
    'speaker_fee_rider':            [PANDADOC],
    'coaching_curriculum':          [CALCOM, CONVERTKIT, STRIPE, KAJABI],
    'corporate_training_proposal':  [APOLLO, PANDADOC],
    'workshop_content':             [CALCOM, CONVERTKIT, PANDADOC, APOLLO],
    'waitlist_landing_page':        [CONVERTKIT],
    'alumni_reactivation':          [APOLLO, CONVERTKIT],

    # ── L3 Productized Income ─────────────────────────────────────────────
    'course_framework':             [KAJABI, STRIPE, CONVERTKIT, LINKEDIN, CALCOM],
    'sales_page':                   [STRIPE, CONVERTKIT],
    'ebook_guide':                  [CONVERTKIT, KAJABI],
    'launch_email_sequence':        [CONVERTKIT],
    'affiliate_program':            [APOLLO, CONVERTKIT],
    'membership_structure':         [KAJABI, STRIPE, CONVERTKIT],
    'lapsed_buyer_reactivation':    [CONVERTKIT],

    # ── L4 Automated / Residual ───────────────────────────────────────────
    'funnel_design':                [CONVERTKIT, CALCOM],
    'newsletter_monetization':      [CONVERTKIT, STRIPE],
    'lead_magnet_funnel':           [CONVERTKIT, CALCOM, LINKEDIN],
    'seo_content_calendar':         [LINKEDIN],
    'youtube_podcast':              [LINKEDIN, CONVERTKIT],
    'community_flywheel':           [CONVERTKIT, LINKEDIN],
}


# ---------------------------------------------------------------------------
# Integration loader
# ---------------------------------------------------------------------------

def _load_integrations(user_id: str) -> dict:
    """Return {provider: UserIntegration} for all healthy connected integrations."""
    try:
        from app.models.integration import UserIntegration
        rows = UserIntegration.query.filter_by(user_id=user_id).all()
        return {
            r.provider: r for r in rows
            if r.is_connected and not r.is_expired
            and r.health_status not in ('disabled', 'error')
        }
    except Exception as exc:
        logger.warning('wire_service: could not load integrations for %s: %s', user_id, exc)
        return {}


# ---------------------------------------------------------------------------
# Per-integration deployers
# ---------------------------------------------------------------------------

def _deploy_apollo(rec, *, user_id, action_type, artifact, simulation_id,
                   action_id, **_kw) -> dict:
    """
    For outreach-type agents: upsert researched prospects as Apollo contacts
    and enrol them in a sequence if the artifact contains prospect data.
    FR-WIRE-02.
    """
    from app.services.apollo_client import ApolloClient
    from app.services.token_crypto import decrypt_token

    token = decrypt_token(rec.access_token_enc)
    client = ApolloClient(token)

    # Try to parse structured prospect list from artifact (raw_decode tolerates trailing text)
    prospects = []
    try:
        data, _ = json.JSONDecoder().raw_decode(artifact.strip())
        prospects = data.get('prospects', [])
    except (json.JSONDecodeError, AttributeError, ValueError):
        pass

    if not prospects:
        logger.debug('Apollo deploy: no structured prospects in %s artifact', action_type)
        return {'contacts_upserted': 0}

    upserted = 0
    for p in prospects:
        email = p.get('email', '')
        if not email:
            continue
        try:
            client.upsert_contact(
                email=email,
                first_name=p.get('first_name', ''),
                last_name=p.get('last_name', ''),
                title=p.get('job_title'),
                company=p.get('company_name'),
                custom_fields={'simulacrum_simulation_id': simulation_id,
                               'simulacrum_action_id': action_id},
            )
            upserted += 1
        except Exception as exc:
            logger.warning('Apollo upsert failed for %s: %s', email, exc)

    logger.info('Apollo deploy: upserted %d contacts for %s', upserted, action_type)
    return {'contacts_upserted': upserted}


def _deploy_calcom(rec, *, user_id, action_type, artifact, simulation_id,
                   action_id, layer_number, **_kw) -> dict:
    """
    For booking-creation agents: create a Cal.com event type from the artifact.
    For URL-embedding agents: nothing to do at deploy time (URL injected at generation).
    FR-WIRE-03.
    """
    from app.services.cal_service import deploy_booking_page
    from app.services.token_crypto import decrypt_token

    # Only these three agents actually CREATE event types
    CREATE_EVENT_TYPES = {'booking_page', 'coaching_curriculum', 'workshop_content'}
    if action_type not in CREATE_EVENT_TYPES:
        return {'action': 'url_embedded_at_generation'}

    token = decrypt_token(rec.access_token_enc)

    # Parse artifact to find meeting config
    artifact_dict: dict = {}
    try:
        artifact_dict = json.loads(artifact)
    except (json.JSONDecodeError, AttributeError):
        pass

    result = deploy_booking_page(
        user_id=user_id,
        simulation_id=simulation_id,
        action_id=action_id,
        artifact=artifact_dict,
    )
    logger.info('Cal.com event type created for %s: %s', action_type, result)
    return result


def _deploy_convertkit(rec, *, user_id, action_type, artifact, simulation_id,
                       action_id, **_kw) -> dict:
    """
    Add the action's leads/audience to ConvertKit with an action-specific tag.
    For sequence agents: parse sequences from artifact and create broadcasts.
    FR-WIRE-04.
    """
    from app.services.convertkit_service import ConvertKitClient
    from app.services.token_crypto import decrypt_token

    secret = decrypt_token(rec.access_token_enc)
    client = ConvertKitClient(secret)

    tag = f'simulacrum:{action_type}'

    # For launch_email_sequence: try to find sequences in the artifact
    sequences_added = 0
    if action_type in ('launch_email_sequence', 'funnel_design', 'waitlist_landing_page'):
        try:
            data = json.loads(artifact)
            sequences = client.list_sequences()
            if sequences:
                seq_id = sequences[0]['id']  # Use first available sequence
                # Prospect contacts from CRM go into the sequence
                from app.models.contact import Contact
                contacts = Contact.query.filter_by(
                    source_action_id=action_id
                ).limit(50).all()
                for c in contacts:
                    if c.email:
                        try:
                            client.add_to_sequence(seq_id, c.email,
                                                   first_name=c.first_name or '')
                            sequences_added += 1
                        except Exception as exc:
                            logger.warning('ConvertKit sequence enrol failed: %s', exc)
        except Exception as exc:
            logger.warning('ConvertKit sequence deploy failed: %s', exc)

    # Tag all contacts associated with this action
    contacts_tagged = 0
    try:
        from app.models.contact import Contact
        contacts = Contact.query.filter_by(source_action_id=action_id).limit(100).all()
        for c in contacts:
            if c.email:
                try:
                    client.add_subscriber(c.email, first_name=c.first_name or '',
                                          tags=[tag, 'simulacrum_lead'])
                    contacts_tagged += 1
                except Exception as exc:
                    logger.warning('ConvertKit subscriber tag failed: %s', exc)
    except Exception as exc:
        logger.warning('ConvertKit contacts query failed: %s', exc)

    logger.info('ConvertKit deploy: tagged %d contacts, %d sequence enrolments for %s',
                contacts_tagged, sequences_added, action_type)
    return {'contacts_tagged': contacts_tagged, 'sequences_added': sequences_added}


def _deploy_pandadoc(rec, *, user_id, simulation_id, action_id, action_type,
                     artifact, layer_number, **_kw) -> dict:
    """
    Convert the artifact to a PandaDoc document and send for e-signature.
    Recipient is taken from the first active/prospect CRM contact linked to
    this action, or escalated if none found.
    FR-WIRE-05.
    """
    from app.services.pandadoc_service import deploy_document_for_signing
    from app.services.token_crypto import decrypt_token

    # Find a recipient from CRM contacts linked to this action
    recipient_email = ''
    recipient_name = ''
    try:
        from app.models.contact import Contact
        c = Contact.query.filter_by(source_action_id=action_id).first()
        if c:
            recipient_email = c.email or ''
            recipient_name = c.display_name or ''
    except Exception:
        pass

    if not recipient_email:
        return {'skipped': True, 'reason': 'no_recipient_found'}

    label = action_type.replace('_', ' ').title()
    content_html = f'<pre style="font-family:sans-serif">{artifact[:8000]}</pre>'

    result = deploy_document_for_signing(
        user_id=user_id,
        simulation_id=simulation_id,
        action_id=action_id,
        action_type=action_type,
        artifact_version_id=None,
        layer_number=layer_number,
        recipient_email=recipient_email,
        recipient_name=recipient_name,
        document_title=label,
        content_html=content_html,
    )
    logger.info('PandaDoc document deployed for %s → %s', action_type, recipient_email)
    return result


def _deploy_stripe(rec, *, user_id, simulation_id, action_id, action_type,
                   artifact, layer_number, **_kw) -> dict:
    """
    Create a Stripe Payment Link on the user's connected Stripe account with
    full attribution metadata.  FR-WIRE-06.
    """
    try:
        import stripe as _stripe
        from flask import current_app
        _stripe.api_key = current_app.config.get('STRIPE_SECRET_KEY', '')
    except Exception as exc:
        return {'skipped': True, 'reason': f'stripe_import_failed: {exc}'}

    stripe_account_id = rec.provider_account_id
    if not stripe_account_id:
        return {'skipped': True, 'reason': 'no_stripe_account_id'}

    # Derive a product name and price from the artifact
    product_name = action_type.replace('_', ' ').title()
    unit_amount = 50000  # $500 default placeholder

    try:
        data = json.loads(artifact)
        if isinstance(data, dict):
            rate = data.get('rate') or data.get('price') or data.get('hourly_rate')
            if rate:
                try:
                    unit_amount = int(float(str(rate).replace('$', '').replace(',', '')) * 100)
                except (ValueError, TypeError):
                    pass
            if data.get('name') or data.get('service_name'):
                product_name = data.get('name') or data.get('service_name') or product_name
    except (json.JSONDecodeError, AttributeError):
        pass

    attribution = {
        'simulacrum_simulation_id': simulation_id,
        'simulacrum_layer_number': str(layer_number),
        'simulacrum_action_type': action_type,
        'simulacrum_action_id': action_id,
    }

    try:
        product = _stripe.Product.create(
            name=product_name,
            metadata=attribution,
            stripe_account=stripe_account_id,
        )
        price = _stripe.Price.create(
            product=product['id'],
            unit_amount=unit_amount,
            currency='usd',
            stripe_account=stripe_account_id,
        )
        link = _stripe.PaymentLink.create(
            line_items=[{'price': price['id'], 'quantity': 1}],
            metadata=attribution,
            stripe_account=stripe_account_id,
        )
        logger.info('Stripe payment link created for %s: %s', action_type, link['url'])
        return {'payment_link_url': link['url'], 'product_id': product['id']}
    except Exception as exc:
        logger.error('Stripe deploy failed for %s: %s', action_type, exc)
        raise


def _deploy_kajabi(rec, *, action_type, **_kw) -> dict:
    """
    Kajabi deployment — escalated until kajabi_service is implemented.
    FR-WIRE-07.
    """
    logger.info('Kajabi deploy: escalating %s — service not yet implemented', action_type)
    raise NotImplementedError('Kajabi service not yet implemented — escalating')


def _deploy_linkedin(rec, *, action_type, **_kw) -> dict:
    """
    LinkedIn deployment — escalated until attorney review is cleared and
    linkedin_service is implemented.  FR-WIRE-08.
    """
    logger.info('LinkedIn deploy: escalating %s — attorney review required', action_type)
    raise NotImplementedError('LinkedIn requires attorney review — escalating')


# Map provider key → deployer function
_DEPLOYERS = {
    APOLLO:     _deploy_apollo,
    CALCOM:     _deploy_calcom,
    CONVERTKIT: _deploy_convertkit,
    PANDADOC:   _deploy_pandadoc,
    STRIPE:     _deploy_stripe,
    KAJABI:     _deploy_kajabi,
    LINKEDIN:   _deploy_linkedin,
}


# ---------------------------------------------------------------------------
# Main entry point  (FR-WIRE-01, FR-WIRE-10)
# ---------------------------------------------------------------------------

def deploy_to_integration(
    user_id: str,
    simulation_id: str,
    action_id: str,
    action_type: str,
    artifact: str,
    layer_number: int,
    artifact_version_id: str = None,
) -> dict:
    """
    Deploy an artifact to every integration in its deployment chain.

    Each step runs independently inside its own try/except — if Apollo fails
    but Stripe succeeds, Stripe objects are live and Apollo escalates.
    Returns {'deployed': [...], 'escalated': [...], 'skipped': [...]}.
    """
    chain = AGENT_INTEGRATION_MAP.get(action_type, [])
    if not chain:
        return {'deployed': [], 'escalated': [], 'skipped': [], 'no_chain': True}

    active = _load_integrations(user_id)
    logger.info('wire_service: deploying %s → %s (active: %s)',
                action_type, chain, list(active.keys()))

    deployed: list[str] = []
    escalated: list[str] = []
    skipped: list[str] = []

    ctx: dict[str, Any] = dict(
        user_id=user_id,
        simulation_id=simulation_id,
        action_id=action_id,
        action_type=action_type,
        artifact=artifact,
        layer_number=layer_number,
        artifact_version_id=artifact_version_id,
    )

    for provider in chain:
        rec = active.get(provider)
        if rec is None:
            # Integration not connected — escalate with a GCC action item
            _escalate(user_id, simulation_id, action_id, action_type, provider,
                      reason='Integration not connected')
            escalated.append(provider)
            continue

        deployer = _DEPLOYERS.get(provider)
        if not deployer:
            skipped.append(provider)
            continue

        try:
            deployer(rec=rec, **ctx)
            deployed.append(provider)
        except NotImplementedError as exc:
            # Known stub — escalate without noise
            _escalate(user_id, simulation_id, action_id, action_type, provider,
                      reason=str(exc))
            escalated.append(provider)
        except Exception as exc:
            logger.error('wire_service: %s/%s deploy failed: %s', action_type, provider, exc, exc_info=True)
            _escalate(user_id, simulation_id, action_id, action_type, provider,
                      reason=str(exc)[:300])
            escalated.append(provider)

    logger.info('wire_service: %s — deployed=%s escalated=%s skipped=%s',
                action_type, deployed, escalated, skipped)
    return {'deployed': deployed, 'escalated': escalated, 'skipped': skipped}


# ---------------------------------------------------------------------------
# Escalation helper
# ---------------------------------------------------------------------------

def _escalate(user_id: str, simulation_id: str, action_id: str,
              action_type: str, provider: str, reason: str) -> None:
    """Create a tier-3 GCC action item for a failed/missing integration step."""
    try:
        from app.services.notification_service import send_notification
        label = action_type.replace('_', ' ').title()
        prov_label = provider.replace('_', ' ').title()
        send_notification(
            user_id=user_id,
            notification_type='integration_deploy_failed',
            title=f'{label} → {prov_label} needs attention',
            body=(f'The orchestrator could not deploy your {label} artifact '
                  f'to {prov_label}. Reason: {reason}'),
            cta_url=f'/simulations/{simulation_id}/layer6',
            cta_label='Review in GCC →',
            simulation_id=simulation_id,
            priority='normal',
        )
    except Exception as exc:
        logger.warning('wire_service escalation notification failed: %s', exc)
