from flask import Blueprint
publishing_bp = Blueprint('publishing', __name__)
from app.blueprints.publishing import routes  # noqa: E402, F401
