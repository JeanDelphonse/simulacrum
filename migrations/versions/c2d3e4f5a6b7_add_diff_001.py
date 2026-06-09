"""add_diff_001 — cycle_posterior_snapshots + contacts.source_cycle_id

Revision ID: c2d3e4f5a6b7
Revises: a2b3c4d5e6f7
Create Date: 2026-06-05
"""
from alembic import op
import sqlalchemy as sa

revision = 'c2d3e4f5a6b7'
down_revision = 'a2b3c4d5e6f7'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('contacts', sa.Column('source_cycle_id', sa.String(9), nullable=True))
    op.create_index('ix_contacts_source_cycle_id', 'contacts', ['source_cycle_id'])

    op.create_table(
        'cycle_posterior_snapshots',
        sa.Column('id', sa.String(9), primary_key=True),
        sa.Column('cycle_id', sa.String(9), sa.ForeignKey('layer6_cycles.id', ondelete='CASCADE'), nullable=False),
        sa.Column('simulation_id', sa.String(9), nullable=False),
        sa.Column('action_type', sa.String(100), nullable=False),
        sa.Column('posterior_value', sa.Numeric(10, 6), nullable=False),
        sa.Column('created_at', sa.DateTime, nullable=False),
        sa.UniqueConstraint('cycle_id', 'action_type', name='uq_cps_cycle_action'),
    )
    op.create_index('ix_cps_cycle_id', 'cycle_posterior_snapshots', ['cycle_id'])
    op.create_index('ix_cps_simulation_id', 'cycle_posterior_snapshots', ['simulation_id'])


def downgrade():
    op.drop_table('cycle_posterior_snapshots')
    op.drop_index('ix_contacts_source_cycle_id', table_name='contacts')
    op.drop_column('contacts', 'source_cycle_id')
