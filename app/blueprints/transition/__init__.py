"""Employer Transition Package (PRD-SIM-TRANS-001)."""
from flask import Blueprint

# Declares its own full paths: /admin/transition, /api/admin/transition/...,
# /transition/employer/<org_id>, /transition/join/<token>, /transition,
# /api/transition/..., /coach, /api/coach/...
transition_bp = Blueprint('transition', __name__)

from app.blueprints.transition import routes  # noqa: E402,F401
