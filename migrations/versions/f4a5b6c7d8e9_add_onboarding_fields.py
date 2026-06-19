"""Add onboarding_step and onboarding_completed_at to users (SIM-PRD-ONBOARD-001)

Revision ID: f4a5b6c7d8e9
Revises: e3f4a5b6c7d8
Create Date: 2026-06-19
"""
from alembic import op
import sqlalchemy as sa

revision = 'f4a5b6c7d8e9'
down_revision = 'e3f4a5b6c7d8'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('users', sa.Column('onboarding_step', sa.Integer(), nullable=False, server_default='1'))
    op.add_column('users', sa.Column('onboarding_completed_at', sa.DateTime(), nullable=True))
    # Existing verified users are treated as having already completed onboarding
    op.execute(
        "UPDATE users SET onboarding_completed_at = created_at WHERE email_verified = 1"
    )


def downgrade():
    op.drop_column('users', 'onboarding_completed_at')
    op.drop_column('users', 'onboarding_step')
