from flask import Blueprint

notifications_bp = Blueprint('notifications', __name__)

from app.blueprints.notifications import routes  # noqa: E402, F401
