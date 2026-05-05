from flask import Blueprint

contacts_bp = Blueprint('contacts', __name__)

from app.blueprints.contacts import routes  # noqa
