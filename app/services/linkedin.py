import os
import json
import logging
from flask import current_app

logger = logging.getLogger(__name__)

LINKEDIN_AUTH_URL = 'https://www.linkedin.com/oauth/v2/authorization'
LINKEDIN_TOKEN_URL = 'https://www.linkedin.com/oauth/v2/accessToken'
LINKEDIN_PROFILE_URL = 'https://api.linkedin.com/v2/me'
LINKEDIN_SCOPE = 'r_liteprofile r_emailaddress'


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
    """Crawl LinkedIn profile and return normalized text representation."""
    import requests  # lazy — avoid slow import at startup
    headers = {'Authorization': f'Bearer {access_token}'}
    profile = requests.get(LINKEDIN_PROFILE_URL, headers=headers).json()

    # Build text representation from available profile data
    name = f"{profile.get('localizedFirstName', '')} {profile.get('localizedLastName', '')}".strip()
    headline = profile.get('localizedHeadline', '')

    text_parts = [f"Name: {name}", f"Headline: {headline}"]

    # LinkedIn API v2 for positions
    positions_url = 'https://api.linkedin.com/v2/positions?q=members&projection=(elements*(title,companyName,startMonthYear,endMonthYear,description))'
    try:
        positions_resp = requests.get(positions_url, headers=headers)
        if positions_resp.ok:
            data = positions_resp.json()
            for pos in data.get('elements', []):
                start = pos.get('startMonthYear', {})
                end = pos.get('endMonthYear', {})
                start_str = f"{start.get('month', '')}/{start.get('year', '')}" if start else ''
                end_str = f"{end.get('month', '')}/{end.get('year', '')}" if end else 'Present'
                text_parts.append(
                    f"\n{pos.get('title', '')} at {pos.get('companyName', '')} ({start_str} - {end_str})"
                )
                if pos.get('description'):
                    text_parts.append(pos['description'])
    except Exception as e:
        logger.warning(f'Could not fetch LinkedIn positions: {e}')

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
