"""Careers — public job board + application intake, and the admin review panel."""
from flask import Blueprint

# Declares its own full paths (/api/careers/..., /api/admin/careers/...).
careers_bp = Blueprint('careers', __name__)

from app.blueprints.careers import routes  # noqa: E402,F401
