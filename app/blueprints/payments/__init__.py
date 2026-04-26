from flask import Blueprint

payments_bp = Blueprint('payments', __name__)

from app.blueprints.payments import routes  # noqa
