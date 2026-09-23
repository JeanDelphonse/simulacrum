"""Agent Observability & Governance — PRD-SIM-OBS-001. Admin-only."""
from flask import Blueprint

# Declares its own full paths (/api/admin/observability/..., /api/admin/guardrails/...).
observability_bp = Blueprint('observability', __name__)

from app.blueprints.observability import routes  # noqa: E402,F401
