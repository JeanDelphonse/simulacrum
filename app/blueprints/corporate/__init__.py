from flask import Blueprint

corporate_bp = Blueprint('corporate', __name__)

from app.blueprints.corporate import routes  # noqa: F401, E402
