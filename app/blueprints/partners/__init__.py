from flask import Blueprint

partners_bp = Blueprint('partners', __name__)

from app.blueprints.partners import routes  # noqa: F401, E402
