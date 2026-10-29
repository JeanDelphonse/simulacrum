"""Agent Execution Layer — plans, approvals and autonomy (EXEC_PLAN P3)."""
from flask import Blueprint

# Declares its own full paths (/api/artifacts/<id>/plan, /api/plan-steps/...,
# /api/agent-autonomy, /settings/agent-autonomy).
execution_bp = Blueprint('execution', __name__)

from app.blueprints.execution import routes  # noqa: E402,F401
