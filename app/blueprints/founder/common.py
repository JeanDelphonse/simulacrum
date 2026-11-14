"""Shared helpers for founder routes."""
from __future__ import annotations

from functools import wraps

from flask import abort
from flask_login import current_user


def founder_required(view=None, key: str = 'founder_track'):
    """404 unless the Founder Track (or the given sub-switch) is enabled for
    the current user. Anonymous users get 404 too, so the track stays hidden."""
    def deco(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            from app.services import founder_feature
            if not founder_feature.is_enabled(current_user, key):
                abort(404)
            return fn(*args, **kwargs)
        return wrapper
    return deco(view) if view is not None else deco


# Venture sections, in the order founders meet them. One list so every founder
# page links to every other one (endpoint, label, key).
VENTURE_SECTIONS = (
    ('workspace', 'Workspace', 'founder.workspace_page'),
    ('plan', 'Plan', 'founder.plan_page'),
    ('team', 'Team', 'founder.team_page'),
    ('report', 'Report', 'founder.report_page'),
    ('intake', 'Documents', 'founder.intake_page'),
    ('alignment', 'Alignment', 'founder.tp_alignment_page'),
    ('roles', 'Roles', 'founder.tp_roles_page'),
    ('proof', 'Proof of concept', 'founder.tp_proof_page'),
    ('page', 'Startup page', 'founder.page_editor'),
    ('support', 'Support access', 'founder.support_page'),
)


def venture_nav(venture) -> list:
    """[(key, label, href)] for the venture section tabs. An endpoint that isn't
    registered on this server is left out."""
    from flask import url_for
    from werkzeug.routing import BuildError
    out = []
    for key, label, endpoint in VENTURE_SECTIONS:
        try:
            out.append((key, label, url_for(endpoint, venture_id=venture.id)))
        except BuildError:
            continue
    return out
