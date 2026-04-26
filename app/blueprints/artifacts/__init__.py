from flask import Blueprint

artifacts_bp = Blueprint('artifacts', __name__)

from app.blueprints.artifacts import routes  # noqa: E402, F401
