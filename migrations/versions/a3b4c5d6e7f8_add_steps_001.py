"""SIM-PRD-STEPS-001: email_logs, email_suppressions, action_steps tables + contacts.outreach_count

Revision ID: a3b4c5d6e7f8
Revises: f5a6b7c8d9e0
Create Date: 2026-06-08

"""
from alembic import op
import sqlalchemy as sa

revision = 'a3b4c5d6e7f8'
down_revision = 'f5a6b7c8d9e0'
branch_labels = None
depends_on = None


def upgrade():
    # ── email_suppressions ────────────────────────────────────────────────────
    op.create_table(
        'email_suppressions',
        sa.Column('id',         sa.String(9),   nullable=False),
        sa.Column('email',      sa.String(255), nullable=False),
        sa.Column('reason',     sa.String(20),  nullable=False),
        sa.Column('detail',     sa.String(500), nullable=True),
        sa.Column('created_at', sa.DateTime(),  nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('email', name='uq_email_suppression_email'),
    )

    # ── action_steps ─────────────────────────────────────────────────────────
    op.create_table(
        'action_steps',
        sa.Column('id',               sa.String(9),    nullable=False),
        sa.Column('agent_action_id',  sa.String(9),    nullable=False),
        sa.Column('parent_action_id', sa.String(9),    nullable=True),
        sa.Column('simulation_id',    sa.String(9),    nullable=False),
        sa.Column('step_number',      sa.Integer(),    nullable=False),
        sa.Column('total_steps',      sa.Integer(),    nullable=False),
        sa.Column('action_type',      sa.String(50),   nullable=False),
        sa.Column('step_type',        sa.String(50),   nullable=False),
        sa.Column('subject',          sa.String(255),  nullable=True),
        sa.Column('payload',          sa.Text(),       nullable=False),
        sa.Column('scheduled_for',    sa.DateTime(),   nullable=False),
        sa.Column('condition_type',   sa.String(30),   nullable=True),
        sa.Column('condition_ref',    sa.String(9),    nullable=True),
        sa.Column('status',           sa.String(20),   nullable=False),
        sa.Column('executed_at',      sa.DateTime(),   nullable=True),
        sa.Column('skipped_at',       sa.DateTime(),   nullable=True),
        sa.Column('skip_reason',      sa.String(200),  nullable=True),
        sa.Column('created_at',       sa.DateTime(),   nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['agent_action_id'],  ['agent_actions.id'],        ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['parent_action_id'], ['layer6_action_queue.id'],  ondelete='SET NULL'),
        sa.UniqueConstraint('agent_action_id', 'step_number', name='uq_step_action_num'),
    )
    with op.batch_alter_table('action_steps') as batch_op:
        batch_op.create_index('ix_action_steps_agent_action_id',  ['agent_action_id'],  unique=False)
        batch_op.create_index('ix_action_steps_parent_action_id', ['parent_action_id'], unique=False)
        batch_op.create_index('ix_action_steps_simulation_id',    ['simulation_id'],    unique=False)
        batch_op.create_index('ix_action_steps_scheduled_for',    ['scheduled_for'],    unique=False)
        batch_op.create_index('ix_action_steps_status',           ['status'],           unique=False)

    # ── email_logs ────────────────────────────────────────────────────────────
    op.create_table(
        'email_logs',
        sa.Column('id',                  sa.String(9),   nullable=False),
        sa.Column('simulation_id',       sa.String(9),   nullable=False),
        sa.Column('contact_id',          sa.String(9),   nullable=False),
        sa.Column('step_id',             sa.String(9),   nullable=True),
        sa.Column('action_id',           sa.String(9),   nullable=True),
        sa.Column('subject',             sa.String(255), nullable=False),
        sa.Column('from_email',          sa.String(255), nullable=False),
        sa.Column('from_name',           sa.String(255), nullable=False),
        sa.Column('to_email',            sa.String(255), nullable=False),
        sa.Column('provider_message_id', sa.String(100), nullable=True),
        sa.Column('status',              sa.String(20),  nullable=False),
        sa.Column('sent_at',             sa.DateTime(),  nullable=True),
        sa.Column('delivered_at',        sa.DateTime(),  nullable=True),
        sa.Column('opened_at',           sa.DateTime(),  nullable=True),
        sa.Column('open_count',          sa.Integer(),   nullable=False),
        sa.Column('clicked_at',          sa.DateTime(),  nullable=True),
        sa.Column('click_count',         sa.Integer(),   nullable=False),
        sa.Column('replied_at',          sa.DateTime(),  nullable=True),
        sa.Column('bounced_at',          sa.DateTime(),  nullable=True),
        sa.Column('bounce_reason',       sa.String(500), nullable=True),
        sa.Column('created_at',          sa.DateTime(),  nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['contact_id'], ['contacts.id'],      ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['step_id'],    ['action_steps.id'],  ondelete='SET NULL'),
        sa.UniqueConstraint('provider_message_id', name='uq_email_log_provider_msg_id'),
    )
    with op.batch_alter_table('email_logs') as batch_op:
        batch_op.create_index('ix_email_logs_simulation_id', ['simulation_id'], unique=False)
        batch_op.create_index('ix_email_logs_contact_id',    ['contact_id'],    unique=False)
        batch_op.create_index('ix_email_logs_step_id',       ['step_id'],       unique=False)
        batch_op.create_index('ix_email_logs_action_id',     ['action_id'],     unique=False)

    # ── contacts.outreach_count ───────────────────────────────────────────────
    op.add_column('contacts',
        sa.Column('outreach_count', sa.Integer(), nullable=False, server_default='0'))


def downgrade():
    op.drop_column('contacts', 'outreach_count')
    with op.batch_alter_table('email_logs') as batch_op:
        batch_op.drop_index('ix_email_logs_action_id')
        batch_op.drop_index('ix_email_logs_step_id')
        batch_op.drop_index('ix_email_logs_contact_id')
        batch_op.drop_index('ix_email_logs_simulation_id')
    op.drop_table('email_logs')
    with op.batch_alter_table('action_steps') as batch_op:
        batch_op.drop_index('ix_action_steps_status')
        batch_op.drop_index('ix_action_steps_scheduled_for')
        batch_op.drop_index('ix_action_steps_simulation_id')
        batch_op.drop_index('ix_action_steps_parent_action_id')
        batch_op.drop_index('ix_action_steps_agent_action_id')
    op.drop_table('action_steps')
    op.drop_table('email_suppressions')
