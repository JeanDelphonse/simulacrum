from flask import Blueprint

artifact_view_bp = Blueprint('artifact_view', __name__)

from app.blueprints.artifact_view import routes  # noqa
