from flask import Blueprint

bio_bp = Blueprint('bio', __name__)

from app.blueprints.bio import routes  # noqa: F401, E402
