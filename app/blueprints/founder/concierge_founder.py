"""Founder side of concierge onboarding (FOUNDER_CONCIERGE_PLAN.md 3.10-3.11).

  GET /founder/concierge/t/<token>     the invitation link: sign in first; only
                                       the verified primary-founder email gets
                                       through, to the preview. Anyone else gets
                                       the M6 page, with no venture data.

These routes are reached from the invitation, so they do not depend on the
founder_track switch: the invitation (and the verified email) is the access.
"""
from __future__ import annotations

import logging

from flask import redirect, render_template, request, url_for
from flask_login import current_user

from app.blueprints.founder import founder_bp
from app.extensions import db

logger = logging.getLogger(__name__)


def _gate(mode: str, code: int = 200, **ctx):
    return render_template('founder/concierge_gate.html', mode=mode, **ctx), code


@founder_bp.route('/founder/concierge/t/<token>')
def concierge_token(token):
    from app.models.audit_log import AuditLog
    from app.services import concierge_service as cs
    if not getattr(current_user, 'is_authenticated', False):
        # No venture data before sign-in, whatever the token.
        return _gate('signin', next_path=request.path)
    case = cs.case_for_token(token)
    if case is None or not cs.token_usable(case):
        return _gate('invalid', 410)
    if not cs.is_primary(current_user, case):
        reason = 'unverified' if cs.email_matches(current_user, case) else 'other_email'
        AuditLog.log('concierge_invite_refused', user_id=current_user.id, resource_id=case.venture_id,
                     metadata={'case': case.id, 'reason': reason, 'version': case.invite_version})
        db.session.commit()
        return _gate('mismatch', 403, unverified=reason == 'unverified')
    return redirect('/founder/concierge/%s' % case.id)
