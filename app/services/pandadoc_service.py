"""
PandaDoc document signing service (SIM-PRD-SIGN-001).
Uses per-user API key model (workspace API key stored in user_integrations).
"""
import logging
import requests
from datetime import datetime
from flask import current_app

logger = logging.getLogger(__name__)

PANDADOC_API_BASE = 'https://api.pandadoc.com/public/v1'

SIGNING_ACTION_TYPES = {
    'consulting_proposal', 'coaching_agreement', 'service_agreement',
    'freelance_contract', 'nda', 'mou', 'retainer_agreement',
}


# ── PandaDoc API client ───────────────────────────────────────────────────────

class PandaDocClient:
    def __init__(self, token: str, auth_type: str = 'oauth'):
        self._token = token
        self._auth_type = auth_type

    def _headers(self):
        prefix = 'Bearer' if self._auth_type == 'oauth' else 'API-Key'
        return {
            'Authorization': f'{prefix} {self._token}',
            'Content-Type': 'application/json',
        }

    def _get(self, path, params=None):
        resp = requests.get(f'{PANDADOC_API_BASE}{path}', headers=self._headers(), params=params)
        resp.raise_for_status()
        return resp.json()

    def _post(self, path, body=None):
        resp = requests.post(f'{PANDADOC_API_BASE}{path}', headers=self._headers(), json=body or {})
        resp.raise_for_status()
        return resp.json()

    def _patch(self, path, body=None):
        resp = requests.patch(f'{PANDADOC_API_BASE}{path}', headers=self._headers(), json=body or {})
        resp.raise_for_status()
        return resp.json()

    def create_document_from_content(self, title: str, content_html: str,
                                     recipients: list, metadata: dict = None) -> dict:
        """
        Create a PandaDoc document from raw HTML content, add recipients, and send.
        recipients: [{'email': '...', 'first_name': '...', 'last_name': '...', 'role': 'signer'}]
        """
        payload = {
            'name': title,
            'content_placeholders': [],
            'recipients': [
                {
                    'email': r['email'],
                    'first_name': r.get('first_name', ''),
                    'last_name': r.get('last_name', ''),
                    'role': r.get('role', 'signer'),
                }
                for r in recipients
            ],
            'content': [
                {
                    'type': 'text',
                    'data': content_html,
                }
            ],
            'parse_form_fields': False,
        }
        if metadata:
            payload['metadata'] = metadata
        return self._post('/documents', payload)

    def send_document(self, document_id: str, subject: str = None,
                      message: str = None, silent: bool = False) -> dict:
        body = {'silent': silent}
        if subject:
            body['subject'] = subject
        if message:
            body['message'] = message
        return self._post(f'/documents/{document_id}/send', body)

    def get_document(self, document_id: str) -> dict:
        return self._get(f'/documents/{document_id}')

    def create_document_link(self, document_id: str, recipient_email: str,
                             lifetime: int = 3600) -> str:
        """Return a direct signing link for a specific recipient."""
        data = self._post(f'/documents/{document_id}/session', {
            'recipient': recipient_email,
            'lifetime': lifetime,
        })
        return data.get('id', '')

    def void_document(self, document_id: str) -> dict:
        return self._patch(f'/documents/{document_id}/status', {'status': 'document.voided'})


# ── Document deployment ───────────────────────────────────────────────────────

def deploy_document_for_signing(user_id: str, simulation_id: str,
                                action_id: str, action_type: str,
                                artifact_version_id: str, layer_number: int,
                                recipient_email: str, recipient_name: str,
                                document_title: str, content_html: str) -> dict:
    """
    Send a document for signing via PandaDoc.
    Returns {'signing_document_id': ..., 'pandadoc_document_id': ..., 'status': 'sent'}
    """
    from app.models.integration import UserIntegration
    from app.models.signing import SigningDocument
    from app.extensions import db

    integration = UserIntegration.query.filter_by(
        user_id=user_id, provider='pandadoc'
    ).first()
    if not integration or not integration.is_connected:
        raise PandaDocAuthRequired('pandadoc_not_configured')

    token = integration.decrypt_access_token()
    auth_type = 'oauth' if integration.refresh_token_enc else 'api_key'
    client = PandaDocClient(token, auth_type=auth_type)

    name_parts = (recipient_name or '').split(' ', 1)
    first_name = name_parts[0] if name_parts else ''
    last_name = name_parts[1] if len(name_parts) > 1 else ''

    base_url = current_app.config.get('BASE_URL', 'http://localhost:5000')

    metadata = {
        'simulacrum_user_id': user_id,
        'simulacrum_simulation_id': simulation_id,
        'simulacrum_action_id': action_id or '',
        'simulacrum_action_type': action_type,
        'simulacrum_layer': str(layer_number),
    }

    doc = client.create_document_from_content(
        title=document_title or 'Agreement',
        content_html=content_html,
        recipients=[{
            'email': recipient_email,
            'first_name': first_name,
            'last_name': last_name,
            'role': 'signer',
        }],
        metadata=metadata,
    )
    pandadoc_doc_id = doc.get('id') or doc.get('uuid')
    if not pandadoc_doc_id:
        raise PandaDocError('PandaDoc did not return a document ID')

    # Send the document to the recipient
    client.send_document(
        pandadoc_doc_id,
        subject=f'Please sign: {document_title or "Agreement"}',
        message='Your document is ready to be reviewed and signed.',
    )

    record = SigningDocument(
        user_id=user_id,
        simulation_id=simulation_id,
        action_id=action_id,
        action_type=action_type,
        artifact_version_id=artifact_version_id,
        layer_number=layer_number,
        pandadoc_document_id=pandadoc_doc_id,
        recipient_email=recipient_email,
        recipient_name=recipient_name,
        document_title=document_title,
        status='sent',
    )
    db.session.add(record)
    db.session.commit()

    logger.info('PandaDoc document sent: user=%s doc_id=%s recipient=%s',
                user_id, pandadoc_doc_id, recipient_email)
    return {
        'signing_document_id': record.id,
        'pandadoc_document_id': pandadoc_doc_id,
        'status': 'sent',
    }


# ── Webhook event handler ─────────────────────────────────────────────────────

def handle_pandadoc_event(payload: dict) -> None:
    """
    Process a PandaDoc webhook event and update signing_documents + CRM.
    Expected event types: document_state_changed
    """
    from app.models.signing import SigningDocument
    from app.extensions import db

    event = payload.get('event')
    data = payload.get('data', {})
    pandadoc_doc_id = data.get('id') or data.get('uuid')
    if not pandadoc_doc_id:
        return

    record = SigningDocument.query.filter_by(
        pandadoc_document_id=pandadoc_doc_id
    ).first()
    if not record:
        logger.warning('PandaDoc webhook: no signing_document found for %s', pandadoc_doc_id)
        return

    status_map = {
        'document_state_changed': _map_pandadoc_status(data.get('status', '')),
    }
    new_status = status_map.get(event)

    sim_id      = record.simulation_id
    action_type = record.action_type or 'consulting_proposal'

    from app.services.bayesian_service import dispatch_signal

    if new_status == 'viewed' and record.status == 'sent':
        record.status = 'viewed'
        record.viewed_at = datetime.utcnow()
        dispatch_signal(sim_id, f'view_rate:{action_type}', 1.0, 0.3, '+')

    elif new_status == 'signed':
        record.status = 'signed'
        record.signed_at = datetime.utcnow()
        dispatch_signal(sim_id, f'proposal_win_rate:{action_type}', 1.0, 0.9, '+')
        if record.viewed_at:
            days_to_sign = (record.signed_at - record.viewed_at).days
            dispatch_signal(
                sim_id, 'avg_time_to_sign_days',
                max(0.0, 1.0 - days_to_sign / 30.0), 0.3, '+'
            )
        _on_document_signed(record)

    elif new_status == 'declined':
        record.status = 'declined'
        record.declined_at = datetime.utcnow()
        decline_reason = data.get('decline_message') or data.get('reason') or ''
        if hasattr(record, 'declined_reason'):
            record.declined_reason = decline_reason[:500] if decline_reason else None
        dispatch_signal(sim_id, f'decline_rate:{action_type}', 1.0, 0.6, '-')
        # Create contact_activities record for decline (FR-PANDADOC-03)
        try:
            _log_pandadoc_contact_activity(record, 'proposal_declined', decline_reason)
        except Exception:
            pass

    elif new_status == 'expired':
        record.status = 'expired'
        dispatch_signal(sim_id, 'void_rate', 1.0, 0.2, '-')

    db.session.commit()
    logger.info('PandaDoc event %s processed for doc %s → status=%s',
                event, pandadoc_doc_id, record.status)


def _map_pandadoc_status(pd_status: str) -> str:
    mapping = {
        'document.viewed': 'viewed',
        'document.completed': 'signed',
        'document.declined': 'declined',
        'document.expired': 'expired',
        'document.voided': 'declined',
    }
    return mapping.get(pd_status, '')


def _on_document_signed(record) -> None:
    """Advance the CRM contact from active → client when a document is signed."""
    from app.models.contact import Contact
    from app.extensions import db

    contact = Contact.query.filter_by(
        user_id=record.user_id,
        email=record.recipient_email,
    ).first()
    if not contact:
        return

    try:
        contact.advance_stage('client', note=f'Document signed via PandaDoc ({record.document_title})')
        db.session.commit()
        logger.info('CRM contact %s advanced to client after document signed', contact.id)
    except Exception as exc:
        logger.warning('Could not advance CRM stage for contact %s: %s', contact.id, exc)


def _log_pandadoc_contact_activity(record, activity_type: str, notes: str = '') -> None:
    """Log a contact_activities record from a PandaDoc event."""
    from app.models.contact import Contact, ContactActivity
    from app.extensions import db
    from utils.id_gen import generate_id

    contact = Contact.query.filter_by(
        user_id=record.user_id,
        email=record.recipient_email,
    ).first()
    if not contact:
        return
    activity = ContactActivity(
        id=generate_id(),
        contact_id=contact.id,
        simulation_id=record.simulation_id,
        action_id=record.action_id,
        activity_type=activity_type,
        notes=notes[:500] if notes else None,
        created_by='webhook',
    )
    db.session.add(activity)


def check_stalled_proposals() -> int:
    """
    Detect proposals viewed 7+ days ago without signing (FR-PANDADOC-06).
    Returns count of newly stalled documents. Call from a background task.
    """
    from app.models.signing import SigningDocument
    from app.models.layer6 import ActionItem
    from app.extensions import db
    from utils.id_gen import generate_id
    from datetime import timedelta

    cutoff = datetime.utcnow() - timedelta(days=7)
    stalled = SigningDocument.query.filter(
        SigningDocument.status == 'viewed',
        SigningDocument.viewed_at <= cutoff,
    ).all()

    count = 0
    for doc in stalled:
        # Check if we already created a stall action item for this document
        existing = ActionItem.query.filter_by(
            simulation_id=doc.simulation_id,
            item_type='proposal_stalled',
            source_action_id=doc.action_id,
        ).first()
        if existing:
            continue

        try:
            from app.models.user import User
            user = User.query.get(doc.user_id)
            if not user:
                continue

            contact_name = doc.recipient_name or doc.recipient_email or 'Your contact'
            days_stalled = (datetime.utcnow() - doc.viewed_at).days

            item = ActionItem(
                id=generate_id(),
                simulation_id=doc.simulation_id,
                user_id=doc.user_id,
                item_type='proposal_stalled',
                urgency_tier=4,
                title=f'{contact_name} viewed your proposal {days_stalled} days ago but has not signed',
                description=(
                    f'Consider following up with {contact_name} about '
                    f'the "{doc.document_title or "proposal"}". '
                    f'They opened it on {doc.viewed_at.strftime("%b %d")} but have not signed.'
                ),
                action_label='View document',
                action_url=f'/contacts?search={doc.recipient_email}',
                source_action_id=doc.action_id,
                is_dismissable=True,
            )
            db.session.add(item)

            from app.services.bayesian_service import dispatch_signal
            dispatch_signal(doc.simulation_id, f'stall_rate:{doc.action_type}', 1.0, 0.4, '-')
            count += 1
        except Exception as exc:
            logger.warning('Stall detection failed for doc %s: %s', doc.id, exc)

    if count > 0:
        try:
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            logger.error('Stall detection commit failed: %s', exc)

    return count


class PandaDocAuthRequired(Exception):
    pass


class PandaDocError(Exception):
    pass
