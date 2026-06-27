"""Add sandbox_email_routing flag to layer6_configs.

Revision ID: k1l2m3n4o5p6
Revises: j0k1l2m3n4o5
Create Date: 2026-06-26
"""
from alembic import op
import sqlalchemy as sa

revision = 'k1l2m3n4o5p6'
down_revision = 'j0k1l2m3n4o5'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('layer6_configs', sa.Column(
        'sandbox_email_routing', sa.Boolean(), nullable=False,
        server_default=sa.false(),
    ))


def downgrade():
    op.drop_column('layer6_configs', 'sandbox_email_routing')
