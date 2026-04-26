"""add layer6 tables

Revision ID: e5f6a7b8c9d0
Revises: b3c4d5e6f7a8
Create Date: 2026-04-17

"""
from alembic import op
import sqlalchemy as sa

revision = 'e5f6a7b8c9d0'
down_revision = 'b3c4d5e6f7a8'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'layer6_configs',
        sa.Column('id', sa.String(9), primary_key=True),
        sa.Column('simulation_id', sa.String(9),
                  sa.ForeignKey('simulations.id', ondelete='CASCADE'),
                  nullable=False, unique=True),
        sa.Column('channel_approvals', sa.Text(), nullable=True),
        sa.Column('spend_ceiling', sa.Numeric(10, 2), nullable=False, server_default='0'),
        sa.Column('contact_scope', sa.String(30), nullable=False, server_default='uploaded_only'),
        sa.Column('blocked_actions', sa.Text(), nullable=True),
        sa.Column('cadence', sa.String(20), nullable=False, server_default='daily'),
        sa.Column('actions_per_cycle', sa.Integer(), nullable=False, server_default='3'),
        sa.Column('quiet_hours', sa.Text(), nullable=True),
        sa.Column('explore_phase_end_month', sa.Integer(), nullable=False, server_default='3'),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='1'),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
    )
    op.create_index('ix_layer6_configs_simulation_id', 'layer6_configs', ['simulation_id'])

    op.create_table(
        'layer6_cycles',
        sa.Column('id', sa.String(9), primary_key=True),
        sa.Column('simulation_id', sa.String(9),
                  sa.ForeignKey('simulations.id', ondelete='CASCADE'), nullable=False),
        sa.Column('cycle_number', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('phase', sa.String(10), nullable=False, server_default='explore'),
        sa.Column('actions_scored', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('actions_dispatched', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('actions_escalated', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('orchestrator_reasoning', sa.Text(), nullable=True),
        sa.Column('cycle_started_at', sa.DateTime(), nullable=False),
        sa.Column('cycle_completed_at', sa.DateTime(), nullable=True),
    )
    op.create_index('ix_layer6_cycles_simulation_id', 'layer6_cycles', ['simulation_id'])

    op.create_table(
        'layer6_action_queue',
        sa.Column('id', sa.String(9), primary_key=True),
        sa.Column('simulation_id', sa.String(9),
                  sa.ForeignKey('simulations.id', ondelete='CASCADE'), nullable=False),
        sa.Column('cycle_id', sa.String(9),
                  sa.ForeignKey('layer6_cycles.id', ondelete='CASCADE'), nullable=False),
        sa.Column('source_layer', sa.Integer(), nullable=False),
        sa.Column('action_type', sa.String(100), nullable=False),
        sa.Column('priority_score', sa.Numeric(10, 6), nullable=False, server_default='0'),
        sa.Column('dependency_ids', sa.Text(), nullable=True),
        sa.Column('escalation_reason', sa.Text(), nullable=True),
        sa.Column('status', sa.String(20), nullable=False, server_default='queued'),
        sa.Column('agent_action_id', sa.String(9),
                  sa.ForeignKey('agent_actions.id', ondelete='SET NULL'), nullable=True),
        sa.Column('dispatched_at', sa.DateTime(), nullable=True),
        sa.Column('completed_at', sa.DateTime(), nullable=True),
        sa.Column('outcome_summary', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
    )
    op.create_index('ix_layer6_action_queue_simulation_id', 'layer6_action_queue', ['simulation_id'])
    op.create_index('ix_layer6_action_queue_cycle_id', 'layer6_action_queue', ['cycle_id'])

    op.create_table(
        'layer6_outcomes',
        sa.Column('id', sa.String(9), primary_key=True),
        sa.Column('simulation_id', sa.String(9),
                  sa.ForeignKey('simulations.id', ondelete='CASCADE'), nullable=False),
        sa.Column('layer_number', sa.Integer(), nullable=False),
        sa.Column('income_stream_id', sa.String(9),
                  sa.ForeignKey('income_streams.id', ondelete='SET NULL'), nullable=True),
        sa.Column('reporting_month', sa.String(7), nullable=False),
        sa.Column('actual_income', sa.Numeric(12, 2), nullable=False, server_default='0'),
        sa.Column('projected_income', sa.Numeric(12, 2), nullable=False, server_default='0'),
        sa.Column('variance', sa.Numeric(12, 2), nullable=False, server_default='0'),
        sa.Column('reported_by', sa.String(20), nullable=False, server_default='user'),
        sa.Column('integration_source', sa.String(50), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
    )
    op.create_index('ix_layer6_outcomes_simulation_id', 'layer6_outcomes', ['simulation_id'])
    op.create_index('ix_layer6_outcomes_month', 'layer6_outcomes', ['simulation_id', 'reporting_month'])

    op.create_table(
        'layer6_momentum',
        sa.Column('id', sa.String(9), primary_key=True),
        sa.Column('simulation_id', sa.String(9),
                  sa.ForeignKey('simulations.id', ondelete='CASCADE'), nullable=False),
        sa.Column('snapshot_date', sa.Date(), nullable=False),
        sa.Column('email_list_size', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('linkedin_connections', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('course_enrollments', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('funnel_opt_in_rate', sa.Numeric(5, 4), nullable=False, server_default='0'),
        sa.Column('seo_organic_sessions', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('newsletter_subscribers', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('pipeline_value', sa.Numeric(12, 2), nullable=False, server_default='0'),
        sa.Column('investment_balance', sa.Numeric(12, 2), nullable=False, server_default='0'),
        sa.Column('consulting_bookings_mo', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(), nullable=False),
    )
    op.create_index('ix_layer6_momentum_simulation_id', 'layer6_momentum', ['simulation_id'])

    op.create_table(
        'layer6_execution_log',
        sa.Column('id', sa.String(9), primary_key=True),
        sa.Column('simulation_id', sa.String(9),
                  sa.ForeignKey('simulations.id', ondelete='CASCADE'), nullable=False),
        sa.Column('cycle_id', sa.String(9),
                  sa.ForeignKey('layer6_cycles.id', ondelete='SET NULL'), nullable=True),
        sa.Column('action_id', sa.String(9),
                  sa.ForeignKey('layer6_action_queue.id', ondelete='SET NULL'), nullable=True),
        sa.Column('event_type', sa.String(30), nullable=False),
        sa.Column('actor', sa.String(20), nullable=False, server_default='orchestrator'),
        sa.Column('reasoning', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
    )
    op.create_index('ix_layer6_execution_log_simulation_id', 'layer6_execution_log', ['simulation_id'])


def downgrade():
    op.drop_index('ix_layer6_execution_log_simulation_id', table_name='layer6_execution_log')
    op.drop_table('layer6_execution_log')
    op.drop_index('ix_layer6_momentum_simulation_id', table_name='layer6_momentum')
    op.drop_table('layer6_momentum')
    op.drop_index('ix_layer6_outcomes_month', table_name='layer6_outcomes')
    op.drop_index('ix_layer6_outcomes_simulation_id', table_name='layer6_outcomes')
    op.drop_table('layer6_outcomes')
    op.drop_index('ix_layer6_action_queue_cycle_id', table_name='layer6_action_queue')
    op.drop_index('ix_layer6_action_queue_simulation_id', table_name='layer6_action_queue')
    op.drop_table('layer6_action_queue')
    op.drop_index('ix_layer6_cycles_simulation_id', table_name='layer6_cycles')
    op.drop_table('layer6_cycles')
    op.drop_index('ix_layer6_configs_simulation_id', table_name='layer6_configs')
    op.drop_table('layer6_configs')
