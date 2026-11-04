"""SME Console conversational guide (user-facing AI guide; not the human-SME /sme console)."""
from flask import Blueprint

# Declares its own full paths: /guide, /guide/history, /api/guide/..., /admin/guide, /api/admin/guide/...
guide_bp = Blueprint('guide', __name__)

from app.blueprints.guide import routes  # noqa: E402,F401
