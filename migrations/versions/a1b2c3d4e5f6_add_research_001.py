"""SIM-PRD-RESEARCH-001: prospect_research_runs audit table.

Revision ID: a1b2c3d4e5f6
Revises: f3a4b5c6d7e8
Create Date: 2026-05-22

"""
from alembic import op
import sqlalchemy as sa

revision      = 'a1b2c3d4e5f6'
down_revision = 'f3a4b5c6d7e8'
branch_labels = None
depends_on    = None


def upgrade():
    op.create_table(
        'prospect_research_runs',
        sa.Column('id',                      sa.String(9),       nullable=False),
        sa.Column('simulation_id',           sa.String(9),       nullable=False),
        sa.Column('user_id',                 sa.String(9),       nullable=False),
        sa.Column('action_id',               sa.String(9),       nullable=False),
        sa.Column('calling_agent',           sa.String(50),      nullable=False),
        sa.Column('targeting_criteria',      sa.Text(),          nullable=False),
        sa.Column('sources_used',            sa.Text(),          nullable=False),
        sa.Column('total_researched',        sa.SmallInteger(),  nullable=False, server_default='0'),
        sa.Column('total_from_apollo',       sa.SmallInteger(),  nullable=False, server_default='0'),
        sa.Column('total_from_web',          sa.SmallInteger(),  nullable=False, server_default='0'),
        sa.Column('total_from_crm',          sa.SmallInteger(),  nullable=False, server_default='0'),
        sa.Column('total_verified',          sa.SmallInteger(),  nullable=False, server_default='0'),
        sa.Column('total_discarded_invalid', sa.SmallInteger(),  nullable=False, server_default='0'),
        sa.Column('total_risky',             sa.SmallInteger(),  nullable=False, server_default='0'),
        sa.Column('verification_cost_cents', sa.Integer(),       nullable=False, server_default='0'),
        sa.Column('apollo_api_calls',        sa.SmallInteger(),  nullable=False, server_default='0'),
        sa.Column('web_search_calls',        sa.SmallInteger(),  nullable=False, server_default='0'),
        sa.Column('extraction_calls',        sa.SmallInteger(),  nullable=False, server_default='0'),
        sa.Column('duration_seconds',        sa.Numeric(6, 2),   nullable=False, server_default='0'),
        sa.Column('created_at',              sa.DateTime(),      nullable=False,
                  server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('prospect_research_runs') as batch_op:
        batch_op.create_index('ix_prr_simulation_id', ['simulation_id'])
        batch_op.create_index('ix_prr_user_id',       ['user_id'])
        batch_op.create_index('ix_prr_action_id',     ['action_id'])


def downgrade():
    with op.batch_alter_table('prospect_research_runs') as batch_op:
        batch_op.drop_index('ix_prr_action_id')
        batch_op.drop_index('ix_prr_user_id')
        batch_op.drop_index('ix_prr_simulation_id')
    op.drop_table('prospect_research_runs')
