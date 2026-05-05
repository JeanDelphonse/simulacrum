import logging
from flask import request, jsonify, Response, abort
from flask_login import login_required, current_user

from app.blueprints.publishing import publishing_bp
from app.extensions import db

logger = logging.getLogger(__name__)

SAFE_CONTENT_TYPES = {
    'text': 'text/plain; charset=utf-8',
    'html': 'text/html; charset=utf-8',
    'markdown': 'text/plain; charset=utf-8',
}


# ── Public artifact viewer (FR-PUB-04) ───────────────────────────────────────

@publishing_bp.route('/artifacts/<artifact_version_id>')
def serve_artifact(artifact_version_id):
    """
    Serve an artifact's content at its canonical public URL.
    No auth required — content is intentionally public once published.
    """
    from app.models.artifact import ArtifactVersion

    av = ArtifactVersion.query.get_or_404(artifact_version_id)
    if not av.public_url:
        abort(404)
    if not av.content:
        abort(404)

    content_type = SAFE_CONTENT_TYPES.get(av.file_type or 'text', 'text/plain; charset=utf-8')

    if av.file_type == 'html' or (av.content and av.content.strip().startswith('<')):
        content_type = 'text/html; charset=utf-8'

    return Response(av.content, content_type=content_type)


# ── Public sales page (/p/<slug>) (FR-PUB-03) ────────────────────────────────

@publishing_bp.route('/p/<slug>')
def serve_page(slug):
    """
    Serve a Simulacrum-hosted sales/landing page by slug.
    No auth required — these are intentionally public.
    """
    from app.models.published_page import PublishedPage

    page = PublishedPage.query.filter_by(slug=slug, status='live').first_or_404()
    return Response(page.html_content, content_type='text/html; charset=utf-8')


# ── Deploy endpoint (called from GCC) ────────────────────────────────────────

@publishing_bp.route('/api/publishing/deploy', methods=['POST'])
@login_required
def deploy():
    """
    Deploy an artifact to a public URL from the GCC (FR-PUB-01, FR-PUB-02, FR-PUB-03).
    Body: {simulation_id, action_id, action_type, artifact_version_id, layer_number,
           title, html_content?}
    """
    data = request.get_json(silent=True) or {}

    required = ('simulation_id', 'action_type')
    missing = [f for f in required if not data.get(f)]
    if missing:
        return jsonify({'error': f'Missing: {", ".join(missing)}'}), 400

    action_type = data['action_type']
    simulation_id = data['simulation_id']
    action_id = data.get('action_id')
    artifact_version_id = data.get('artifact_version_id')
    layer_number = int(data.get('layer_number', 3))
    title = data.get('title') or action_type.replace('_', ' ').title()

    # Resolve HTML content
    html_content = data.get('html_content', '')
    if not html_content and artifact_version_id:
        from app.models.artifact import ArtifactVersion
        av = ArtifactVersion.query.get(artifact_version_id)
        if av and av.content:
            html_content = av.content

    if not html_content and action_id:
        from app.models.artifact import ArtifactVersion
        av = ArtifactVersion.query.filter_by(
            action_id=action_id, is_current=True
        ).first()
        if av:
            artifact_version_id = av.id
            html_content = av.content or ''

    if not html_content and action_id:
        from app.models.agent_action import AgentAction
        aa = AgentAction.query.get(action_id)
        if aa and aa.artifact:
            html_content = f'<pre style="font-family:sans-serif;white-space:pre-wrap;padding:2rem">{aa.artifact}</pre>'

    if not html_content:
        return jsonify({'error': 'No content available to publish.'}), 422

    from app.services.publishing_service import (
        deploy_sales_page, deploy_convertkit_landing_page,
        publish_artifact_url, PAGE_ACTION_TYPES, CONVERTKIT_ACTION_TYPES,
        ConvertKitAuthRequired,
    )

    result = {}

    # Sales pages deploy to /p/<slug> (FR-PUB-03)
    if action_type in PAGE_ACTION_TYPES:
        result = deploy_sales_page(
            user_id=current_user.id,
            simulation_id=simulation_id,
            action_id=action_id,
            action_type=action_type,
            artifact_version_id=artifact_version_id,
            layer_number=layer_number,
            html_content=html_content,
            title=title,
        )

    elif action_type in CONVERTKIT_ACTION_TYPES:
        # Deploy to ConvertKit (FR-PUB-02)
        artifact_dict = data.get('artifact') or {}
        artifact_dict.setdefault('form_name', title)
        try:
            ck_result = deploy_convertkit_landing_page(
                user_id=current_user.id,
                simulation_id=simulation_id,
                action_id=action_id,
                action_type=action_type,
                artifact_version_id=artifact_version_id,
                layer_number=layer_number,
                artifact=artifact_dict,
            )
            result = ck_result
        except ConvertKitAuthRequired:
            return jsonify({'error': 'convertkit_not_configured',
                            'message': 'Connect ConvertKit in Settings → Integrations first.'}), 403

    else:
        # Generic artifact hosting (FR-PUB-01)
        if artifact_version_id:
            public_url = publish_artifact_url(artifact_version_id)
            result = {'public_url': public_url}
        else:
            result = {}

    return jsonify({'ok': True, **result}), 200


# ── Harvest endpoint ──────────────────────────────────────────────────────────

@publishing_bp.route('/api/publishing/harvest/<sim_id>', methods=['POST'])
@login_required
def harvest(sim_id):
    """Trigger a ConvertKit subscriber harvest for a simulation (FR-PUB-05)."""
    from app.models.simulation import Simulation
    sim = Simulation.query.filter_by(id=sim_id, user_id=current_user.id).first_or_404()

    from app.services.publishing_service import harvest_convertkit_subscribers
    count = harvest_convertkit_subscribers(current_user.id, sim.id)
    return jsonify({'ok': True, 'subscriber_count': count}), 200
