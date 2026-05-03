from flask import Blueprint

advisor_bp = Blueprint('advisor', __name__)

from app.blueprints.advisor import routes  # noqa: F401, E402
