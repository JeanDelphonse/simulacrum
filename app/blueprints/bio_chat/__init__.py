from flask import Blueprint

bio_chat_bp = Blueprint('bio_chat', __name__)

from app.blueprints.bio_chat import routes  # noqa: F401, E402
