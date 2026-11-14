"""Public startup page routes. WP0 placeholder: /startup/<slug> is gated and
returns 404 until WP6 (startup_page_service) replaces this module."""
from __future__ import annotations

from flask import abort

from app.blueprints.startup import startup_bp


@startup_bp.route('/startup/<slug>')
def public_page(slug):
    from app.services import founder_feature
    if not founder_feature.public_pages_enabled():
        abort(404)
    abort(404)       # no published pages until WP6
