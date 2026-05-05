"""
Cal.com OAuth + booking page deployment service (SIM-PRD-CAL-001).
Creates event types and webhooks on the user's connected Cal.com account.
"""
import logging
from urllib.parse import urlencode
from flask import current_app

logger = logging.getLogger(__name__)

CAL_AUTH_URL   = 'https://app.cal.com/oauth/authorize'
CAL_TOKEN_URL  = 'https://app.cal.com/api/auth/oauth/token'
CAL_API_BASE   = 'https://api.cal.com/v1'

BOOKING_ACTION_TYPES = {'booking_page', 'consulting_proposal', 'workshop_curriculum'}


# ── OAuth helpers ─────────────────────────────────────────────────────────────

def get_auth_url(state: str) -> str:
    params = {
        'client_id': current_app.config['CAL_CLIENT_ID'],
        'redirect_uri': f"{current_app.config['BASE_URL']}/api/integrations/cal/callback",
        'response_type': 'code',
        'scope': 'bookings:read event_types:write',
        'state': state,
    }
    return f"{CAL_AUTH_URL}?{urlencode(params)}"


def exchange_code(code: str) -> dict:
    import requests
    resp = requests.post(CAL_TOKEN_URL, json={
        'code': code,
        'client_id': current_app.config['CAL_CLIENT_ID'],
        'client_secret': current_app.config['CAL_CLIENT_SECRET'],
        'grant_type': 'authorization_code',
        'redirect_uri': f"{current_app.config['BASE_URL']}/api/integrations/cal/callback",
    })
    resp.raise_for_status()
    return resp.json()


def refresh_access_token(refresh_token: str) -> dict:
    import requests
    resp = requests.post(CAL_TOKEN_URL, json={
        'grant_type': 'refresh_token',
        'refresh_token': refresh_token,
        'client_id': current_app.config['CAL_CLIENT_ID'],
        'client_secret': current_app.config['CAL_CLIENT_SECRET'],
    })
    resp.raise_for_status()
    return resp.json()


# ── Cal.com API client ────────────────────────────────────────────────────────

class CalClient:
    def __init__(self, access_token: str):
        self._token = access_token

    def _headers(self):
        return {
            'Authorization': f'Bearer {self._token}',
            'Content-Type': 'application/json',
        }

    def _get(self, path, params=None):
        import requests
        resp = requests.get(f'{CAL_API_BASE}{path}', headers=self._headers(), params=params)
        resp.raise_for_status()
        return resp.json()

    def _post(self, path, body=None):
        import requests
        resp = requests.post(f'{CAL_API_BASE}{path}', headers=self._headers(), json=body or {})
        resp.raise_for_status()
        return resp.json()

    def _delete(self, path):
        import requests
        resp = requests.delete(f'{CAL_API_BASE}{path}', headers=self._headers())
        resp.raise_for_status()
        return resp.json()

    def get_me(self) -> dict:
        return self._get('/me')

    def create_event_type(self, title: str, length: int, price: int = 0,
                          currency: str = 'USD', description: str = '',
                          buffer_time: int = 15) -> dict:
        payload = {
            'title': title,
            'length': length,
            'price': price,
            'currency': currency,
            'description': description,
            'beforeEventBuffer': buffer_time,
        }
        data = self._post('/event-types', payload)
        return data.get('event_type', data)

    def create_webhook(self, subscriber_url: str, event_triggers: list,
                       event_type_id: int = None,
                       payload_template: str = None) -> dict:
        payload = {
            'subscriberUrl': subscriber_url,
            'eventTriggers': event_triggers,
            'active': True,
        }
        if event_type_id:
            payload['eventTypeId'] = event_type_id
        if payload_template:
            payload['payloadTemplate'] = payload_template
        return self._post('/webhooks', payload)

    def get_booking_page_url(self, username: str, event_slug: str = None) -> str:
        base = f'https://cal.com/{username}'
        return f'{base}/{event_slug}' if event_slug else base

    def delete_webhook(self, webhook_id: int):
        return self._delete(f'/webhooks/{webhook_id}')


# ── Booking page deployment ───────────────────────────────────────────────────

def deploy_booking_page(user_id: str, simulation_id: str,
                        action_id: str, artifact: dict) -> dict:
    """
    Create Cal.com event types from a booking_page artifact and register webhooks.
    Returns {'booking_page_url': ..., 'event_type_ids': [...]}
    """
    from app.models.integration import UserIntegration

    integration = UserIntegration.query.filter_by(
        user_id=user_id, provider='cal'
    ).first()
    if not integration or not integration.is_connected:
        raise CalAuthRequired('cal_auth_required')

    if integration.is_expired:
        _try_refresh(integration)

    token = integration.decrypt_access_token()
    cal = CalClient(token)

    me = cal.get_me()
    username = me.get('username') or me.get('user', {}).get('username', 'unknown')

    base_url = current_app.config.get('BASE_URL', 'http://localhost:5000')
    webhook_url = f'{base_url}/webhooks/cal/{user_id}'

    session_types = artifact.get('session_types') or []
    if not session_types:
        # Fallback: build a single session type from top-level artifact fields
        session_types = [{
            'name': artifact.get('session_name', 'Strategy Session'),
            'duration_minutes': int(artifact.get('duration_minutes', 60)),
            'price_cents': int(artifact.get('price_cents', 0)),
        }]

    buffer_minutes = int(artifact.get('buffer_minutes', 15))
    description = artifact.get('description', '')

    event_type_ids = []
    first_slug = None

    for session in session_types:
        title    = session.get('name', 'Session')
        length   = int(session.get('duration_minutes', 60))
        price    = int(session.get('price_cents', 0))
        currency = session.get('currency', 'USD')

        event_type = cal.create_event_type(
            title=title,
            length=length,
            price=price,
            currency=currency,
            description=description,
            buffer_time=buffer_minutes,
        )

        et_id   = event_type.get('id')
        et_slug = event_type.get('slug') or title.lower().replace(' ', '-')
        if first_slug is None:
            first_slug = et_slug
        if et_id:
            event_type_ids.append(et_id)

        # Register webhook with simulation_id in payload template
        import json as _json
        payload_tpl = _json.dumps({
            'triggerEvent': '{{triggerEvent}}',
            'payload': '{{payload}}',
            'simulacrum_simulation_id': simulation_id,
            'simulacrum_action_id': action_id,
            'simulacrum_user_id': user_id,
        })
        try:
            cal.create_webhook(
                subscriber_url=webhook_url,
                event_triggers=['BOOKING_CREATED', 'BOOKING_CANCELLED'],
                event_type_id=et_id,
                payload_template=payload_tpl,
            )
        except Exception as exc:
            logger.warning('Cal.com webhook registration failed for event %s: %s', et_id, exc)

    booking_page_url = cal.get_booking_page_url(username, first_slug)

    logger.info('Cal.com booking page deployed: user=%s url=%s', user_id, booking_page_url)
    return {'booking_page_url': booking_page_url, 'event_type_ids': event_type_ids}


def _try_refresh(integration):
    if not integration.refresh_token_enc:
        raise CalAuthRequired('cal_token_expired')
    from app.services.token_crypto import encrypt_token, decrypt_token
    from datetime import datetime, timedelta
    from app.extensions import db
    try:
        refresh_tok = decrypt_token(integration.refresh_token_enc)
        data = refresh_access_token(refresh_tok)
        integration.access_token_enc = encrypt_token(data['access_token'])
        if data.get('refresh_token'):
            integration.refresh_token_enc = encrypt_token(data['refresh_token'])
        if data.get('expires_in'):
            integration.token_expires_at = datetime.utcnow() + timedelta(seconds=data['expires_in'])
        db.session.commit()
    except Exception as exc:
        logger.error('Cal.com token refresh failed: %s', exc)
        raise CalAuthRequired('cal_token_expired')


class CalAuthRequired(Exception):
    pass
