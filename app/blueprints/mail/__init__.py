"""Email-to-CRM contact extraction (PRD-SIM-MAIL-001)."""
from flask import Blueprint

# Declares its own full paths (/settings/email-import, /api/mail/..., /mail/connect/...,
# /admin/email-crm, /api/admin/email-crm/...).
mail_bp = Blueprint('mail', __name__)

from app.blueprints.mail import routes  # noqa: E402,F401
