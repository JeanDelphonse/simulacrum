import logging
from urllib.parse import urlencode
from flask import current_app

logger = logging.getLogger(__name__)

APOLLO_BASE_URL      = 'https://api.apollo.io/v1'
APOLLO_AUTH_URL      = 'https://api.apollo.io/oauth2/authorize'
APOLLO_TOKEN_URL     = 'https://api.apollo.io/oauth2/token'
APOLLO_SCOPES        = 'sequences:write contacts:write emailer_campaigns:write webhooks:write'


# ── OAuth helpers ─────────────────────────────────────────────────────────────

def get_auth_url(state: str) -> str:
    params = {
        'response_type': 'code',
        'client_id': current_app.config['APOLLO_CLIENT_ID'],
        'redirect_uri': current_app.config['APOLLO_REDIRECT_URI'],
        'scope': APOLLO_SCOPES,
        'state': state,
    }
    return f"{APOLLO_AUTH_URL}?{urlencode(params)}"


def exchange_code_for_token(code: str) -> dict:
    import requests
    resp = requests.post(APOLLO_TOKEN_URL, json={
        'grant_type': 'authorization_code',
        'code': code,
        'redirect_uri': current_app.config['APOLLO_REDIRECT_URI'],
        'client_id': current_app.config['APOLLO_CLIENT_ID'],
        'client_secret': current_app.config['APOLLO_CLIENT_SECRET'],
    })
    resp.raise_for_status()
    return resp.json()


def refresh_access_token(refresh_token: str) -> dict:
    import requests
    resp = requests.post(APOLLO_TOKEN_URL, json={
        'grant_type': 'refresh_token',
        'refresh_token': refresh_token,
        'client_id': current_app.config['APOLLO_CLIENT_ID'],
        'client_secret': current_app.config['APOLLO_CLIENT_SECRET'],
    })
    resp.raise_for_status()
    return resp.json()


# ── API client ────────────────────────────────────────────────────────────────

class ApolloClient:
    def __init__(self, access_token: str):
        self._token = access_token

    def _headers(self):
        return {
            'Authorization': f'Bearer {self._token}',
            'Content-Type': 'application/json',
        }

    def _get(self, path, params=None):
        import requests
        resp = requests.get(f'{APOLLO_BASE_URL}{path}', headers=self._headers(), params=params)
        resp.raise_for_status()
        return resp.json()

    def _post(self, path, body=None):
        import requests
        resp = requests.post(f'{APOLLO_BASE_URL}{path}', headers=self._headers(), json=body or {})
        resp.raise_for_status()
        return resp.json()

    # ── Contacts ──────────────────────────────────────────────────────────────

    def upsert_contact(self, email, first_name, last_name, title=None,
                       company=None, custom_fields=None):
        payload = {
            'email': email,
            'first_name': first_name,
            'last_name': last_name,
        }
        if title:
            payload['title'] = title
        if company:
            payload['organization_name'] = company
        if custom_fields:
            payload['custom_fields'] = custom_fields
        data = self._post('/contacts/upsert', payload)
        return data.get('contact', {})

    # ── Sequences ─────────────────────────────────────────────────────────────

    def create_sequence(self, name: str, steps: list, daily_limit: int = 30) -> dict:
        payload = {
            'name': name,
            'active': True,
            'daily_limit': daily_limit,
            'steps': steps,
        }
        data = self._post('/emailer_campaigns', payload)
        return data.get('emailer_campaign', {})

    def add_contacts_to_sequence(self, sequence_id: str, contact_ids: list):
        payload = {
            'emailer_campaign_id': sequence_id,
            'contact_ids': contact_ids,
        }
        return self._post('/emailer_campaigns/add_contact_ids', payload)

    # ── Webhooks ──────────────────────────────────────────────────────────────

    def register_webhook(self, url: str, events: list, sequence_id: str = None):
        payload = {
            'notification_endpoint_url': url,
            'event_types': events,
        }
        if sequence_id:
            payload['emailer_campaign_id'] = sequence_id
        return self._post('/webhooks', payload)

    # ── Sequence pause/cancel ─────────────────────────────────────────────────

    def pause_sequence(self, sequence_id: str):
        return self._post(f'/emailer_campaigns/{sequence_id}/pause')

    def cancel_sequence(self, sequence_id: str):
        return self._post(f'/emailer_campaigns/{sequence_id}/archive')
