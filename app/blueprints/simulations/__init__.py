from flask import Blueprint

simulations_bp = Blueprint('simulations', __name__)

from app.blueprints.simulations import routes  # noqa
