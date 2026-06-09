"""SIM-REQ-PROSPECT-001: add prospect_tier and prospect_tier_paid_cents to simulations

Revision ID: b4c5d6e7f8a9
Revises: a3b4c5d6e7f8
Create Date: 2026-06-09

"""
from alembic import op
import sqlalchemy as sa

revision = 'b4c5d6e7f8a9'
down_revision = 'a3b4c5d6e7f8'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('simulations') as batch_op:
        batch_op.add_column(sa.Column(
            'prospect_tier', sa.Integer(), nullable=False, server_default='1',
        ))
        batch_op.add_column(sa.Column(
            'prospect_tier_paid_cents', sa.Integer(), nullable=False, server_default='0',
        ))


def downgrade():
    with op.batch_alter_table('simulations') as batch_op:
        batch_op.drop_column('prospect_tier_paid_cents')
        batch_op.drop_column('prospect_tier')
