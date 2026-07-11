from flask import Blueprint

# SME console (advisor side) — /api/sme
sme_bp = Blueprint('sme', __name__)
# User side of the advisory loop — /api/sme-advisor
sme_user_bp = Blueprint('sme_user', __name__)

from app.blueprints.sme import routes  # noqa: F401, E402
