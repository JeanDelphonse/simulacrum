from flask import Blueprint

collaboration_bp = Blueprint('collaboration', __name__)

from app.blueprints.collaboration import routes  # noqa
