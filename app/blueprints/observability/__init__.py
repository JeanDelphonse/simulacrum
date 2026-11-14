"""Agent Observability & Governance — PRD-SIM-OBS-001. Admin-only."""
from flask import Blueprint

# Declares its own full paths (/api/admin/observability/..., /api/admin/guardrails/...).
observability_bp = Blueprint('observability', __name__)

from app.blueprints.observability import routes  # noqa: E402,F401
# Founder Track tab (FOUNDER_PLAN.md WP7). Additive: its own module, its own
# /api/admin/observability/founder/... paths; nothing above changes.
from app.blueprints.observability import founder_routes  # noqa: E402,F401
