"""SIM-PRD-PRIVACY-001 — Bio Page Private Mode blueprint."""
from flask import Blueprint

bio_privacy_bp = Blueprint('bio_privacy', __name__)

from app.blueprints.bio_privacy import routes  # noqa: E402,F401
