"""add layer6_share_tokens table

Revision ID: g7h8i9j0k1l2
Revises: e5f6a7b8c9d0
Create Date: 2026-04-24

"""
from alembic import op
import sqlalchemy as sa

revision = 'g7h8i9j0k1l2'
down_revision = 'e5f6a7b8c9d0'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'layer6_share_tokens',
        sa.Column('id', sa.String(9), primary_key=True),
        sa.Column('simulation_id', sa.String(9),
                  sa.ForeignKey('simulations.id', ondelete='CASCADE'),
                  nullable=False, index=True),
        sa.Column('cycle_id', sa.String(9),
                  sa.ForeignKey('layer6_cycles.id', ondelete='CASCADE'),
                  nullable=True),
        sa.Column('token', sa.String(9), nullable=False, unique=True),
        sa.Column('created_by', sa.String(9),
                  sa.ForeignKey('users.id', ondelete='CASCADE'),
                  nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
    )
    op.create_index('ix_layer6_share_tokens_simulation_id', 'layer6_share_tokens', ['simulation_id'])
    op.create_index('ix_layer6_share_tokens_token', 'layer6_share_tokens', ['token'])


def downgrade():
    op.drop_index('ix_layer6_share_tokens_token', table_name='layer6_share_tokens')
    op.drop_index('ix_layer6_share_tokens_simulation_id', table_name='layer6_share_tokens')
    op.drop_table('layer6_share_tokens')
