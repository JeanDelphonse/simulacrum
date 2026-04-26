"""initial

Revision ID: 0f24a06803ea
Revises:
Create Date: 2026-04-06

"""
from alembic import op
import sqlalchemy as sa

revision = '0f24a06803ea'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # users
    op.create_table(
        'users',
        sa.Column('id', sa.String(9), primary_key=True),
        sa.Column('email', sa.String(255), nullable=False, unique=True),
        sa.Column('password_hash', sa.String(255), nullable=True),
        sa.Column('full_name', sa.String(255), nullable=False),
        sa.Column('google_id', sa.String(255), nullable=True, unique=True),
        sa.Column('email_verified', sa.Boolean(), nullable=False, server_default='0'),
        sa.Column('email_verify_token', sa.String(255), nullable=True),
        sa.Column('password_reset_token', sa.String(255), nullable=True),
        sa.Column('password_reset_expires', sa.DateTime(), nullable=True),
        sa.Column('simulation_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('total_spend', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('is_admin', sa.Boolean(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
    )
    op.create_index('ix_users_email', 'users', ['email'])

    # resumes
    op.create_table(
        'resumes',
        sa.Column('id', sa.String(9), primary_key=True),
        sa.Column('user_id', sa.String(9), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('label', sa.String(255), nullable=False),
        sa.Column('file_path', sa.String(500), nullable=True),
        sa.Column('file_type', sa.String(10), nullable=True),
        sa.Column('source', sa.String(20), nullable=False, server_default='upload'),
        sa.Column('parsed_text', sa.Text(), nullable=True),
        sa.Column('expertise_zones', sa.Text(), nullable=True),
        sa.Column('linkedin_access_token_enc', sa.Text(), nullable=True),
        sa.Column('linkedin_profile_url', sa.String(500), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
    )
    op.create_index('ix_resumes_user_id', 'resumes', ['user_id'])

    # simulations
    op.create_table(
        'simulations',
        sa.Column('id', sa.String(9), primary_key=True),
        sa.Column('user_id', sa.String(9), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('resume_id', sa.String(9), sa.ForeignKey('resumes.id', ondelete='SET NULL'), nullable=True),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('focus_hint', sa.Text(), nullable=True),
        sa.Column('expertise_zone', sa.String(500), nullable=True),
        sa.Column('status', sa.String(20), nullable=False, server_default='pending'),
        sa.Column('stripe_payment_intent_id', sa.String(255), nullable=True),
        sa.Column('stripe_charge_id', sa.String(255), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
    )
    op.create_index('ix_simulations_user_id', 'simulations', ['user_id'])
    op.create_index('ix_simulations_resume_id', 'simulations', ['resume_id'])

    # simulation_layers
    op.create_table(
        'simulation_layers',
        sa.Column('id', sa.String(9), primary_key=True),
        sa.Column('simulation_id', sa.String(9), sa.ForeignKey('simulations.id', ondelete='CASCADE'), nullable=False),
        sa.Column('layer_number', sa.Integer(), nullable=False),
        sa.Column('layer_name', sa.String(255), nullable=False),
        sa.Column('income_type', sa.String(100), nullable=True),
        sa.Column('ai_narrative', sa.Text(), nullable=True),
        sa.Column('priority_score', sa.Float(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
    )
    op.create_index('ix_simulation_layers_simulation_id', 'simulation_layers', ['simulation_id'])

    # income_streams
    op.create_table(
        'income_streams',
        sa.Column('id', sa.String(9), primary_key=True),
        sa.Column('layer_id', sa.String(9), sa.ForeignKey('simulation_layers.id', ondelete='CASCADE'), nullable=False),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('platform', sa.String(255), nullable=True),
        sa.Column('est_monthly_low', sa.Integer(), nullable=True),
        sa.Column('est_monthly_high', sa.Integer(), nullable=True),
        sa.Column('ai_reasoning', sa.Text(), nullable=False),
        sa.Column('deliverable_refs', sa.Text(), nullable=True),
        sa.Column('automation_level', sa.String(50), nullable=True),
        sa.Column('launch_timeline_weeks', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
    )
    op.create_index('ix_income_streams_layer_id', 'income_streams', ['layer_id'])

    # collaborations
    op.create_table(
        'collaborations',
        sa.Column('id', sa.String(9), primary_key=True),
        sa.Column('simulation_id', sa.String(9), sa.ForeignKey('simulations.id', ondelete='CASCADE'), nullable=False),
        sa.Column('invitee_email', sa.String(255), nullable=False),
        sa.Column('invitee_id', sa.String(9), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('permission_level', sa.String(20), nullable=False, server_default='viewer'),
        sa.Column('share_token', sa.String(64), nullable=False, unique=True),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('accepted_at', sa.DateTime(), nullable=True),
        sa.Column('revoked_at', sa.DateTime(), nullable=True),
        sa.Column('created_by', sa.String(9), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
    )
    op.create_index('ix_collaborations_simulation_id', 'collaborations', ['simulation_id'])
    op.create_index('ix_collaborations_invitee_email', 'collaborations', ['invitee_email'])

    # collab_activities
    op.create_table(
        'collab_activities',
        sa.Column('id', sa.String(9), primary_key=True),
        sa.Column('simulation_id', sa.String(9), sa.ForeignKey('simulations.id', ondelete='CASCADE'), nullable=False),
        sa.Column('collaborator_id', sa.String(9), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('collaboration_id', sa.String(9), sa.ForeignKey('collaborations.id', ondelete='SET NULL'), nullable=True),
        sa.Column('activity_type', sa.String(30), nullable=False),
        sa.Column('layer_number', sa.Integer(), nullable=True),
        sa.Column('content', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
    )
    op.create_index('ix_collab_activities_simulation_id', 'collab_activities', ['simulation_id'])

    # platform_settings
    op.create_table(
        'platform_settings',
        sa.Column('id', sa.String(9), primary_key=True),
        sa.Column('key', sa.String(100), nullable=False, unique=True),
        sa.Column('value', sa.Text(), nullable=False),
        sa.Column('updated_by', sa.String(9), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
    )
    op.create_index('ix_platform_settings_key', 'platform_settings', ['key'])

    # ai_interactions
    op.create_table(
        'ai_interactions',
        sa.Column('id', sa.String(9), primary_key=True),
        sa.Column('user_id', sa.String(9), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('simulation_id', sa.String(9), sa.ForeignKey('simulations.id', ondelete='SET NULL'), nullable=True),
        sa.Column('interaction_type', sa.String(30), nullable=False),
        sa.Column('prompt_tokens', sa.Integer(), nullable=True),
        sa.Column('completion_tokens', sa.Integer(), nullable=True),
        sa.Column('model', sa.String(100), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
    )
    op.create_index('ix_ai_interactions_user_id', 'ai_interactions', ['user_id'])
    op.create_index('ix_ai_interactions_simulation_id', 'ai_interactions', ['simulation_id'])

    # audit_logs
    op.create_table(
        'audit_logs',
        sa.Column('id', sa.String(9), primary_key=True),
        sa.Column('user_id', sa.String(9), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('action', sa.String(100), nullable=False),
        sa.Column('resource_id', sa.String(9), nullable=True),
        sa.Column('metadata', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
    )
    op.create_index('ix_audit_logs_user_id', 'audit_logs', ['user_id'])


def downgrade():
    op.drop_table('audit_logs')
    op.drop_table('ai_interactions')
    op.drop_table('platform_settings')
    op.drop_table('collab_activities')
    op.drop_table('collaborations')
    op.drop_table('income_streams')
    op.drop_table('simulation_layers')
    op.drop_table('simulations')
    op.drop_table('resumes')
    op.drop_table('users')
