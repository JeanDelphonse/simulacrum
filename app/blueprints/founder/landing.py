"""/founder: the Founder Track intro page (WP0). Later packages add the start
flow and the workspace."""
from __future__ import annotations

from flask import abort, render_template
from flask_login import current_user

from app.blueprints.founder import founder_bp


@founder_bp.route('/founder')
def intro():
    """Members the track is enabled for see their ventures. Signed-out visitors
    see the intro only once the switch is fully on (the public nav links here);
    otherwise 404, as before, so the track stays hidden."""
    from app.services import founder_config, founder_feature, founder_ventures
    if getattr(current_user, 'is_authenticated', False):
        if not founder_feature.is_enabled(current_user):
            abort(404)
        ventures = founder_ventures.ventures_for_user(current_user.id)
    else:
        if founder_feature.mode() != 'on':
            abort(404)
        ventures = []
    return render_template('founder/intro.html', ventures=ventures, pricing=founder_config.pricing(),
                           preview_level=founder_config.get('fnd_preview_level'), team=_team_by_layer(),
                           startups=_recent_startups())


def _recent_startups() -> list:
    """The same listed startups as the home page section."""
    try:
        from app.blueprints.pages.routes import _get_recent_startups
        return _get_recent_startups()
    except Exception:
        return []


def _team_by_layer() -> list:
    """The agent catalog by layer, for the 'Build the team' section."""
    try:
        from app.services import founder_registry
        names = founder_registry.LAYER_NAMES
        agents = founder_registry.all_agents()
    except Exception:
        return []
    out = []
    for n in sorted(names):
        row = [{'label': a.get('label'), 'description': a.get('description') or '',
                'external': (a.get('external') or 'none') != 'none'} for a in agents if int(a.get('layer') or 0) == n]
        out.append({'layer': n, 'name': names[n], 'agents': row})
    return out
