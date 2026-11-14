"""Investor access routes, startup side (FOUNDER_PLAN.md WP8 part A).

Public (investor) routes; every one 404s unless startup_access_service.gate_open()
(founder_investor not off, counsel sign-off recorded, startup pages on) and the
page is published:

    GET  /startup/<slug>/i/<token>          private link (free, invited access)
    GET  /startup/<slug>/signin             enter an email; POST sends a sign-in link
    GET  /startup/<slug>/signin/<token>     verify the link; the email lives in the session only
    GET  /startup/<slug>/investor           the investor view for a verified email or opened link
    GET  /startup/<slug>/unlock             price, what unlocks, terms; POST starts Stripe Checkout
    GET  /startup/<slug>/unlock/success     Stripe return (webhook fallback)
    GET  /startup/<slug>/access-request     ask the founders; POST records the request
    POST /startup/<slug>/investor-request   intro or data-room request (investors with access only)
    GET  /startup/<slug>/onepager.pdf       public version for anyone; investor version with access
    POST /startup/<slug>/signout            forget this browser's investor identity

Founder JSON APIs (member of the venture with founder_investor enabled; issuing,
revoking and deciding need the owner or the investor_contact right holder):

    GET  /api/founder/v/<id>/investor-links
    POST /api/founder/v/<id>/investor-links               {label, investor_email?}
    POST /api/founder/v/<id>/investor-links/<link_id>/revoke
    GET  /api/founder/v/<id>/investor-access
    POST /api/founder/v/<id>/investor-access/<access_id>  {action: approve | deny}
"""
from __future__ import annotations

import logging

from flask import abort, jsonify, make_response, redirect, render_template, request, url_for
from flask_login import current_user

from app.blueprints.startup import startup_bp

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sas():
    from app.services import startup_access_service
    return startup_access_service


def _page_or_404(slug, endpoint, **kw):
    """The published page for slug behind the investor gate; old slugs 301."""
    from app.services import startup_page_service as sps
    sas = _sas()
    if not sas.gate_open():
        abort(404)
    page, moved = sps.resolve_slug(slug)
    if page is None or not sps.is_public(page) or not sas.gate_open(page):
        abort(404)
    if moved:
        abort(redirect(url_for(endpoint, slug=moved, **kw), code=301))
    return page


def _no_store(resp):
    resp = make_response(resp)
    resp.headers['Cache-Control'] = 'no-store'
    resp.headers['X-Robots-Tag'] = 'noindex, nofollow'
    resp.headers['Referrer-Policy'] = 'no-referrer'
    return resp


def _seo(ctx):
    from app.services import bio_seo
    origin = bio_seo.canonical_origin(request.url_root)
    return {'title': '%s | Investor view | SimiSI startup page' % ctx['name'],
            'description': (ctx['one_liner'] or ctx['name'])[:160],
            'canonical': '%s/startup/%s' % (origin, ctx['slug']), 'image': None, 'indexable': False}


def _shell(template, page, status=200, **kw):
    """The small investor pages (sign in, unlock, request access)."""
    from app.services import startup_page_service as sps
    sas = _sas()
    ctx = sps.public_context(page)
    return _no_store((render_template(template, p=ctx, seo=_seo(ctx), page=page,
                                      emails=sas.session_emails(), price_cents=sas.price_cents(),
                                      paid_available=sas.paid_access_available(),
                                      refund_days=sas.refund_window_days(), unlocks=sas.WHAT_UNLOCKS,
                                      exemption=sas.exemption(sas._venture(page)), **kw), status))


def _render_investor(page, access=None, link=None):
    from app.services import startup_page_service as sps
    sas = _sas()
    ctx = sas.investor_context(page, access, link)
    sps.record_visit(page, request, view='investor', investor_link_id=getattr(link, 'id', None) or
                     getattr(access, 'investor_link_id', None), user=current_user)
    return _no_store((render_template('startup/investor_view.html', p=ctx, seo=_seo(ctx), preview=False,
                                      is_live=True, investor_view=True, emails=sas.session_emails(),
                                      refund_days=sas.refund_window_days()), 200))


def _form():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        data = request.form.to_dict()
    return {k: v for k, v in data.items() if isinstance(v, str)}


def _wants_json():
    return request.is_json or 'application/json' in (request.headers.get('Accept') or '')


# ---------------------------------------------------------------------------
# Private link
# ---------------------------------------------------------------------------

@startup_bp.route('/startup/<slug>/i/<token>')
def investor_link(slug, token):
    page = _page_or_404(slug, 'startup.investor_link', token=token)
    sas = _sas()
    link = sas.resolve_link(token)
    if link is None or link.startup_page_id != page.id:
        return _shell('startup/investor_signin.html', page, status=410, step='link_inactive')
    sas.remember_link(link)
    access = None
    if link.investor_email:
        access = sas.grant_invited(page, link, link.investor_email)
    else:
        for e in sas.session_emails():
            access = sas.grant_invited(page, link, e)
            break
    return _render_investor(page, access, link)


# ---------------------------------------------------------------------------
# Email sign-in
# ---------------------------------------------------------------------------

@startup_bp.route('/startup/<slug>/signin', methods=['GET', 'POST'])
def investor_signin(slug):
    page = _page_or_404(slug, 'startup.investor_signin')
    sas = _sas()
    nxt = (request.values.get('next') or '').strip()
    nxt = nxt if nxt in ('view', 'unlock', 'request') else ''
    if request.method == 'GET':
        return _shell('startup/investor_signin.html', page, step='form', nxt=nxt)
    data = _form()
    if (data.get('website') or '').strip():          # honeypot
        return _shell('startup/investor_signin.html', page, step='sent', nxt=nxt)
    link = sas.session_link_for(page)
    try:
        sas.send_signin(data.get('email'), page, link=link, nxt=nxt)
    except sas.AccessError as exc:
        return _shell('startup/investor_signin.html', page, status=exc.status, step='form', nxt=nxt,
                      error=str(exc), email=data.get('email', '')[:254])
    return _shell('startup/investor_signin.html', page, step='sent', nxt=nxt)


@startup_bp.route('/startup/<slug>/signin/<token>')
def investor_signin_verify(slug, token):
    page = _page_or_404(slug, 'startup.investor_signin_verify', token=token)
    sas = _sas()
    data = sas.load_signin(token, page)
    email = sas.verify_signin(token, page) if data is not None else None
    if email is None:
        return _shell('startup/investor_signin.html', page, status=400, step='bad_token')
    if data.get('l'):
        from app.extensions import db
        from app.models.founder import InvestorLink
        link = db.session.get(InvestorLink, data['l'])
        if link is not None and link.revoked_at is None and link.startup_page_id == page.id:
            sas.grant_invited(page, link, email)
    nxt = data.get('n') or ''
    if nxt == 'unlock':
        return redirect(url_for('startup.investor_unlock', slug=page.slug))
    if nxt == 'request':
        return redirect(url_for('startup.investor_access_request', slug=page.slug))
    return redirect(url_for('startup.investor_view', slug=page.slug))


@startup_bp.route('/startup/<slug>/signout', methods=['POST'])
def investor_signout(slug):
    _sas().forget_session()
    return redirect('/startup/%s' % slug)


# ---------------------------------------------------------------------------
# Investor view
# ---------------------------------------------------------------------------

@startup_bp.route('/startup/<slug>/investor')
def investor_view(slug):
    page = _page_or_404(slug, 'startup.investor_view')
    sas = _sas()
    email, access, link = sas.viewer(page)
    if access is None and link is None:
        if email:
            return redirect(url_for('startup.investor_unlock', slug=page.slug))
        return redirect(url_for('startup.investor_signin', slug=page.slug, next='view'))
    if access is not None and access.status == 'requested' and access.kind != 'paid':
        return redirect(url_for('startup.investor_access_request', slug=page.slug))
    return _render_investor(page, access, link)


# ---------------------------------------------------------------------------
# Unlock (payment)
# ---------------------------------------------------------------------------

@startup_bp.route('/startup/<slug>/unlock', methods=['GET'])
def investor_unlock(slug):
    page = _page_or_404(slug, 'startup.investor_unlock')
    sas = _sas()
    email, access, link = sas.viewer(page)
    if sas.is_full(page, access, link):
        return redirect(url_for('startup.investor_view', slug=page.slug))
    notice = 'Checkout was cancelled. Nothing was charged.' if request.args.get('cancelled') else None
    return _shell('startup/investor_unlock.html', page, email=email, access=access, notice=notice)


@startup_bp.route('/startup/<slug>/unlock', methods=['POST'])
def investor_unlock_start(slug):
    from flask import current_app
    page = _page_or_404(slug, 'startup.investor_unlock')
    sas = _sas()
    emails = sas.session_emails()
    email = (_form().get('email') or (emails[0] if emails else '')).strip().lower()
    if not email or email not in emails:
        return redirect(url_for('startup.investor_signin', slug=page.slug, next='unlock'))
    if not current_app.config.get('STRIPE_SECRET_KEY'):
        return _shell('startup/investor_unlock.html', page, status=503, email=email,
                      error='Payments are not available right now. Please try again later.')
    try:
        result = sas.create_checkout(page, email)
    except sas.AccessError as exc:
        return _shell('startup/investor_unlock.html', page, status=exc.status, email=email, error=str(exc))
    except ValueError as exc:
        return _shell('startup/investor_unlock.html', page, status=400, email=email, error=str(exc))
    except Exception as exc:
        logger.error('investor checkout failed for page %s: %s', page.id, exc)
        return _shell('startup/investor_unlock.html', page, status=502, email=email,
                      error='We could not start checkout. Please try again in a minute.')
    return redirect(result['checkout_url'], code=303)


@startup_bp.route('/startup/<slug>/unlock/success')
def investor_unlock_success(slug):
    from flask import current_app
    page = _page_or_404(slug, 'startup.investor_unlock_success')
    sas = _sas()
    sid = (request.args.get('session_id') or '').strip()
    if sid and current_app.config.get('STRIPE_SECRET_KEY') and sas.session_emails():
        sas.verify_session(sid, page, sas.session_emails())
    email, access, link = sas.viewer(page)
    if sas.is_full(page, access, link):
        return redirect(url_for('startup.investor_view', slug=page.slug))
    if access is not None and access.kind == 'paid' and access.status == 'requested':
        return _shell('startup/investor_unlock.html', page, email=email, access=access,
                      notice='Payment received. The founders review access before the ask is shown; you will get '
                             'an email when they decide. If they decline, the fee is refunded.')
    return _shell('startup/investor_unlock.html', page, email=email, access=access,
                  notice='We are confirming your payment with the card processor. Refresh this page in a minute; '
                         'you will not be charged twice.')


# ---------------------------------------------------------------------------
# Access request
# ---------------------------------------------------------------------------

@startup_bp.route('/startup/<slug>/access-request', methods=['GET', 'POST'])
def investor_access_request(slug):
    page = _page_or_404(slug, 'startup.investor_access_request')
    sas = _sas()
    email, access, link = sas.viewer(page)
    if sas.is_full(page, access, link):
        if request.method == 'POST' and _wants_json():
            return jsonify({'ok': True, 'status': 'active'})
        return redirect(url_for('startup.investor_view', slug=page.slug))
    if request.method == 'GET':
        return _shell('startup/investor_request.html', page, email=email, access=access)
    if not email or email not in sas.session_emails():
        if _wants_json():
            return jsonify({'error': 'Sign in with your email first.'}), 401
        return redirect(url_for('startup.investor_signin', slug=page.slug, next='request'))
    data = _form()
    try:
        row = sas.request_access(page, email, data.get('message'), data.get('name'))
    except sas.AccessError as exc:
        if _wants_json():
            return jsonify({'error': str(exc)}), exc.status
        return _shell('startup/investor_request.html', page, status=exc.status, email=email, access=access,
                      error=str(exc))
    if _wants_json():
        return jsonify({'ok': True, 'status': row.status})
    return _shell('startup/investor_request.html', page, email=email, access=row, sent=True)


@startup_bp.route('/startup/<slug>/investor-request', methods=['POST'])
def investor_page_request(slug):
    """Intro or data-room request from the investor view (full access only)."""
    from app.services import startup_page_service as sps
    sas = _sas()
    if not sas.gate_open():
        return jsonify({'error': 'Not found.'}), 404
    page, _moved = sps.resolve_slug(slug)
    if page is None or not sps.is_public(page) or not sas.gate_open(page):
        return jsonify({'error': 'Not found.'}), 404
    email, access, link = sas.viewer(page)
    full = sas.is_full(page, access, link)
    if not full and not (access is not None and access.kind == 'paid' and access.status == 'requested'):
        return jsonify({'error': 'The data room request is open to investors with access to this page.'}), 403
    data = _form()
    if not full and (data.get('kind') or '') == 'data_room':
        return jsonify({'error': 'The data room request opens once the founders approve your access.'}), 403
    if (data.get('website') or '').strip():
        return jsonify({'ok': True, 'message': 'Thank you. Your request was sent to the founders.'})
    limited = sps.rate_limited(request, page)
    if limited:
        return jsonify({'error': 'Too many requests in the last hour. Please try again later.'}), 429
    try:
        out = sps.submit_request(page, data, request, allow_data_room=full)
    except sps.PageError as exc:
        return jsonify({'error': str(exc)}), 400
    return jsonify({'ok': True, 'kind': out['kind'],
                    'message': 'Thank you. Your request was sent to the founders of %s. They decide whether and '
                               'how to reply.' % page.name})


# ---------------------------------------------------------------------------
# One-pager
# ---------------------------------------------------------------------------

@startup_bp.route('/startup/<slug>/onepager.pdf')
def investor_onepager(slug):
    from app.services import founder_onepager
    page = _page_or_404(slug, 'startup.investor_onepager')
    sas = _sas()
    email, access, link = sas.viewer(page)
    investor = sas.is_full(page, access, link) and request.args.get('v') != 'public'
    pdf = founder_onepager.render_pdf(page, investor, access if investor else None)
    resp = make_response(pdf)
    resp.headers['Content-Type'] = 'application/pdf'
    resp.headers['Content-Disposition'] = 'inline; filename="%s-%s.pdf"' % (
        page.slug, 'investor-one-pager' if investor else 'one-pager')
    resp.headers['Cache-Control'] = 'no-store' if investor else 'public, max-age=600'
    resp.headers['X-Robots-Tag'] = 'noindex'
    return resp


# ---------------------------------------------------------------------------
# Founder JSON APIs (part B's investors page calls these)
# ---------------------------------------------------------------------------

def _api_venture(venture_id):
    """(venture, page) for an active member with founder_investor enabled."""
    from app.services import founder_feature, founder_ventures
    from app.services import startup_page_service as sps
    if not founder_feature.is_enabled(current_user, founder_feature.INVESTOR):
        abort(404)
    venture = founder_ventures.require_member(venture_id, current_user)
    page = sps._page_for(venture)
    return venture, page


def _api_error(exc):
    return jsonify({'error': str(exc)}), getattr(exc, 'status', 400)


@startup_bp.route('/api/founder/v/<venture_id>/investor-links', methods=['GET'])
def api_investor_links(venture_id):
    venture, page = _api_venture(venture_id)
    sas = _sas()
    if page is None:
        return jsonify({'links': [], 'can_manage': sas.can_manage(venture, current_user), 'page': None})
    return jsonify({'links': [sas.link_dict(page, ln) for ln in sas.links(page)],
                    'can_manage': sas.can_manage(venture, current_user),
                    'page': {'slug': page.slug, 'status': page.status}})


@startup_bp.route('/api/founder/v/<venture_id>/investor-links', methods=['POST'])
def api_investor_link_issue(venture_id):
    venture, page = _api_venture(venture_id)
    sas = _sas()
    if page is None:
        return jsonify({'error': 'Create the startup page first.'}), 409
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({'error': 'Send a JSON object.'}), 400
    try:
        link = sas.issue_link(page, current_user, data.get('label') if isinstance(data.get('label'), str) else '',
                              data.get('investor_email') if isinstance(data.get('investor_email'), str) else None)
    except sas.AccessError as exc:
        return _api_error(exc)
    return jsonify({'ok': True, 'link': sas.link_dict(page, link)}), 201


@startup_bp.route('/api/founder/v/<venture_id>/investor-links/<link_id>/revoke', methods=['POST'])
def api_investor_link_revoke(venture_id, link_id):
    from app.extensions import db
    from app.models.founder import InvestorLink
    venture, page = _api_venture(venture_id)
    sas = _sas()
    link = db.session.get(InvestorLink, link_id)
    if link is None or page is None or link.venture_id != venture.id:
        return jsonify({'error': 'Not found.'}), 404
    try:
        sas.revoke_link(link, current_user)
    except sas.AccessError as exc:
        return _api_error(exc)
    return jsonify({'ok': True, 'link': sas.link_dict(page, link)})


@startup_bp.route('/api/founder/v/<venture_id>/investor-access', methods=['GET'])
def api_investor_access(venture_id):
    venture, page = _api_venture(venture_id)
    sas = _sas()
    return jsonify({'access': sas.access_list(page) if page is not None else [],
                    'can_manage': sas.can_manage(venture, current_user),
                    'exemption': sas.exemption(venture), 'price_cents': sas.price_cents()})


@startup_bp.route('/api/founder/v/<venture_id>/investor-access/<access_id>', methods=['POST'])
def api_investor_access_decide(venture_id, access_id):
    from app.extensions import db
    from app.models.founder import InvestorAccess
    venture, page = _api_venture(venture_id)
    sas = _sas()
    row = db.session.get(InvestorAccess, access_id)
    if row is None or page is None or row.startup_page_id != page.id:
        return jsonify({'error': 'Not found.'}), 404
    data = request.get_json(silent=True)
    action = data.get('action') if isinstance(data, dict) else None
    try:
        if action == 'approve':
            sas.approve_access(row, current_user)
        elif action == 'deny':
            sas.deny_access(row, current_user)
        else:
            return jsonify({'error': 'Use action approve or deny.'}), 400
    except sas.AccessError as exc:
        return _api_error(exc)
    return jsonify({'ok': True, 'status': row.status})
