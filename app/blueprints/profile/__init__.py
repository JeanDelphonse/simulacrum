from flask import Blueprint

profile_bp = Blueprint('profile', __name__)

from app.blueprints.profile import routes  # noqa: E402,F401
