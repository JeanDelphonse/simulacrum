"""Celery task: compile an ArtifactBundle into a ZIP with Claude-generated cover PDF."""
import logging
import os
import zipfile
import json
from datetime import datetime

from celery_worker import celery

logger = logging.getLogger(__name__)

# Subdirectory sort order for layer portfolio bundles (audience-aware)
DEFAULT_SORT_ORDER = [
    '01_Strategy',
    '02_Execution',
    '03_Tracking',
    '04_Financials',
    '05_Supporting',
]

ACTION_CATEGORY_MAP = {
    # Strategy documents
    'consulting_proposal': '01_Strategy',
    'sales_page': '01_Strategy',
    'course_framework': '01_Strategy',
    'investment_policy_statement': '01_Strategy',
    'entity_structure': '01_Strategy',
    'saas_product_spec': '01_Strategy',
    'real_estate_strategy': '01_Strategy',
    # Execution / outreach artifacts
    'cold_email_campaign': '02_Execution',
    'outreach_email': '02_Execution',
    'launch_email_sequence': '02_Execution',
    'speaking_proposals': '02_Execution',
    'corporate_training_proposal': '02_Execution',
    'waitlist_landing_page': '02_Execution',
    'affiliate_program': '02_Execution',
    'funnel_design': '02_Execution',
    # Tracking / operational
    'seo_content_calendar': '03_Tracking',
    'dca_schedule': '03_Tracking',
    'ab_test_plan': '03_Tracking',
    'testimonial_system': '03_Tracking',
    # Financials
    'rate_card': '04_Financials',
    'compound_growth': '04_Financials',
    'portfolio_analysis': '04_Financials',
    'tax_optimization': '04_Financials',
    'income_allocation': '04_Financials',
}


@celery.task(bind=True, max_retries=2, default_retry_delay=15)
def compile_bundle_task(self, bundle_id: str):
    """Compile all artifact versions for a bundle into a ZIP with a cover PDF."""
    from app.extensions import db
    from app.models.artifact import ArtifactBundle, ArtifactVersion
    from app.models.agent_action import AgentAction
    from app.models.simulation import Simulation
    from app.models.user import User
    from app.models.platform_settings import PlatformSetting

    bundle = ArtifactBundle.query.get(bundle_id)
    if not bundle:
        logger.error('Bundle %s not found', bundle_id)
        return

    bundle.status = ArtifactBundle.STATUS_PROCESSING
    db.session.commit()

    try:
        sim = Simulation.query.get(bundle.simulation_id)
        user = User.query.get(bundle.user_id)
        if not sim or not user:
            raise RuntimeError('Simulation or user not found')

        # Gather current artifact versions for the selected action IDs
        artifacts = []
        for action_id in bundle.artifact_ids:
            action = AgentAction.query.get(action_id)
            if not action or not action.artifact:
                continue
            version = ArtifactVersion.query.filter_by(
                action_id=action_id, is_current=True,
            ).first()
            artifacts.append({
                'action': action,
                'version': version,
                'content': action.artifact,
                'category': ACTION_CATEGORY_MAP.get(action.action_type, '05_Supporting'),
            })

        if not artifacts:
            raise RuntimeError('No artifact content available to bundle')

        # Determine sort order from BundleTypeConfig or use default
        sort_order = _get_sort_order(bundle.bundle_type)

        # Sort artifacts into categories
        categorised: dict[str, list] = {cat: [] for cat in sort_order}
        for art in artifacts:
            cat = art['category']
            if cat not in categorised:
                cat = '05_Supporting'
            categorised.setdefault(cat, []).append(art)

        # Generate cover document via Claude
        cover_text = _generate_cover(sim, user, bundle, artifacts)

        # Build ZIP in memory
        bundle_dir = _ensure_bundle_dir(user.id)
        zip_filename = _zip_filename(sim, bundle)
        zip_path = os.path.join(bundle_dir, zip_filename)

        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            # Cover as first file
            zf.writestr('00_Cover.txt', cover_text)

            # Artifacts by category
            for cat in sort_order:
                for art in categorised.get(cat, []):
                    filename = _artifact_filename(art['action'], art['version'])
                    zf.writestr(f'{cat}/{filename}', art['content'] or '')

            # Bundle manifest
            manifest = _build_manifest(bundle, artifacts)
            zf.writestr('manifest.json', json.dumps(manifest, indent=2))

        bundle.file_path = zip_path
        bundle.status = ArtifactBundle.STATUS_READY
        db.session.commit()
        logger.info('Bundle %s compiled at %s', bundle_id, zip_path)

        # Notify user
        _notify_user(user, sim, bundle)

    except Exception as exc:
        logger.error('Bundle %s compilation failed: %s', bundle_id, exc)
        try:
            self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            bundle.status = ArtifactBundle.STATUS_FAILED
            bundle.error_message = str(exc)
            db.session.commit()


def _generate_cover(sim, user, bundle, artifacts: list) -> str:
    """Generate a cover document via Claude API describing the bundle."""
    try:
        import anthropic
        from flask import current_app

        from utils.model_router import get_model
        model = get_model('bio_generation')
        client = anthropic.Anthropic()

        artifact_list = '\n'.join(
            f'- {a["action"].action_type.replace("_", " ").title()} (Layer {a["action"].layer_number})'
            for a in artifacts
        )

        prompt = (
            f'Generate a professional cover document for a bundle of career wealth simulation artifacts.\n\n'
            f'Bundle title: {bundle.bundle_name}\n'
            f'User name: {user.full_name}\n'
            f'Expertise Zone: {sim.expertise_zone}\n'
            f'Date: {datetime.utcnow().strftime("%B %d, %Y")}\n'
            f'Bundle type: {bundle.bundle_type}\n'
            f'Audience: {bundle.audience or "General"}\n\n'
            f'Included artifacts:\n{artifact_list}\n\n'
            f'Write a concise professional cover page (plain text) that includes:\n'
            f'1. Bundle title and metadata (user name, zone, date)\n'
            f'2. A brief executive summary (2–3 sentences) of the overall strategy represented\n'
            f'3. A one-sentence description of each included artifact\n\n'
            f'Keep it professional, concise, and audience-appropriate for: {bundle.audience or "a business audience"}.'
        )

        message = client.messages.create(
            model=model,
            max_tokens=800,
            messages=[{'role': 'user', 'content': prompt}],
        )
        return message.content[0].text
    except Exception as e:
        logger.warning('Claude cover generation failed, using fallback: %s', e)
        return _fallback_cover(sim, user, bundle, artifacts)


def _fallback_cover(sim, user, bundle, artifacts: list) -> str:
    lines = [
        bundle.bundle_name or 'Simulacrum Bundle',
        '=' * 60,
        f'User: {user.full_name}',
        f'Expertise Zone: {sim.expertise_zone}',
        f'Date: {datetime.utcnow().strftime("%B %d, %Y")}',
        f'Bundle Type: {bundle.bundle_type}',
        '',
        'Included Artifacts:',
    ]
    for a in artifacts:
        label = a['action'].action_type.replace('_', ' ').title()
        layer = a['action'].layer_number
        lines.append(f'  • Layer {layer} — {label}')
    return '\n'.join(lines)


def _get_sort_order(bundle_type: str) -> list[str]:
    try:
        from app.models.artifact import BundleTypeConfig
        config = BundleTypeConfig.query.filter_by(bundle_type=bundle_type).first()
        if config and config.sort_order:
            return config.sort_order
    except Exception:
        pass
    return DEFAULT_SORT_ORDER


def _ensure_bundle_dir(user_id: str) -> str:
    base = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'bundles', user_id)
    os.makedirs(base, exist_ok=True)
    return base


def _zip_filename(sim, bundle) -> str:
    safe_zone = (sim.expertise_zone or 'Simulation').replace(' ', '_').replace('/', '-')[:30]
    safe_type = bundle.bundle_type.replace('_', '-')
    date_str = datetime.utcnow().strftime('%b%Y')
    return f'Simulacrum_{safe_type}_{safe_zone}_{date_str}_{bundle.id}.zip'


def _artifact_filename(action, version) -> str:
    label = action.action_type.replace('_', ' ').title().replace(' ', '_')
    ver_num = version.version_number if version else 1
    date_str = datetime.utcnow().strftime('%b%Y')
    return f'{label}_v{ver_num}_{date_str}.txt'


def _build_manifest(bundle, artifacts: list) -> dict:
    return {
        'bundle_id': bundle.id,
        'bundle_type': bundle.bundle_type,
        'bundle_name': bundle.bundle_name,
        'compiled_at': datetime.utcnow().isoformat(),
        'expires_at': bundle.expires_at.isoformat() if bundle.expires_at else None,
        'artifacts': [
            {
                'action_id': a['action'].id,
                'action_type': a['action'].action_type,
                'layer_number': a['action'].layer_number,
                'version_number': a['version'].version_number if a['version'] else 1,
                'category': a['category'],
            }
            for a in artifacts
        ],
    }


def _notify_user(user, sim, bundle):
    try:
        from app.services.email_service import _send
        _send(
            subject=f'Your Simulacrum Bundle is Ready — {bundle.bundle_name}',
            recipients=[user.email],
            body=(
                f'Hi {user.full_name},\n\n'
                f'Your "{bundle.bundle_name}" bundle for simulation "{sim.name}" '
                f'has been compiled and is ready to download.\n\n'
                f'Log in to your Simulacrum dashboard to download it. '
                f'The download link is valid for 7 days.\n\n'
                f'— Simulacrum'
            ),
        )
    except Exception as e:
        logger.warning('Bundle ready email failed for bundle %s: %s', bundle.id, e)
