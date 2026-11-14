"""/founder: the Founder Track intro page (WP0). Later packages add the start
flow and the workspace."""
from __future__ import annotations

from flask import render_template
from flask_login import current_user

from app.blueprints.founder import founder_bp
from app.blueprints.founder.common import founder_required


@founder_bp.route('/founder')
@founder_required
def intro():
    from app.services import founder_config, founder_ventures
    ventures = founder_ventures.ventures_for_user(current_user.id)
    return render_template('founder/intro.html', ventures=ventures, pricing=founder_config.pricing(),
                           preview_level=founder_config.get('fnd_preview_level'))
