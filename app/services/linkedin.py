import os
import json
import logging
from flask import current_app

logger = logging.getLogger(__name__)

LINKEDIN_AUTH_URL = 'https://www.linkedin.com/oauth/v2/authorization'
LINKEDIN_TOKEN_URL = 'https://www.linkedin.com/oauth/v2/accessToken'
LINKEDIN_USERINFO_URL = 'https://api.linkedin.com/v2/userinfo'
LINKEDIN_SCOPE = 'openid profile email'


def get_auth_url(state: str) -> str:
    """Build the LinkedIn OAuth authorization URL."""
    params = {
        'response_type': 'code',
        'client_id': current_app.config['LINKEDIN_CLIENT_ID'],
        'redirect_uri': current_app.config['LINKEDIN_REDIRECT_URI'],
        'state': state,
        'scope': LINKEDIN_SCOPE,
    }
    from urllib.parse import urlencode
    return f"{LINKEDIN_AUTH_URL}?{urlencode(params)}"


def exchange_code_for_token(code: str) -> dict:
    """Exchange OAuth code for access token."""
    import requests  # lazy — avoid slow import at startup
    resp = requests.post(LINKEDIN_TOKEN_URL, data={
        'grant_type': 'authorization_code',
        'code': code,
        'redirect_uri': current_app.config['LINKEDIN_REDIRECT_URI'],
        'client_id': current_app.config['LINKEDIN_CLIENT_ID'],
        'client_secret': current_app.config['LINKEDIN_CLIENT_SECRET'],
    })
    resp.raise_for_status()
    return resp.json()


def crawl_profile(access_token: str) -> str:
    """Fetch LinkedIn profile via OpenID Connect userinfo endpoint."""
    import requests  # lazy — avoid slow import at startup
    headers = {'Authorization': f'Bearer {access_token}'}
    resp = requests.get(LINKEDIN_USERINFO_URL, headers=headers)
    resp.raise_for_status()
    profile = resp.json()

    given  = profile.get('given_name', '')
    family = profile.get('family_name', '')
    name   = profile.get('name') or f'{given} {family}'.strip()
    email  = profile.get('email', '')

    text_parts = [f'Name: {name}']
    if email:
        text_parts.append(f'Email: {email}')

    # Work history is not available via standard OAuth; user can add it manually
    text_parts.append('\n[Work history not imported — please paste your resume text below to enrich this source.]')

    return '\n'.join(text_parts)


def encrypt_token(token: str) -> str:
    """Encrypt a LinkedIn access token for storage."""
    from cryptography.fernet import Fernet  # lazy — avoid slow import at startup
    key = current_app.config.get('ENCRYPTION_KEY')
    if not key:
        return token  # dev fallback — no encryption
    fernet = Fernet(key.encode() if isinstance(key, str) else key)
    return fernet.encrypt(token.encode()).decode()


def decrypt_token(encrypted_token: str) -> str:
    """Decrypt a stored LinkedIn access token."""
    from cryptography.fernet import Fernet  # lazy — avoid slow import at startup
    key = current_app.config.get('ENCRYPTION_KEY')
    if not key:
        return encrypted_token  # dev fallback
    fernet = Fernet(key.encode() if isinstance(key, str) else key)
    return fernet.decrypt(encrypted_token.encode()).decode()
