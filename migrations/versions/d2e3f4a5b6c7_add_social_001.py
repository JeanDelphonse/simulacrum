"""Add social network layer tables (SIM-PRD-SOCIAL-001)

bio_page_likes, user_connections, activity_events, platform_chats, platform_chat_messages.
Also adds like_count to bio_pages and connection_count to users.

Revision ID: d2e3f4a5b6c7
Revises: c5d6e7f8a9b0
Create Date: 2026-06-13 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = 'd2e3f4a5b6c7'
down_revision = 'c5d6e7f8a9b0'
branch_labels = None
depends_on = None


def upgrade():
    # ── bio_page_likes ────────────────────────────────────────────────────
    op.create_table(
        'bio_page_likes',
        sa.Column('id',          sa.String(9),  nullable=False),
        sa.Column('bio_page_id', sa.String(9),  nullable=False),
        sa.Column('user_id',     sa.String(9),  nullable=False),
        sa.Column('created_at',  sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('bio_page_id', 'user_id', name='uq_bpl_page_user'),
    )
    op.create_index('idx_bpl_bio',       'bio_page_likes', ['bio_page_id'])
    op.create_index('idx_bpl_user',      'bio_page_likes', ['user_id'])
    op.create_index('idx_bpl_user_date', 'bio_page_likes', ['user_id', 'created_at'])

    # ── user_connections ──────────────────────────────────────────────────
    op.create_table(
        'user_connections',
        sa.Column('id',         sa.String(9),  nullable=False),
        sa.Column('user_a_id',  sa.String(9),  nullable=False),
        sa.Column('user_b_id',  sa.String(9),  nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_a_id', 'user_b_id', name='uq_uc_pair'),
    )
    op.create_index('idx_uc_a',  'user_connections', ['user_a_id'])
    op.create_index('idx_uc_b',  'user_connections', ['user_b_id', 'created_at'])

    # ── activity_events ───────────────────────────────────────────────────
    op.create_table(
        'activity_events',
        sa.Column('id',         sa.String(9),   nullable=False),
        sa.Column('user_id',    sa.String(9),   nullable=False),
        sa.Column('event_type', sa.String(50),  nullable=False),
        sa.Column('metadata',   sa.Text(),      nullable=True),
        sa.Column('created_at', sa.DateTime(),  nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_ae_user_date', 'activity_events', ['user_id', 'created_at'])
    op.create_index('idx_ae_type_date', 'activity_events', ['event_type', 'created_at'])

    # ── platform_chats ────────────────────────────────────────────────────
    op.create_table(
        'platform_chats',
        sa.Column('id',              sa.String(9),  nullable=False),
        sa.Column('owner_user_id',   sa.String(9),  nullable=False),
        sa.Column('bio_page_id',     sa.String(9),  nullable=False),
        sa.Column('chatter_user_id', sa.String(9),  nullable=False),
        sa.Column('contact_id',      sa.String(9),  nullable=True),
        sa.Column('status',          sa.String(20), nullable=False),
        sa.Column('message_count',   sa.SmallInteger(), nullable=False),
        sa.Column('total_tokens',    sa.Integer(),  nullable=False),
        sa.Column('last_message_at', sa.DateTime(), nullable=True),
        sa.Column('tool_calls',      sa.Text(),     nullable=True),
        sa.Column('created_at',      sa.DateTime(), nullable=False),
        sa.Column('updated_at',      sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('owner_user_id', 'chatter_user_id', name='uq_pc_pair'),
    )
    op.create_index('idx_pc_owner',   'platform_chats', ['owner_user_id', 'created_at'])
    op.create_index('idx_pc_chatter', 'platform_chats', ['chatter_user_id', 'updated_at'])
    op.create_index('idx_pc_bio',     'platform_chats', ['bio_page_id'])

    # ── platform_chat_messages ────────────────────────────────────────────
    op.create_table(
        'platform_chat_messages',
        sa.Column('id',            sa.String(9),  nullable=False),
        sa.Column('chat_id',       sa.String(9),  nullable=False),
        sa.Column('role',          sa.String(20), nullable=False),
        sa.Column('content',       sa.Text(),     nullable=False),
        sa.Column('model_used',    sa.String(50), nullable=True),
        sa.Column('tokens_input',  sa.Integer(),  nullable=True),
        sa.Column('tokens_output', sa.Integer(),  nullable=True),
        sa.Column('created_at',    sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_pcm_chat', 'platform_chat_messages', ['chat_id', 'created_at'])

    # ── bio_pages: add like_count ─────────────────────────────────────────
    op.add_column('bio_pages', sa.Column('like_count', sa.Integer(), nullable=False, server_default='0'))

    # ── users: add connection_count ───────────────────────────────────────
    op.add_column('users', sa.Column('connection_count', sa.Integer(), nullable=False, server_default='0'))


def downgrade():
    op.drop_column('users', 'connection_count')
    op.drop_column('bio_pages', 'like_count')
    op.drop_index('idx_pcm_chat',    table_name='platform_chat_messages')
    op.drop_table('platform_chat_messages')
    op.drop_index('idx_pc_bio',      table_name='platform_chats')
    op.drop_index('idx_pc_chatter',  table_name='platform_chats')
    op.drop_index('idx_pc_owner',    table_name='platform_chats')
    op.drop_table('platform_chats')
    op.drop_index('idx_ae_type_date', table_name='activity_events')
    op.drop_index('idx_ae_user_date', table_name='activity_events')
    op.drop_table('activity_events')
    op.drop_index('idx_uc_b',  table_name='user_connections')
    op.drop_index('idx_uc_a',  table_name='user_connections')
    op.drop_table('user_connections')
    op.drop_index('idx_bpl_user_date', table_name='bio_page_likes')
    op.drop_index('idx_bpl_user',      table_name='bio_page_likes')
    op.drop_index('idx_bpl_bio',       table_name='bio_page_likes')
    op.drop_table('bio_page_likes')
