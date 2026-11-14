"""Founder Track public startup pages: /startup/<slug> and its investor routes.

Declares its own full paths and is registered with no prefix, before pages_bp.
Pages render only while founder_feature.public_pages_enabled() (the
founder_track switch is not off); otherwise every route 404s.

The route modules are routes.py (WP6, public view) and investor.py (WP8,
Phase 3); each is imported when present.
"""
from __future__ import annotations

import importlib

from flask import Blueprint

startup_bp = Blueprint('startup', __name__)

for _name in ('routes', 'investor'):
    try:
        importlib.import_module('app.blueprints.startup.' + _name)
    except ModuleNotFoundError as _exc:
        if _exc.name != 'app.blueprints.startup.' + _name:
            raise
