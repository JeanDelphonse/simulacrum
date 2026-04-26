"""add agent_actions

Revision ID: b2c3d4e5f6a7
Revises: 0f24a06803ea
Create Date: 2026-04-08

"""
from alembic import op
import sqlalchemy as sa

revision = 'b2c3d4e5f6a7'
down_revision = '0f24a06803ea'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'agent_actions',
        sa.Column('id', sa.String(9), primary_key=True),
        sa.Column('simulation_id', sa.String(9),
                  sa.ForeignKey('simulations.id', ondelete='CASCADE'), nullable=False),
        sa.Column('layer_number', sa.Integer(), nullable=False),
        sa.Column('action_type', sa.String(50), nullable=False),
        sa.Column('user_inputs', sa.Text(), nullable=True),
        sa.Column('artifact', sa.Text(), nullable=True),
        sa.Column('archived_artifact', sa.Text(), nullable=True),
        sa.Column('archived_at', sa.DateTime(), nullable=True),
        sa.Column('status', sa.String(20), nullable=False, server_default='pending'),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('created_by', sa.String(9),
                  sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('completed_at', sa.DateTime(), nullable=True),
    )
    op.create_index('ix_agent_actions_simulation_id', 'agent_actions', ['simulation_id'])
    op.create_index('ix_agent_actions_layer', 'agent_actions', ['simulation_id', 'layer_number'])


def downgrade():
    op.drop_index('ix_agent_actions_layer', table_name='agent_actions')
    op.drop_index('ix_agent_actions_simulation_id', table_name='agent_actions')
    op.drop_table('agent_actions')
