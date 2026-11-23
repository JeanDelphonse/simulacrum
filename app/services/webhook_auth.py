"""Authentication for inbound webhooks (security review 2026-11).

Per-user integration webhooks (Cal.com, Kajabi, ConvertKit, Apollo) carry a
key in the URL: an HMAC of the provider and user id under SECRET_KEY, so it
can't be guessed from the user id and needs no storage. URLs registered
before this change have no key and are refused; reconnecting the integration
(or re-running the campaign) registers the keyed URL.

SendGrid's event webhook is verified with its signed-webhook public key
(SENDGRID_WEBHOOK_PUBLIC_KEY, ECDSA) when set, else a shared token in the URL
(SENDGRID_WEBHOOK_TOKEN, ?k=...). Inbound Parse uses SENDGRID_INBOUND_TOKEN
the same way. With nothing configured these webhooks refuse every request
(fail closed) and log a warning.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import time

logger = logging.getLogger(__name__)

USER_PROVIDERS = ('cal', 'kajabi', 'convertkit', 'apollo')
SIGNATURE_HEADER = 'X-Twilio-Email-Event-Webhook-Signature'
TIMESTAMP_HEADER = 'X-Twilio-Email-Event-Webhook-Timestamp'
MAX_SKEW_SECONDS = 15 * 60


def _secret() -> bytes:
    from flask import current_app
    return (current_app.config.get('SECRET_KEY') or '').encode('utf-8')


def user_webhook_key(provider: str, user_id: str) -> str:
    msg = ('webhook:%s:%s' % (provider, user_id)).encode('utf-8')
    return hmac.new(_secret(), msg, hashlib.sha256).hexdigest()[:40]


def user_webhook_url(provider: str, user_id: str) -> str:
    from flask import current_app
    base = (current_app.config.get('BASE_URL') or '').rstrip('/')
    return '%s/webhooks/%s/%s?k=%s' % (base, provider, user_id, user_webhook_key(provider, user_id))


def _given_key(request) -> str:
    return (request.args.get('k') or request.headers.get('X-SimiSI-Webhook-Key') or '').strip()


def user_webhook_ok(provider: str, user_id: str, request) -> bool:
    given = _given_key(request)
    if not given or not user_id:
        logger.warning('%s webhook for %s refused: missing or wrong key', provider, user_id)
        return False
    ok = hmac.compare_digest(given, user_webhook_key(provider, user_id))
    if not ok:
        logger.warning('%s webhook for %s refused: wrong key', provider, user_id)
    return ok


def _token_ok(config_key: str, request) -> bool:
    from flask import current_app
    token = current_app.config.get(config_key) or ''
    given = _given_key(request)
    return bool(token) and bool(given) and hmac.compare_digest(given, token)


def _sendgrid_signature_ok(public_key_b64: str, request) -> bool:
    sig = request.headers.get(SIGNATURE_HEADER) or ''
    ts = request.headers.get(TIMESTAMP_HEADER) or ''
    if not sig or not ts:
        return False
    try:
        if abs(time.time() - int(ts)) > MAX_SKEW_SECONDS:
            return False
    except ValueError:
        return False
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        key = serialization.load_der_public_key(base64.b64decode(public_key_b64))
        key.verify(base64.b64decode(sig), ts.encode('utf-8') + request.get_data(), ec.ECDSA(hashes.SHA256()))
        return True
    except InvalidSignature:
        return False
    except Exception as exc:
        logger.error('SendGrid webhook signature check failed: %s', exc)
        return False


def sendgrid_events_ok(request) -> bool:
    """Signed event webhook (public key) or the URL token; refuse otherwise."""
    from flask import current_app
    pub = current_app.config.get('SENDGRID_WEBHOOK_PUBLIC_KEY') or ''
    if pub:
        if _sendgrid_signature_ok(pub, request):
            return True
        # A configured token still works while switching to signing.
        if _token_ok('SENDGRID_WEBHOOK_TOKEN', request):
            return True
        logger.warning('SendGrid event webhook refused: bad signature')
        return False
    if current_app.config.get('SENDGRID_WEBHOOK_TOKEN'):
        if _token_ok('SENDGRID_WEBHOOK_TOKEN', request):
            return True
        logger.warning('SendGrid event webhook refused: wrong token')
        return False
    logger.warning('SendGrid event webhook refused: set SENDGRID_WEBHOOK_PUBLIC_KEY or SENDGRID_WEBHOOK_TOKEN')
    return False


def sendgrid_inbound_ok(request) -> bool:
    from flask import current_app
    if not current_app.config.get('SENDGRID_INBOUND_TOKEN'):
        logger.warning('SendGrid inbound parse refused: set SENDGRID_INBOUND_TOKEN')
        return False
    if _token_ok('SENDGRID_INBOUND_TOKEN', request):
        return True
    logger.warning('SendGrid inbound parse refused: wrong token')
    return False
