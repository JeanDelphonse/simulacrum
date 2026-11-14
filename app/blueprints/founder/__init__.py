"""Founder Track (SIM-PRD-FND-001): founder-facing pages and APIs.

Declares its own full paths (/founder/..., /api/founder/..., /admin/founder,
/api/admin/founder/...) and is registered with no prefix, before pages_bp.

Every route checks founder_feature.is_enabled(current_user) and 404s when the
track is off for that user (use common.founder_required).

Area modules (FOUNDER_PLAN.md section 8) are imported below when present, so a
work package adds its module without editing this file.
"""
from __future__ import annotations

import importlib

from flask import Blueprint

founder_bp = Blueprint('founder', __name__)

from app.blueprints.founder import landing  # noqa: E402,F401

_AREA_MODULES = ('intake', 'report', 'admin', 'billing', 'workspace', 'team_proof', 'page_editor', 'support', 'investors')

for _name in _AREA_MODULES:
    try:
        importlib.import_module('app.blueprints.founder.' + _name)
    except ModuleNotFoundError as _exc:
        if _exc.name != 'app.blueprints.founder.' + _name:
            raise          # the module exists but one of its imports is missing
