from flask import Blueprint

social_bp = Blueprint('social', __name__)

from app.blueprints.social import routes  # noqa: F401, E402
