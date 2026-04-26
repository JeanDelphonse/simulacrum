from flask import Blueprint

layer6_bp = Blueprint('layer6', __name__)

from app.blueprints.layer6 import routes  # noqa: F401,E402
