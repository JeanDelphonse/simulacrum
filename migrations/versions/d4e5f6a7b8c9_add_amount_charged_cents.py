"""add amount_charged_cents to simulations

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-04-07 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = 'd4e5f6a7b8c9'
down_revision = 'c3d4e5f6a7b8'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('simulations', sa.Column('amount_charged_cents', sa.Integer(), nullable=True))


def downgrade():
    op.drop_column('simulations', 'amount_charged_cents')
