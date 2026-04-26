"""add profile, visibility, inquiries, sessions tables + user fields

Revision ID: h8i9j0k1l2m3
Revises: g7h8i9j0k1l2
Create Date: 2026-04-26

"""
from alembic import op
import sqlalchemy as sa

revision = 'h8i9j0k1l2m3'
down_revision = 'g7h8i9j0k1l2'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'user_profiles',
        sa.Column('id', sa.String(9), primary_key=True),
        sa.Column('user_id', sa.String(9), sa.ForeignKey('users.id', ondelete='CASCADE'),
                  unique=True, nullable=False),
        sa.Column('username', sa.String(30), unique=True, nullable=False),
        sa.Column('display_name', sa.String(100)),
        sa.Column('tagline', sa.String(200)),
        sa.Column('bio', sa.Text),
        sa.Column('bio_generated_at', sa.DateTime),
        sa.Column('bio_edited', sa.Boolean, default=False),
        sa.Column('avatar_path', sa.String(500)),
        sa.Column('location', sa.String(100)),
        sa.Column('linkedin_url', sa.String(255)),
        sa.Column('website_url', sa.String(255)),
        sa.Column('twitter_url', sa.String(255)),
        sa.Column('other_link_url', sa.String(255)),
        sa.Column('other_link_label', sa.String(50)),
        sa.Column('booking_url', sa.String(255)),
        sa.Column('booking_btn_label', sa.String(50)),
        sa.Column('show_contact_form', sa.Boolean, default=True),
        sa.Column('show_booking_btn', sa.Boolean, default=True),
        sa.Column('is_published', sa.Boolean, default=False),
        sa.Column('noindex', sa.Boolean, default=False),
        sa.Column('created_at', sa.DateTime, nullable=False),
        sa.Column('updated_at', sa.DateTime),
    )
    op.create_index('ix_user_profiles_username', 'user_profiles', ['username'], unique=True)

    op.create_table(
        'simulation_visibility',
        sa.Column('id', sa.String(9), primary_key=True),
        sa.Column('simulation_id', sa.String(9),
                  sa.ForeignKey('simulations.id', ondelete='CASCADE'), nullable=False),
        sa.Column('user_id', sa.String(9),
                  sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('is_public', sa.Boolean, default=False),
        sa.Column('display_order', sa.Integer, default=0),
        sa.Column('zone_tagline', sa.String(200)),
        sa.Column('services', sa.JSON),
        sa.Column('availability', sa.Enum('available', 'limited', 'unavailable', 'hidden',
                                          name='sim_availability'), default='hidden'),
        sa.Column('created_at', sa.DateTime, nullable=False),
        sa.Column('updated_at', sa.DateTime),
    )
    op.create_index('ix_sim_visibility_user', 'simulation_visibility', ['user_id'])
    op.create_index('ix_sim_visibility_sim', 'simulation_visibility', ['simulation_id'])

    op.create_table(
        'profile_inquiries',
        sa.Column('id', sa.String(9), primary_key=True),
        sa.Column('profile_user_id', sa.String(9),
                  sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('visitor_name', sa.String(100), nullable=False),
        sa.Column('visitor_email', sa.String(255), nullable=False),
        sa.Column('subject', sa.String(100)),
        sa.Column('message', sa.Text, nullable=False),
        sa.Column('ip_hash', sa.String(64)),
        sa.Column('recaptcha_score', sa.Float),
        sa.Column('created_at', sa.DateTime, nullable=False),
    )
    op.create_index('ix_profile_inquiries_user', 'profile_inquiries', ['profile_user_id'])
    op.create_index('ix_profile_inquiries_created', 'profile_inquiries', ['created_at'])

    op.create_table(
        'user_sessions',
        sa.Column('id', sa.String(9), primary_key=True),
        sa.Column('user_id', sa.String(9),
                  sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('jti', sa.String(64), unique=True, nullable=False),
        sa.Column('user_agent', sa.String(500)),
        sa.Column('ip_address', sa.String(45)),
        sa.Column('last_active', sa.DateTime),
        sa.Column('expires_at', sa.DateTime, nullable=False),
        sa.Column('revoked_at', sa.DateTime),
        sa.Column('created_at', sa.DateTime, nullable=False),
    )
    op.create_index('ix_user_sessions_user_id', 'user_sessions', ['user_id'])
    op.create_index('ix_user_sessions_jti', 'user_sessions', ['jti'], unique=True)

    # Add new columns to users table
    op.add_column('users', sa.Column('pending_email', sa.String(255), nullable=True))
    op.add_column('users', sa.Column('pending_email_token', sa.String(255), nullable=True))
    op.add_column('users', sa.Column('pending_email_token_expires', sa.DateTime, nullable=True))
    op.add_column('users', sa.Column('deleted_at', sa.DateTime, nullable=True))
    op.add_column('users', sa.Column('recovery_token', sa.String(255), nullable=True))
    op.add_column('users', sa.Column('recovery_token_expires', sa.DateTime, nullable=True))


def downgrade():
    op.drop_column('users', 'recovery_token_expires')
    op.drop_column('users', 'recovery_token')
    op.drop_column('users', 'deleted_at')
    op.drop_column('users', 'pending_email_token_expires')
    op.drop_column('users', 'pending_email_token')
    op.drop_column('users', 'pending_email')

    op.drop_index('ix_user_sessions_jti', table_name='user_sessions')
    op.drop_index('ix_user_sessions_user_id', table_name='user_sessions')
    op.drop_table('user_sessions')

    op.drop_index('ix_profile_inquiries_created', table_name='profile_inquiries')
    op.drop_index('ix_profile_inquiries_user', table_name='profile_inquiries')
    op.drop_table('profile_inquiries')

    op.drop_index('ix_sim_visibility_sim', table_name='simulation_visibility')
    op.drop_index('ix_sim_visibility_user', table_name='simulation_visibility')
    op.drop_table('simulation_visibility')

    op.drop_index('ix_user_profiles_username', table_name='user_profiles')
    op.drop_table('user_profiles')

    op.execute("DROP TYPE IF EXISTS sim_availability")
