"""
SIM-PRD-PRIVACY-001 — Bio Page Private Mode routes.

Public (no login):
    POST /bio/<slug>/request-access        → begin LinkedIn OAuth for an access request
    GET  /bio/access/linkedin/callback     → OAuth callback (verify identity, apply rules)
    GET  /bio/access/approve/<token>       → one-tap owner approval from the email

Owner (login required):
    PUT    /api/bio/privacy                 → privacy_mode / accepting_requests / request_notify
    GET    /api/bio/access/requests         → pending/approved/revoked/expired + counts
    POST   /api/bio/access/requests/<id>/approve
    POST   /api/bio/access/requests/<id>/ignore
    POST   /api/bio/access/grants/<id>/revoke
    GET    /api/bio/access/rules
    POST   /api/bio/access/rules
    DELETE /api/bio/access/rules/<id>
    GET    /api/bio/access/analytics
"""
from __future__ import annotations

import logging
import secrets

from flask import (
    jsonify, request, session, redirect, current_app, render_template_string,
)
from flask_login import login_required, current_user

from app.blueprints.bio_privacy import bio_privacy_bp
from app.extensions import db
from app.models.profile import UserProfile
from app.models.bio_privacy import BioAccessRequest, BioAccessGrant, BioAccessRule
from app.services import bio_privacy_service as svc
from utils.id_gen import generate_id

logger = logging.getLogger(__name__)

_FLOW_KEY = 'bio_access_flow'


def _owner_profile(slug: str) -> UserProfile | None:
    return UserProfile.query.filter_by(username=(slug or '').lower()).first()


# ── Public: begin the LinkedIn-verified access request ───────────────────────

@bio_privacy_bp.route('/bio/<slug>/request-access', methods=['POST'])
def request_access(slug: str):
    profile = _owner_profile(slug)
    if not profile or not profile.is_private:
        return jsonify({'error': 'Not found'}), 404
    if not profile.accepting_requests:
        # Paused: the form should be hidden; guard the API too.
        return jsonify({'error': 'not_accepting',
                        'message': 'This profile is not accepting new requests right now.'}), 403

    data = request.get_json(force=True, silent=True) or {}
    message = (data.get('message') or '').strip()[:2000]

    state = secrets.token_urlsafe(24)
    session[_FLOW_KEY] = {
        'state': state,
        'slug': profile.username,
        'owner_user_id': profile.user_id,
        'message': message,
    }
    from app.services.linkedin import get_auth_url
    redirect_uri = current_app.config['LINKEDIN_BIO_REDIRECT_URI']
    return jsonify({'auth_url': get_auth_url(state, redirect_uri=redirect_uri)})


# ── Public: LinkedIn OAuth callback for access requests ──────────────────────

@bio_privacy_bp.route('/bio/access/linkedin/callback', methods=['GET'])
def linkedin_callback():
    code = request.args.get('code')
    state = request.args.get('state')
    error = request.args.get('error')

    flow = session.pop(_FLOW_KEY, None) or {}
    slug = flow.get('slug', '')
    fallback = f'/u/{slug}' if slug else '/'

    if error or not code:
        return redirect(f'{fallback}?access=cancelled')
    if not flow or state != flow.get('state'):
        return redirect(f'{fallback}?access=error')

    owner_user_id = flow['owner_user_id']
    try:
        from app.services.linkedin import exchange_code_for_token, get_user_info
        redirect_uri = current_app.config['LINKEDIN_BIO_REDIRECT_URI']
        token_data = exchange_code_for_token(code, redirect_uri=redirect_uri)
        access_token = token_data.get('access_token')
        if not access_token:
            raise ValueError('No access token returned')
        userinfo = get_user_info(access_token)
    except Exception as exc:
        logger.error('bio access LinkedIn OAuth failed: %s', exc)
        return redirect(f'{fallback}?access=error')

    identity = svc.identity_from_userinfo(userinfo)
    try:
        _req, outcome = svc.record_request(owner_user_id, identity, flow.get('message'))
    except Exception as exc:
        db.session.rollback()
        logger.error('record_request failed: %s', exc, exc_info=True)
        return redirect(f'{fallback}?access=error')

    status_param = {
        'auto_approved': 'granted',
        'already_approved': 'granted',
        'blocked': 'unavailable',      # neutral — never "blocked" (FR-PRV-07)
        'pending': 'requested',
        'already_pending': 'requested',
    }.get(outcome, 'requested')
    return redirect(f'{fallback}?access={status_param}')


# ── Public: one-tap approval from the notification email ─────────────────────

_APPROVE_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Access {{ result }}</title>
<style>body{font-family:system-ui,-apple-system,Segoe UI,sans-serif;background:#f4f6f8;
margin:0;display:flex;min-height:100vh;align-items:center;justify-content:center}
.card{background:#fff;border-radius:14px;padding:40px;max-width:420px;text-align:center;
box-shadow:0 2px 12px rgba(0,0,0,.08)}h1{font-size:20px;color:#0D1B3E;margin:0 0 8px}
p{color:#4b5563;font-size:15px;line-height:1.6;margin:0 0 20px}
a{display:inline-block;background:#0f7b72;color:#fff;text-decoration:none;padding:11px 22px;
border-radius:8px;font-weight:600;font-size:14px}</style></head>
<body><div class="card">
{% if ok %}<h1>✓ Access approved</h1>
<p>{{ name }} can now view your full profile. They've been emailed a link, and
added to your contacts as a verified warm lead.</p>
{% else %}<h1>Link expired or invalid</h1>
<p>This approval link is no longer valid. Open your access dashboard to approve
the request manually.</p>{% endif %}
<a href="/settings/bio-privacy">Open access dashboard</a>
</div></body></html>"""


@bio_privacy_bp.route('/bio/access/approve/<token>', methods=['GET'])
def approve_via_token(token: str):
    payload = svc.verify_approve_token(token)
    if not payload:
        return render_template_string(_APPROVE_PAGE, ok=False, result='error', name=''), 400
    req = svc.approve_request(payload.get('rid'), owner_user_id=payload.get('owner'))
    if not req:
        return render_template_string(_APPROVE_PAGE, ok=False, result='error', name=''), 400
    return render_template_string(_APPROVE_PAGE, ok=True, result='approved',
                                  name=req.requester_name or 'The requester')


# ── Owner: privacy settings ──────────────────────────────────────────────────

@bio_privacy_bp.route('/api/bio/privacy', methods=['GET'])
@login_required
def get_privacy():
    profile = UserProfile.query.filter_by(user_id=current_user.id).first()
    if not profile:
        return jsonify({'error': 'No profile'}), 404
    return jsonify({
        'privacy_mode': profile.privacy_mode or 'public',
        'accepting_requests': bool(profile.accepting_requests),
        'request_notify': profile.request_notify or 'realtime',
    })


@bio_privacy_bp.route('/api/bio/privacy', methods=['PUT'])
@login_required
def update_privacy():
    profile = UserProfile.query.filter_by(user_id=current_user.id).first()
    if not profile:
        return jsonify({'error': 'No profile'}), 404
    data = request.get_json(force=True, silent=True) or {}

    if 'privacy_mode' in data:
        mode = data['privacy_mode']
        if mode in ('public', 'private'):
            profile.privacy_mode = mode
    if 'accepting_requests' in data:
        profile.accepting_requests = bool(data['accepting_requests'])
    if 'request_notify' in data:
        rn = data['request_notify']
        if rn in ('realtime', 'digest'):
            profile.request_notify = rn

    db.session.commit()
    return jsonify({
        'privacy_mode': profile.privacy_mode,
        'accepting_requests': bool(profile.accepting_requests),
        'request_notify': profile.request_notify,
    })


# ── Owner: access dashboard (requests + grants) ──────────────────────────────

@bio_privacy_bp.route('/api/bio/access/requests', methods=['GET'])
@login_required
def list_requests():
    reqs = BioAccessRequest.query.filter_by(owner_user_id=current_user.id).order_by(
        BioAccessRequest.created_at.desc()
    ).all()
    grants = {
        g.requester_linkedin: g
        for g in BioAccessGrant.query.filter_by(owner_user_id=current_user.id).all()
    }
    out = []
    for r in reqs:
        d = r.to_dict()
        g = grants.get(r.requester_linkedin)
        d['grant_id'] = g.id if g and g.is_active else None
        d['view_count'] = (g.view_count if g else 0) or 0
        d['last_viewed_at'] = (g.last_viewed_at.isoformat()
                               if g and g.last_viewed_at else None)
        out.append(d)
    counts = {
        'pending': sum(1 for r in reqs if r.status == BioAccessRequest.STATUS_PENDING),
        'approved': sum(1 for r in reqs if r.is_approved),
        'revoked': sum(1 for r in reqs if r.status == BioAccessRequest.STATUS_REVOKED),
        'expired': sum(1 for r in reqs if r.status == BioAccessRequest.STATUS_EXPIRED),
        'blocked': sum(1 for r in reqs if r.status == BioAccessRequest.STATUS_BLOCKED),
    }
    return jsonify({'requests': out, 'counts': counts})


@bio_privacy_bp.route('/api/bio/access/requests/<request_id>/approve', methods=['POST'])
@login_required
def approve(request_id: str):
    req = svc.approve_request(request_id, owner_user_id=current_user.id)
    if not req:
        return jsonify({'error': 'Not found'}), 404
    return jsonify({'ok': True, 'request': req.to_dict()})


@bio_privacy_bp.route('/api/bio/access/requests/<request_id>/ignore', methods=['POST'])
@login_required
def ignore(request_id: str):
    """Silently dismiss a pending request (marks it expired — no email is sent)."""
    from datetime import datetime
    req = BioAccessRequest.query.filter_by(
        id=request_id, owner_user_id=current_user.id,
    ).first()
    if not req:
        return jsonify({'error': 'Not found'}), 404
    if req.status == BioAccessRequest.STATUS_PENDING:
        req.status = BioAccessRequest.STATUS_EXPIRED
        req.resolved_at = datetime.utcnow()
        db.session.commit()
    return jsonify({'ok': True})


@bio_privacy_bp.route('/api/bio/access/grants/<grant_id>/revoke', methods=['POST'])
@login_required
def revoke(grant_id: str):
    ok = svc.revoke_grant(grant_id, current_user.id)
    if not ok:
        return jsonify({'error': 'Not found or already revoked'}), 404
    return jsonify({'ok': True})


# ── Owner: allow / block rules ───────────────────────────────────────────────

@bio_privacy_bp.route('/api/bio/access/rules', methods=['GET'])
@login_required
def list_rules():
    rules = BioAccessRule.query.filter_by(owner_user_id=current_user.id).order_by(
        BioAccessRule.created_at.desc()
    ).all()
    return jsonify({'rules': [r.to_dict() for r in rules]})


@bio_privacy_bp.route('/api/bio/access/rules', methods=['POST'])
@login_required
def add_rule():
    data = request.get_json(force=True, silent=True) or {}
    rule_type = data.get('rule_type')
    match_type = data.get('match_type')
    match_value = (data.get('match_value') or '').strip()[:200]
    if rule_type not in (BioAccessRule.RULE_ALLOW, BioAccessRule.RULE_BLOCK):
        return jsonify({'error': 'rule_type must be allow or block'}), 400
    if match_type not in (BioAccessRule.MATCH_DOMAIN, BioAccessRule.MATCH_COMPANY,
                          BioAccessRule.MATCH_LINKEDIN):
        return jsonify({'error': 'invalid match_type'}), 400
    if not match_value:
        return jsonify({'error': 'match_value required'}), 400

    rule = BioAccessRule(
        id=generate_id(),
        owner_user_id=current_user.id,
        rule_type=rule_type,
        match_type=match_type,
        match_value=match_value,
    )
    db.session.add(rule)
    db.session.commit()
    return jsonify(rule.to_dict()), 201


@bio_privacy_bp.route('/api/bio/access/rules/<rule_id>', methods=['DELETE'])
@login_required
def delete_rule(rule_id: str):
    rule = BioAccessRule.query.filter_by(id=rule_id, owner_user_id=current_user.id).first()
    if not rule:
        return jsonify({'error': 'Not found'}), 404
    db.session.delete(rule)
    db.session.commit()
    return jsonify({'ok': True})


# ── Owner: private-page analytics ────────────────────────────────────────────

@bio_privacy_bp.route('/api/bio/access/analytics', methods=['GET'])
@login_required
def access_analytics():
    return jsonify(svc.analytics(current_user.id))
