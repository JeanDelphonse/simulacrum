"""Add simulation_videos table (SIM-PRD-VOICE-001 FR-VOICE-11).

Revision ID: i9j0k1l2m3n4
Revises: h8i9j0k1l2m3
Create Date: 2026-06-24
"""
from alembic import op
import sqlalchemy as sa

revision = 'i9j0k1l2m3n4'
down_revision = 'h8i9j0k1l2m3'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'simulation_videos',
        sa.Column('id',               sa.String(9),   nullable=False),
        sa.Column('simulation_id',    sa.String(9),   nullable=False),
        sa.Column('user_id',          sa.String(9),   nullable=False),
        sa.Column('script',           sa.Text(),      nullable=True),
        sa.Column('audio_path',       sa.String(500), nullable=True),
        sa.Column('video_path',       sa.String(500), nullable=True),
        sa.Column('thumbnail_path',   sa.String(500), nullable=True),
        sa.Column('format',           sa.String(20),  nullable=False, server_default='square'),
        sa.Column('duration_seconds', sa.Integer(),   nullable=True),
        sa.Column('embedded_on_bio',  sa.Boolean(),   nullable=False, server_default=sa.text('0')),
        sa.Column('status',           sa.String(20),  nullable=False, server_default='processing'),
        sa.Column('created_at',       sa.DateTime(),  nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.ForeignKeyConstraint(['simulation_id'], ['simulations.id']),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_simulation_videos_user_id',       'simulation_videos', ['user_id'])
    op.create_index('ix_simulation_videos_simulation_id', 'simulation_videos', ['simulation_id'])


def downgrade():
    op.drop_index('ix_simulation_videos_simulation_id', table_name='simulation_videos')
    op.drop_index('ix_simulation_videos_user_id',       table_name='simulation_videos')
    op.drop_table('simulation_videos')
