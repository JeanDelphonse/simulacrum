"""Peer Calibration — PRD-SIM-PEER-001."""
from flask import Blueprint

# Declares its own full paths (/api/simulations/..., /api/peers/..., /api/u/...,
# /api/blends/..., /api/admin/peer/...).
peer_bp = Blueprint('peer', __name__)

from app.blueprints.peer import routes  # noqa: E402,F401
