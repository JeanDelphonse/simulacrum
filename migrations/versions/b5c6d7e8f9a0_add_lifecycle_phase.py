"""Add lifecycle phase to simulations and lifecycle config fields to layer6_configs

Revision ID: b5c6d7e8f9a0
Revises: a7b8c9d0e1f2
Create Date: 2026-06-21

"""
from alembic import op
import sqlalchemy as sa

revision = 'b5c6d7e8f9a0'
down_revision = 'a7b8c9d0e1f2'
branch_labels = None
depends_on = None


def upgrade():
    # simulations.lifecycle_phase
    op.add_column('simulations',
        sa.Column('lifecycle_phase', sa.String(15), nullable=False, server_default='active'))

    # layer6_configs lifecycle fields
    op.add_column('layer6_configs',
        sa.Column('active_cycle_limit', sa.Integer, nullable=False, server_default='30'))
    op.add_column('layer6_configs',
        sa.Column('maintenance_frequency_hours', sa.Integer, nullable=False, server_default='168'))
    op.add_column('layer6_configs',
        sa.Column('convergence_delta', sa.Numeric(6, 4), nullable=False, server_default='0.0200'))
    op.add_column('layer6_configs',
        sa.Column('convergence_consecutive', sa.Integer, nullable=False, server_default='3'))
    op.add_column('layer6_configs',
        sa.Column('convergence_min_cycles', sa.Integer, nullable=False, server_default='15'))
    op.add_column('layer6_configs',
        sa.Column('maintenance_dispatch_threshold', sa.Numeric(4, 2), nullable=False, server_default='0.70'))

    # action_steps: suspended status is just a string value, no DDL change needed


def downgrade():
    op.drop_column('simulations', 'lifecycle_phase')
    op.drop_column('layer6_configs', 'active_cycle_limit')
    op.drop_column('layer6_configs', 'maintenance_frequency_hours')
    op.drop_column('layer6_configs', 'convergence_delta')
    op.drop_column('layer6_configs', 'convergence_consecutive')
    op.drop_column('layer6_configs', 'convergence_min_cycles')
    op.drop_column('layer6_configs', 'maintenance_dispatch_threshold')
