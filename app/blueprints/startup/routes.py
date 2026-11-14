"""Public startup page routes (FOUNDER_PLAN.md WP6).

    GET  /startup/<slug>            the public view; published pages only, old slugs 301
    GET  /startup/<slug>/logo       the page logo (published pages only)
    POST /startup/<slug>/request    intro request (JSON or form); the data-room
                                    request is investor-view only (WP8 passes
                                    allow_data_room=True to submit_request)

Every route 404s while the founder_track switch is off. The investor view
(/startup/<slug>/i/<token>, /unlock, /signin/<token>, /onepager.pdf) is WP8's
investor.py; it renders the same template with investor_view=True.
"""
from __future__ import annotations

import os

from flask import abort, current_app, jsonify, redirect, render_template, request, send_from_directory, url_for
from flask_login import current_user

from app.blueprints.startup import startup_bp


def _gate():
    from app.services import founder_feature
    if not founder_feature.public_pages_enabled():
        abort(404)


def _published_or_404(slug, endpoint):
    """The published page for slug; an old slug 301s to the current one."""
    from app.services import startup_page_service as sps
    page, moved = sps.resolve_slug(slug)
    if page is None or not sps.is_public(page):
        abort(404)
    if moved:
        abort(redirect(url_for(endpoint, slug=moved), code=301))
    return page


def render_page(page, ctx, preview: bool = False, status: int = 200):
    """Shared by the public view, the founders' preview and WP8's investor view."""
    from app.services import bio_seo
    origin = bio_seo.canonical_origin(request.url_root)
    seo = {
        'title': '%s%s | SimiSI startup page' % (ctx['name'], (' - ' + ctx['one_liner']) if ctx['one_liner'] else ''),
        'description': (ctx['one_liner'] or ctx['sections']['problem']['text'] or ctx['name'])[:160],
        'canonical': '%s/startup/%s' % (origin, ctx['slug']),
        'image': ('%s/startup/%s/logo' % (origin, ctx['slug'])) if ctx['logo'] else None,
        'indexable': not preview and not ctx.get('investor_view'),
    }
    from app.services import startup_page_service as sps
    return render_template('startup/page.html', p=ctx, seo=seo, preview=preview, is_live=sps.is_public(page),
                           investor_view=bool(ctx.get('investor_view'))), status


@startup_bp.route('/startup/<slug>')
def public_page(slug):
    _gate()
    from app.services import startup_page_service as sps
    page = _published_or_404(slug, 'startup.public_page')
    sps.record_visit(page, request, view='public', user=current_user)
    resp, status = render_page(page, sps.public_context(page))
    return resp, status


@startup_bp.route('/startup/<slug>/logo')
def page_logo(slug):
    _gate()
    from app.services import founder_feature
    from app.services import founder_ventures
    from app.services import startup_page_service as sps
    page, moved = sps.resolve_slug(slug)
    if page is None or not page.logo_path:
        abort(404)
    if not sps.is_public(page):
        # The founders see their own logo in the preview before publishing.
        v = sps._venture(page)
        if not (founder_feature.is_enabled(current_user)
                and founder_ventures.get_member(v, getattr(current_user, 'id', None)) is not None):
            abort(404)
    folder = current_app.config.get('UPLOAD_FOLDER', 'uploads')
    path = os.path.join(folder, page.logo_path)
    if not os.path.isfile(path):
        abort(404)
    resp = send_from_directory(os.path.dirname(os.path.abspath(path)), os.path.basename(path), mimetype='image/png')
    resp.headers['Cache-Control'] = 'public, max-age=3600'
    return resp


@startup_bp.route('/startup/<slug>/request', methods=['POST'])
def page_request(slug):
    _gate()
    from app.services import startup_page_service as sps
    page, moved = sps.resolve_slug(slug)
    if page is None or not sps.is_public(page):
        return jsonify({'error': 'Not found.'}), 404
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        data = request.form.to_dict()
    for key in ('website', 'kind', 'name', 'email', 'firm', 'message'):
        if data.get(key) is not None and not isinstance(data.get(key), str):
            return jsonify({'error': 'Each field must be text.'}), 400
    if (data.get('website') or '').strip():          # honeypot: quietly accept, store nothing
        return jsonify({'ok': True, 'message': 'Thank you. Your request was sent to the founders.'})
    limited = sps.rate_limited(request, page)
    if limited == 'page':
        return jsonify({'error': 'This page has received many requests in the last hour. '
                                 'Please try again later.'}), 429
    if limited:
        return jsonify({'error': 'Too many requests from this connection. Please try again in an hour.'}), 429
    try:
        out = sps.submit_request(page, data, request)
    except sps.PageError as exc:
        return jsonify({'error': str(exc)}), 400
    return jsonify({'ok': True, 'kind': out['kind'],
                    'message': 'Thank you. Your request was sent to the founders of %s. They will reply '
                               'directly if they want to take it further.' % page.name})
