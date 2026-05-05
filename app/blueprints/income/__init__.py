from flask import Blueprint

income_bp = Blueprint('income', __name__)

from app.blueprints.income import routes  # noqa
