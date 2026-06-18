"""Add simi_conversations and simi_messages tables (SIM-PRD-CHAT-001 v1.2)

Revision ID: e3f4a5b6c7d8
Revises: d2e3f4a5b6c7
Create Date: 2026-06-18
"""
from alembic import op
import sqlalchemy as sa

revision = 'e3f4a5b6c7d8'
down_revision = 'd2e3f4a5b6c7'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'simi_conversations',
        sa.Column('id',              sa.String(9),  nullable=False),
        sa.Column('simulation_id',   sa.String(9),  nullable=False),
        sa.Column('user_id',         sa.String(9),  nullable=False),
        sa.Column('created_at',      sa.DateTime(), nullable=False),
        sa.Column('last_message_at', sa.DateTime(), nullable=True),
        sa.Column('total_tokens',    sa.Integer(),  nullable=False, server_default='0'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_simi_conv_sim', 'simi_conversations', ['simulation_id'])
    op.create_index('ix_simi_conv_user', 'simi_conversations', ['user_id'])

    op.create_table(
        'simi_messages',
        sa.Column('id',              sa.String(9),   nullable=False),
        sa.Column('conversation_id', sa.String(9),   nullable=False),
        sa.Column('role',            sa.String(10),  nullable=False),
        sa.Column('content',         sa.Text(),      nullable=False),
        sa.Column('tool_calls',      sa.Text(),      nullable=True),
        sa.Column('tokens_used',     sa.Integer(),   nullable=True),
        sa.Column('model',           sa.String(30),  nullable=True),
        sa.Column('created_at',      sa.DateTime(),  nullable=False),
        sa.ForeignKeyConstraint(['conversation_id'], ['simi_conversations.id'],
                                ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_simi_msg_conv', 'simi_messages', ['conversation_id', 'created_at'])


def downgrade():
    op.drop_table('simi_messages')
    op.drop_table('simi_conversations')
