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
    def __init__(self, api_key: str):
        self._api_key = api_key

    def _headers(self):
        return {
            'Authorization': f'API-Key {self._api_key}',
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

    api_key = integration.decrypt_access_token()
    client = PandaDocClient(api_key)

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

    if new_status == 'viewed' and record.status == 'sent':
        record.status = 'viewed'
        record.viewed_at = datetime.utcnow()

    elif new_status == 'signed':
        record.status = 'signed'
        record.signed_at = datetime.utcnow()
        _on_document_signed(record)

    elif new_status == 'declined':
        record.status = 'declined'
        record.declined_at = datetime.utcnow()

    elif new_status == 'expired':
        record.status = 'expired'

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


class PandaDocAuthRequired(Exception):
    pass


class PandaDocError(Exception):
    pass
