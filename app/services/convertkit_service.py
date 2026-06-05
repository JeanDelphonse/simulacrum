"""
ConvertKit (Kit) integration service — SIM-PRD-WIRE-001 FR-WIRE-04.

Handles: subscriber creation, tagging, sequence enrolment, form subscription.
Auth: API secret stored encrypted in UserIntegration.access_token_enc.
"""
import logging
import requests

logger = logging.getLogger(__name__)

_BASE = 'https://api.convertkit.com/v3'
_TIMEOUT = 15


class ConvertKitClient:
    def __init__(self, api_secret: str):
        self.secret = api_secret

    def _get(self, path: str, params: dict = None) -> dict:
        params = (params or {}) | {'api_secret': self.secret}
        r = requests.get(f'{_BASE}{path}', params=params, timeout=_TIMEOUT)
        r.raise_for_status()
        return r.json()

    def _post(self, path: str, body: dict) -> dict:
        body = body | {'api_secret': self.secret}
        r = requests.post(f'{_BASE}{path}', json=body, timeout=_TIMEOUT)
        r.raise_for_status()
        return r.json()

    # ── Subscribers ───────────────────────────────────────────────────────────

    def add_subscriber(self, email: str, first_name: str = '',
                       fields: dict = None, tags: list[str] = None) -> dict:
        """Create or update a subscriber. Returns subscriber dict."""
        body: dict = {'email': email}
        if first_name:
            body['first_name'] = first_name
        if fields:
            body['fields'] = fields
        data = self._post('/subscribers', body)
        subscriber = data.get('subscriber', {})

        # Apply tags if provided
        if tags and subscriber.get('id'):
            for tag_name in tags:
                try:
                    self._apply_tag(email, tag_name)
                except Exception as exc:
                    logger.warning('ConvertKit tag "%s" failed: %s', tag_name, exc)

        return subscriber

    def _apply_tag(self, email: str, tag_name: str) -> dict:
        """Find or create a tag by name, then subscribe the email to it."""
        tags = self._get('/tags').get('tags', [])
        tag = next((t for t in tags if t['name'] == tag_name), None)
        if not tag:
            tag = self._post('/tags', {'tag': {'name': tag_name}})
        tag_id = tag['id']
        return self._post(f'/tags/{tag_id}/subscribe', {'email': email})

    # ── Sequences ─────────────────────────────────────────────────────────────

    def add_to_sequence(self, sequence_id: int, email: str,
                        first_name: str = '') -> dict:
        """Enrol a subscriber in a sequence by sequence ID."""
        body: dict = {'email': email}
        if first_name:
            body['first_name'] = first_name
        return self._post(f'/sequences/{sequence_id}/subscribe', body)

    def list_sequences(self) -> list:
        return self._get('/sequences').get('courses', [])

    # ── Forms ─────────────────────────────────────────────────────────────────

    def list_forms(self) -> list:
        return self._get('/forms').get('forms', [])

    def add_to_form(self, form_id: int, email: str, first_name: str = '') -> dict:
        body: dict = {'email': email}
        if first_name:
            body['first_name'] = first_name
        return self._post(f'/forms/{form_id}/subscribe', body)


# ── High-level deployers ──────────────────────────────────────────────────────

def deploy_lead_to_convertkit(user_id: str, email: str, first_name: str,
                               tag: str, simulation_id: str = None) -> dict:
    """
    Add a lead (e.g. bio chat visitor) as a ConvertKit subscriber with the
    given tag. Called by the bio chat lead gate and by wire_service deployers.
    """
    from app.models.integration import UserIntegration
    from app.services.token_crypto import decrypt_token

    rec = UserIntegration.query.filter_by(
        user_id=user_id, provider='convertkit'
    ).first()
    if not rec or not rec.is_connected:
        return {'skipped': True, 'reason': 'convertkit_not_connected'}

    secret = decrypt_token(rec.access_token_enc)
    client = ConvertKitClient(secret)
    subscriber = client.add_subscriber(email, first_name=first_name, tags=[tag])
    logger.info('ConvertKit: added subscriber %s with tag "%s"', email, tag)
    return {'subscriber_id': subscriber.get('id'), 'tag': tag}
