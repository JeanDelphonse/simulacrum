from flask import Blueprint

resumes_bp = Blueprint('resumes', __name__)

from app.blueprints.resumes import routes  # noqa
