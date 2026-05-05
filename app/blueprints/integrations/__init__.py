from flask import Blueprint

integrations_bp = Blueprint('integrations', __name__)

from app.blueprints.integrations import routes  # noqa: F401, E402
