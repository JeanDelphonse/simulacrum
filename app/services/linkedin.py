import os
import json
import logging
from flask import current_app

logger = logging.getLogger(__name__)

LINKEDIN_AUTH_URL = 'https://www.linkedin.com/oauth/v2/authorization'
LINKEDIN_TOKEN_URL = 'https://www.linkedin.com/oauth/v2/accessToken'
LINKEDIN_USERINFO_URL = 'https://api.linkedin.com/v2/userinfo'
LINKEDIN_SCOPE = 'openid profile email w_member_social'
LINKEDIN_UGC_POSTS_URL = 'https://api.linkedin.com/v2/ugcPosts'


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


def get_user_info(access_token: str) -> dict:
    """Return the raw OpenID Connect userinfo dict (includes sub, name, email)."""
    import requests
    resp = requests.get(LINKEDIN_USERINFO_URL, headers={'Authorization': f'Bearer {access_token}'})
    resp.raise_for_status()
    return resp.json()


def post_ugc(access_token: str, author_sub: str, text: str) -> dict:
    """Publish a text post to the member's LinkedIn feed.

    author_sub  — the 'sub' field from the OpenID userinfo response
                  (used as urn:li:person:{sub})
    text        — plain text of the post (max ~3000 chars for best results)
    Returns the response JSON from LinkedIn (contains 'id' of the new post).
    """
    import requests
    author_urn = f'urn:li:person:{author_sub}'
    payload = {
        'author': author_urn,
        'lifecycleState': 'PUBLISHED',
        'specificContent': {
            'com.linkedin.ugc.ShareContent': {
                'shareCommentary': {'text': text},
                'shareMediaCategory': 'NONE',
            }
        },
        'visibility': {
            'com.linkedin.ugc.MemberNetworkVisibility': 'PUBLIC'
        },
    }
    headers = {
        'Authorization': f'Bearer {access_token}',
        'Content-Type': 'application/json',
        'X-Restli-Protocol-Version': '2.0.0',
    }
    resp = requests.post(LINKEDIN_UGC_POSTS_URL, json=payload, headers=headers)
    resp.raise_for_status()
    return resp.json() if resp.text else {'id': resp.headers.get('x-restli-id', '')}


def crawl_profile(access_token: str) -> str:
    """Fetch LinkedIn profile via OpenID Connect userinfo endpoint."""
    profile = get_user_info(access_token)

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
