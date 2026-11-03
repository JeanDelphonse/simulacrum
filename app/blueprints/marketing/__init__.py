"""Public marketing pages for employers (the Transition Package page) and the
enterprise inquiry form."""
from flask import Blueprint

# Declares its own full paths: /transition-package, /enterprise/inquiry,
# /admin/inquiries, /api/admin/inquiries/...
marketing_bp = Blueprint('marketing', __name__)

from app.blueprints.marketing import routes  # noqa: E402,F401
