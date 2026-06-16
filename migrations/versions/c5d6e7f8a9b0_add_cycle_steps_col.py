"""Add cycle_steps column to layer6_cycles

Revision ID: c5d6e7f8a9b0
Revises: b4c5d6e7f8a9
Create Date: 2026-06-09
"""
from alembic import op
import sqlalchemy as sa

revision = 'c5d6e7f8a9b0'
down_revision = 'b4c5d6e7f8a9'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('layer6_cycles') as batch_op:
        batch_op.add_column(sa.Column('cycle_steps', sa.Text(), nullable=True))


def downgrade():
    with op.batch_alter_table('layer6_cycles') as batch_op:
        batch_op.drop_column('cycle_steps')
