"""
Content Publishing Pipeline (SIM-PRD-PUB-001).
Handles Simulacrum-hosted artifact URLs, sales page deployment, and ConvertKit integration.
"""
import re
import logging
from datetime import datetime
from flask import current_app

logger = logging.getLogger(__name__)

# Action types whose artifacts get a hosted public URL (FR-PUB-01)
PUBLISHABLE_ACTION_TYPES = {
    'seo_content_calendar', 'sales_page', 'waitlist_landing_page',
    'lead_magnet_funnel', 'newsletter_monetization', 'youtube_podcast_strategy',
    'email_newsletter', 'case_study', 'white_paper', 'ebook_outline',
}

# Action types that deploy as full HTML pages at /p/<slug> (FR-PUB-03)
PAGE_ACTION_TYPES = {'sales_page', 'waitlist_landing_page', 'lead_magnet_funnel'}

# ConvertKit action types (FR-PUB-02)
CONVERTKIT_ACTION_TYPES = {'waitlist_landing_page', 'lead_magnet_funnel'}


# ── Artifact public URL (FR-PUB-01) ──────────────────────────────────────────

def publish_artifact_url(artifact_version_id: str) -> str | None:
    """
    Assign a Simulacrum-hosted public URL to an existing ArtifactVersion.
    Returns the public_url string, or None if the version has no content.
    """
    from app.models.artifact import ArtifactVersion
    from app.extensions import db

    av = ArtifactVersion.query.get(artifact_version_id)
    if not av:
        return None
    if not av.content:
        return None

    base_url = current_app.config.get('BASE_URL', 'http://localhost:5000')
    public_url = f'{base_url}/artifacts/{av.id}'
    av.public_url = public_url
    db.session.commit()
    logger.info('Artifact URL published: av_id=%s url=%s', av.id, public_url)
    return public_url


def auto_publish_on_complete(action_id: str) -> str | None:
    """
    Called after an AgentAction completes. If the action type is publishable,
    finds the current ArtifactVersion and assigns a public URL (FR-PUB-01).
    Returns the public_url or None.
    """
    from app.models.artifact import ArtifactVersion
    from app.models.agent_action import AgentAction

    action = AgentAction.query.get(action_id)
    if not action:
        return None
    if action.action_type not in PUBLISHABLE_ACTION_TYPES:
        return None

    av = ArtifactVersion.query.filter_by(
        action_id=action_id, is_current=True
    ).first()
    if not av:
        return None

    return publish_artifact_url(av.id)


# ── Sales page deploy (FR-PUB-03) ────────────────────────────────────────────

def deploy_sales_page(user_id: str, simulation_id: str, action_id: str,
                      action_type: str, artifact_version_id: str,
                      layer_number: int, html_content: str, title: str) -> dict:
    """
    Store a full-page HTML artifact as a PublishedPage and return its /p/<slug> URL.
    Also updates ArtifactVersion.public_url.
    """
    from app.models.published_page import PublishedPage
    from app.models.artifact import ArtifactVersion
    from app.extensions import db

    slug = _generate_slug(user_id, simulation_id, action_type, title)

    existing = PublishedPage.query.filter_by(
        user_id=user_id, simulation_id=simulation_id, action_type=action_type
    ).first()
    if existing:
        existing.slug = slug
        existing.html_content = html_content
        existing.title = title
        existing.artifact_version_id = artifact_version_id
        existing.status = 'live'
        existing.updated_at = datetime.utcnow()
        page = existing
    else:
        page = PublishedPage(
            user_id=user_id,
            simulation_id=simulation_id,
            action_id=action_id,
            action_type=action_type,
            artifact_version_id=artifact_version_id,
            layer_number=layer_number,
            slug=slug,
            title=title,
            html_content=html_content,
            status='live',
        )
        db.session.add(page)

    db.session.flush()

    base_url = current_app.config.get('BASE_URL', 'http://localhost:5000')
    public_url = f'{base_url}/p/{slug}'

    if artifact_version_id:
        av = ArtifactVersion.query.get(artifact_version_id)
        if av:
            av.public_url = public_url

    db.session.commit()
    logger.info('Sales page deployed: user=%s slug=%s url=%s', user_id, slug, public_url)
    return {'slug': slug, 'public_url': public_url}


def _generate_slug(user_id: str, simulation_id: str, action_type: str, title: str) -> str:
    base = title or action_type.replace('_', '-')
    base = re.sub(r'[^a-z0-9\s-]', '', base.lower())
    base = re.sub(r'[\s_]+', '-', base).strip('-')[:50]
    base = base or action_type.replace('_', '-')

    from app.models.published_page import PublishedPage
    candidate = base
    n = 1
    while PublishedPage.query.filter_by(slug=candidate).first():
        candidate = f'{base}-{n}'
        n += 1
    return candidate


# ── ConvertKit integration (FR-PUB-02) ───────────────────────────────────────

class ConvertKitClient:
    API_BASE = 'https://api.convertkit.com/v3'

    def __init__(self, api_key: str, api_secret: str = None):
        self._key = api_key
        self._secret = api_secret or api_key

    def _get(self, path, params=None):
        import requests
        p = {'api_key': self._key, **(params or {})}
        resp = requests.get(f'{self.API_BASE}{path}', params=p, timeout=15)
        resp.raise_for_status()
        return resp.json()

    def _post(self, path, body=None):
        import requests
        payload = {'api_secret': self._secret, **(body or {})}
        resp = requests.post(f'{self.API_BASE}{path}', json=payload, timeout=15)
        resp.raise_for_status()
        return resp.json()

    def create_form(self, name: str, form_type: str = 'embed') -> dict:
        return self._post('/forms', {'name': name, 'type': form_type})

    def get_form(self, form_id: int) -> dict:
        return self._get(f'/forms/{form_id}')

    def create_sequence(self, name: str) -> dict:
        return self._post('/sequences', {'name': name})

    def add_broadcast_to_sequence(self, sequence_id: int, subject: str,
                                  content: str, delay_days: int = 0) -> dict:
        return self._post('/broadcasts', {
            'subject': subject,
            'content': content,
            'sequence_id': sequence_id,
        })

    def get_subscriber_count(self) -> int:
        data = self._get('/subscribers', {'sort_order': 'desc', 'page': 1})
        return data.get('total_subscribers', 0)

    def get_form_subscriber_count(self, form_id: int) -> int:
        data = self._get(f'/forms/{form_id}/subscriptions')
        return data.get('total_subscriptions', 0)


def deploy_convertkit_landing_page(user_id: str, simulation_id: str,
                                   action_id: str, action_type: str,
                                   artifact_version_id: str, layer_number: int,
                                   artifact: dict) -> dict:
    """
    Create a ConvertKit form + nurture sequence from a landing page artifact (FR-PUB-02).
    Returns {'hosted_url': ..., 'embed_code': ..., 'ck_form_id': ..., 'ck_sequence_id': ...}
    """
    from app.models.integration import UserIntegration
    from app.models.artifact import ArtifactVersion
    from app.extensions import db

    integration = UserIntegration.query.filter_by(
        user_id=user_id, provider='convertkit'
    ).first()
    if not integration or not integration.is_connected:
        raise ConvertKitAuthRequired('convertkit_not_configured')

    api_key = integration.decrypt_access_token()
    api_secret = integration.decrypt_refresh_token() if integration.refresh_token_enc else api_key
    ck = ConvertKitClient(api_key, api_secret)

    form_name = artifact.get('form_name') or artifact.get('title') or 'Landing Page'
    seq_name = artifact.get('sequence_name') or f'{form_name} — Nurture'

    form_data = ck.create_form(form_name)
    form_id = form_data.get('form', {}).get('id') or form_data.get('id')
    if not form_id:
        raise ConvertKitError('ConvertKit did not return a form ID')

    seq_data = ck.create_sequence(seq_name)
    sequence_id = (seq_data.get('sequence') or {}).get('id') or seq_data.get('id')

    nurture_emails = artifact.get('nurture_emails') or []
    for email in nurture_emails[:10]:
        try:
            ck.add_broadcast_to_sequence(
                sequence_id=sequence_id,
                subject=email.get('subject', 'Follow-up'),
                content=email.get('body', ''),
                delay_days=int(email.get('delay_days', 1)),
            )
        except Exception as exc:
            logger.warning('ConvertKit broadcast creation failed: %s', exc)

    # Get the hosted landing page URL from form details
    form_detail = ck.get_form(form_id)
    hosted_url = (form_detail.get('form') or {}).get('url') or f'https://app.convertkit.com/forms/{form_id}'
    embed_js = (form_detail.get('form') or {}).get('embed_js') or ''
    embed_code = f'<script src="{embed_js}"></script>' if embed_js else ''

    if artifact_version_id:
        av = ArtifactVersion.query.get(artifact_version_id)
        if av:
            av.public_url = hosted_url
            db.session.commit()

    logger.info('ConvertKit form deployed: user=%s form_id=%s url=%s', user_id, form_id, hosted_url)
    return {
        'hosted_url': hosted_url,
        'embed_code': embed_code,
        'ck_form_id': form_id,
        'ck_sequence_id': sequence_id,
    }


# ── ConvertKit subscriber harvest (FR-PUB-05) ────────────────────────────────

def harvest_convertkit_subscribers(user_id: str, simulation_id: str) -> int:
    """
    Fetch current subscriber count from ConvertKit and update L2/L3/L4 momentum.
    Returns the subscriber count (or 0 if integration not connected).
    """
    from app.models.integration import UserIntegration
    from app.models.layer6 import Layer6Momentum
    from app.extensions import db
    from datetime import date

    integration = UserIntegration.query.filter_by(
        user_id=user_id, provider='convertkit'
    ).first()
    if not integration or not integration.is_connected:
        return 0

    try:
        api_key = integration.decrypt_access_token()
        ck = ConvertKitClient(api_key)
        count = ck.get_subscriber_count()
    except Exception as exc:
        logger.warning('ConvertKit subscriber harvest failed for user=%s: %s', user_id, exc)
        return 0

    momentum = Layer6Momentum.query.filter_by(
        simulation_id=simulation_id
    ).order_by(Layer6Momentum.snapshot_date.desc()).first()

    if not momentum:
        from utils.id_gen import generate_id
        momentum = Layer6Momentum(
            id=generate_id(),
            simulation_id=simulation_id,
            snapshot_date=date.today(),
        )
        db.session.add(momentum)

    momentum.email_list_size = count
    db.session.commit()
    logger.info('ConvertKit harvest complete: user=%s simulation=%s subscribers=%d',
                user_id, simulation_id, count)
    return count


class ConvertKitAuthRequired(Exception):
    pass


class ConvertKitError(Exception):
    pass
