from flask import Blueprint

calibration_bp = Blueprint('calibration', __name__)

from app.blueprints.calibration import routes  # noqa
