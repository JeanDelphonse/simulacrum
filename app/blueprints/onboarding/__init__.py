from flask import Blueprint

onboarding_bp = Blueprint('onboarding', __name__)

from app.blueprints.onboarding import routes  # noqa: F401, E402
