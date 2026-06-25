"""Add voice training fields to users (SIM-PRD-VOICE-001).

Revision ID: j0k1l2m3n4o5
Revises: i9j0k1l2m3n4
Create Date: 2026-06-25
"""
from alembic import op
import sqlalchemy as sa

revision = 'j0k1l2m3n4o5'
down_revision = 'i9j0k1l2m3n4'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('users', sa.Column('elevenlabs_voice_id',       sa.String(50),  nullable=True))
    op.add_column('users', sa.Column('voice_trained_at',          sa.DateTime(),  nullable=True))
    op.add_column('users', sa.Column('voice_consent_accepted_at', sa.DateTime(),  nullable=True))
    op.add_column('users', sa.Column('voice_training_paid_at',    sa.DateTime(),  nullable=True))


def downgrade():
    op.drop_column('users', 'voice_training_paid_at')
    op.drop_column('users', 'voice_consent_accepted_at')
    op.drop_column('users', 'voice_trained_at')
    op.drop_column('users', 'elevenlabs_voice_id')
