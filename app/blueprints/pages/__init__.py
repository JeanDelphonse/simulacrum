from flask import Blueprint

pages_bp = Blueprint('pages', __name__)

from app.blueprints.pages import routes  # noqa
