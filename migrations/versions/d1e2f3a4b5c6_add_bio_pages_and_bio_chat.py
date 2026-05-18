"""Add bio_pages, bio_chat_sessions, bio_chat_messages (SIM-PRD-BIO-001 + SIM-PRD-BIOCHAT-001)

Revision ID: d1e2f3a4b5c6
Revises: ca6c6fda8cdd
Create Date: 2026-05-18 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = 'd1e2f3a4b5c6'
down_revision = 'ca6c6fda8cdd'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'bio_pages',
        sa.Column('id',                 sa.String(9),   nullable=False),
        sa.Column('user_id',            sa.String(9),   nullable=False),
        sa.Column('simulation_id',      sa.String(9),   nullable=True),
        sa.Column('slug',               sa.String(50),  nullable=False),
        sa.Column('sections',           sa.Text(),      nullable=False),
        sa.Column('custom_testimonials',sa.Text(),      nullable=False),
        sa.Column('chat_settings',      sa.Text(),      nullable=False),
        sa.Column('theme',              sa.String(20),  nullable=False),
        sa.Column('status',             sa.String(20),  nullable=False),
        sa.Column('published_at',       sa.DateTime(),  nullable=True),
        sa.Column('unpublished_at',     sa.DateTime(),  nullable=True),
        sa.Column('view_count',         sa.Integer(),   nullable=False),
        sa.Column('contact_form_count', sa.Integer(),   nullable=False),
        sa.Column('cta_click_count',    sa.Integer(),   nullable=False),
        sa.Column('created_at',         sa.DateTime(),  nullable=False),
        sa.Column('updated_at',         sa.DateTime(),  nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', name='uq_bp_user'),
        sa.UniqueConstraint('slug',    name='uq_bp_slug'),
    )
    with op.batch_alter_table('bio_pages') as batch_op:
        batch_op.create_index('ix_bio_pages_user_id', ['user_id'], unique=False)
        batch_op.create_index('idx_bp_status',        ['status'],  unique=False)

    op.create_table(
        'bio_chat_sessions',
        sa.Column('id',                 sa.String(9),   nullable=False),
        sa.Column('bio_page_id',        sa.String(9),   nullable=False),
        sa.Column('user_id',            sa.String(9),   nullable=False),
        sa.Column('contact_id',         sa.String(9),   nullable=True),
        sa.Column('visitor_name',       sa.String(200), nullable=False),
        sa.Column('visitor_email',      sa.String(255), nullable=False),
        sa.Column('visitor_phone',      sa.String(50),  nullable=True),
        sa.Column('status',             sa.String(20),  nullable=False),
        sa.Column('takeover_active',    sa.Boolean(),   nullable=False),
        sa.Column('takeover_by',        sa.String(9),   nullable=True),
        sa.Column('takeover_at',        sa.DateTime(),  nullable=True),
        sa.Column('message_count',      sa.SmallInteger(), nullable=False),
        sa.Column('model_used_summary', sa.String(100), nullable=True),
        sa.Column('total_tokens',       sa.Integer(),   nullable=False),
        sa.Column('started_at',         sa.DateTime(),  nullable=False),
        sa.Column('ended_at',           sa.DateTime(),  nullable=True),
        sa.Column('created_at',         sa.DateTime(),  nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('bio_chat_sessions') as batch_op:
        batch_op.create_index('ix_bcs_bio_page_id', ['bio_page_id'], unique=False)
        batch_op.create_index('ix_bcs_user_id',     ['user_id'],     unique=False)
        batch_op.create_index('ix_bcs_contact_id',  ['contact_id'],  unique=False)
        batch_op.create_index('idx_bcs_status',     ['user_id', 'status', 'started_at'], unique=False)

    op.create_table(
        'bio_chat_messages',
        sa.Column('id',            sa.String(9),  nullable=False),
        sa.Column('session_id',    sa.String(9),  nullable=False),
        sa.Column('role',          sa.String(20), nullable=False),
        sa.Column('content',       sa.Text(),     nullable=False),
        sa.Column('model_used',    sa.String(50), nullable=True),
        sa.Column('complexity',    sa.String(10), nullable=True),
        sa.Column('tokens_input',  sa.Integer(),  nullable=True),
        sa.Column('tokens_output', sa.Integer(),  nullable=True),
        sa.Column('created_at',    sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('bio_chat_messages') as batch_op:
        batch_op.create_index('ix_bcm_session_id', ['session_id'], unique=False)
        batch_op.create_index('idx_bcm_session',   ['session_id', 'created_at'], unique=False)


def downgrade():
    with op.batch_alter_table('bio_chat_messages') as batch_op:
        batch_op.drop_index('idx_bcm_session')
        batch_op.drop_index('ix_bcm_session_id')
    op.drop_table('bio_chat_messages')

    with op.batch_alter_table('bio_chat_sessions') as batch_op:
        batch_op.drop_index('idx_bcs_status')
        batch_op.drop_index('ix_bcs_contact_id')
        batch_op.drop_index('ix_bcs_user_id')
        batch_op.drop_index('ix_bcs_bio_page_id')
    op.drop_table('bio_chat_sessions')

    with op.batch_alter_table('bio_pages') as batch_op:
        batch_op.drop_index('idx_bp_status')
        batch_op.drop_index('ix_bio_pages_user_id')
    op.drop_table('bio_pages')
