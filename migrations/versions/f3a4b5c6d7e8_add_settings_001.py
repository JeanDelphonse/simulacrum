"""SIM-PRD-SETTINGS-001: health monitoring, config, audit log, activity log.

Revision ID: f3a4b5c6d7e8
Revises: e2f3a4b5c6d7
Create Date: 2026-05-22

"""
from alembic import op
import sqlalchemy as sa

revision = 'f3a4b5c6d7e8'
down_revision = 'e2f3a4b5c6d7'
branch_labels = None
depends_on = None


def upgrade():
    # ── user_integrations: health + config fields ────────────────────────────
    with op.batch_alter_table('user_integrations') as batch_op:
        batch_op.add_column(sa.Column('config', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('health_status', sa.String(20), nullable=False,
                                      server_default='healthy'))
        batch_op.add_column(sa.Column('last_api_success_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('last_api_failure_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('consecutive_failures', sa.SmallInteger(),
                                      nullable=False, server_default='0'))
        batch_op.add_column(sa.Column('connected_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('disconnected_at', sa.DateTime(), nullable=True))

    # ── integration_audit_log ────────────────────────────────────────────────
    op.create_table(
        'integration_audit_log',
        sa.Column('id', sa.String(9), nullable=False),
        sa.Column('admin_user_id', sa.String(9), nullable=False),
        sa.Column('target_user_id', sa.String(9), nullable=False),
        sa.Column('integration_type', sa.String(30), nullable=False),
        sa.Column('action', sa.String(50), nullable=False),
        sa.Column('changes', sa.Text(), nullable=True),
        sa.Column('approved_by', sa.String(9), nullable=True),
        sa.Column('ip_address', sa.String(50), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False,
                  server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_ial_target', 'integration_audit_log', ['target_user_id'])
    op.create_index('idx_ial_admin', 'integration_audit_log', ['admin_user_id'])

    # ── integration_activity_log ─────────────────────────────────────────────
    op.create_table(
        'integration_activity_log',
        sa.Column('id', sa.String(9), nullable=False),
        sa.Column('user_id', sa.String(9), nullable=False),
        sa.Column('provider', sa.String(50), nullable=False),
        sa.Column('event_type', sa.String(80), nullable=False),
        sa.Column('direction', sa.String(10), nullable=False, server_default='outbound'),
        sa.Column('status', sa.String(20), nullable=False, server_default='success'),
        sa.Column('detail', sa.String(500), nullable=True),
        sa.Column('action_id', sa.String(9), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False,
                  server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_ial2_user_provider',
                    'integration_activity_log', ['user_id', 'provider'])


def downgrade():
    op.drop_index('idx_ial2_user_provider', 'integration_activity_log')
    op.drop_table('integration_activity_log')
    op.drop_index('idx_ial_admin', 'integration_audit_log')
    op.drop_index('idx_ial_target', 'integration_audit_log')
    op.drop_table('integration_audit_log')
    with op.batch_alter_table('user_integrations') as batch_op:
        batch_op.drop_column('disconnected_at')
        batch_op.drop_column('connected_at')
        batch_op.drop_column('consecutive_failures')
        batch_op.drop_column('last_api_failure_at')
        batch_op.drop_column('last_api_success_at')
        batch_op.drop_column('health_status')
        batch_op.drop_column('config')
