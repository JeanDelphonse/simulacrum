from flask import Blueprint

feedback_bp = Blueprint('feedback', __name__)

from app.blueprints.feedback import routes  # noqa
