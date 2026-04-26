"""add agent_context table

Revision ID: f6a7b8c9d0e1
Revises: d4e5f6a7b8c9
Create Date: 2026-04-08

"""
from alembic import op
import sqlalchemy as sa

revision = 'f6a7b8c9d0e1'
down_revision = 'd4e5f6a7b8c9'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'agent_context',
        sa.Column('id', sa.String(9), primary_key=True, nullable=False),
        sa.Column('simulation_id', sa.String(9), sa.ForeignKey('simulations.id', ondelete='CASCADE'),
                  nullable=False, index=True),
        sa.Column('layer_number', sa.Integer, nullable=False, server_default='0'),
        sa.Column('context_key', sa.String(100), nullable=False),
        sa.Column('context_value', sa.Text, nullable=True),
        sa.Column('updated_at', sa.DateTime, nullable=False),
        sa.UniqueConstraint('simulation_id', 'layer_number', 'context_key',
                            name='uq_agent_context_sim_layer_key'),
    )


def downgrade():
    op.drop_table('agent_context')
