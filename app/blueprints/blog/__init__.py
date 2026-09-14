"""Community Blog - PRD-SIM-BLOG-001. Posts, likes, comments, reports, moderation."""
from flask import Blueprint

# Declares its own full paths (/api/blog/..., /api/admin/blog/...).
blog_bp = Blueprint('blog', __name__)

from app.blueprints.blog import routes  # noqa: E402,F401
