"""add partner program tables

Revision ID: a1b2c3d4e5f6
Revises: f6a7b8c9d0e1
Create Date: 2026-04-08

"""
from alembic import op
import sqlalchemy as sa

revision = 'a1b2c3d4e5f6'
down_revision = 'f6a7b8c9d0e1'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'referral_partners',
        sa.Column('id', sa.String(9), primary_key=True, nullable=False),
        sa.Column('user_id', sa.String(9), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True, index=True),
        sa.Column('referral_code', sa.String(9), unique=True, nullable=True, index=True),
        sa.Column('full_name', sa.String(200), nullable=False),
        sa.Column('business_name', sa.String(200), nullable=True),
        sa.Column('email', sa.String(255), nullable=False, index=True),
        sa.Column('partner_type', sa.String(50), nullable=False),
        sa.Column('website_url', sa.String(500), nullable=True),
        sa.Column('practice_description', sa.String(300), nullable=True),
        sa.Column('stripe_connect_id', sa.String(100), nullable=True),
        sa.Column('status', sa.String(20), nullable=False, server_default='pending'),
        sa.Column('applied_at', sa.DateTime, nullable=False),
        sa.Column('approved_at', sa.DateTime, nullable=True),
        sa.Column('approved_by', sa.String(9), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
    )

    op.create_table(
        'referral_signups',
        sa.Column('id', sa.String(9), primary_key=True, nullable=False),
        sa.Column('partner_id', sa.String(9), sa.ForeignKey('referral_partners.id', ondelete='CASCADE'),
                  nullable=False, index=True),
        sa.Column('referred_user_id', sa.String(9), sa.ForeignKey('users.id', ondelete='CASCADE'),
                  nullable=False, unique=True, index=True),
        sa.Column('referral_code', sa.String(9), nullable=False),
        sa.Column('clicked_at', sa.DateTime, nullable=False),
        sa.Column('registered_at', sa.DateTime, nullable=False),
        sa.Column('attributed_at', sa.DateTime, nullable=True),
    )

    op.create_table(
        'commissions',
        sa.Column('id', sa.String(9), primary_key=True, nullable=False),
        sa.Column('partner_id', sa.String(9), sa.ForeignKey('referral_partners.id', ondelete='CASCADE'),
                  nullable=False, index=True),
        sa.Column('simulation_id', sa.String(9), sa.ForeignKey('simulations.id', ondelete='SET NULL'),
                  nullable=True, index=True),
        sa.Column('client_user_id', sa.String(9), sa.ForeignKey('users.id', ondelete='SET NULL'),
                  nullable=True, index=True),
        sa.Column('simulation_charge', sa.Numeric(8, 2), nullable=False),
        sa.Column('commission_rate', sa.Numeric(5, 4), nullable=False),
        sa.Column('commission_amount', sa.Numeric(8, 2), nullable=False),
        sa.Column('status', sa.String(20), nullable=False, server_default='pending'),
        sa.Column('stripe_transfer_id', sa.String(255), nullable=True),
        sa.Column('created_at', sa.DateTime, nullable=False),
        sa.Column('paid_at', sa.DateTime, nullable=True),
    )

    op.create_table(
        'partner_payouts',
        sa.Column('id', sa.String(9), primary_key=True, nullable=False),
        sa.Column('partner_id', sa.String(9), sa.ForeignKey('referral_partners.id', ondelete='CASCADE'),
                  nullable=False, index=True),
        sa.Column('payout_amount', sa.Numeric(8, 2), nullable=False),
        sa.Column('commission_ids', sa.Text, nullable=True),
        sa.Column('stripe_payout_id', sa.String(255), nullable=True),
        sa.Column('status', sa.String(20), nullable=False, server_default='processing'),
        sa.Column('initiated_at', sa.DateTime, nullable=False),
        sa.Column('completed_at', sa.DateTime, nullable=True),
    )

    op.create_table(
        'advisor_access',
        sa.Column('id', sa.String(9), primary_key=True, nullable=False),
        sa.Column('simulation_id', sa.String(9), sa.ForeignKey('simulations.id', ondelete='CASCADE'),
                  nullable=False, index=True),
        sa.Column('partner_id', sa.String(9), sa.ForeignKey('referral_partners.id', ondelete='CASCADE'),
                  nullable=True, index=True),
        sa.Column('pending_email', sa.String(255), nullable=True),
        sa.Column('granted_by', sa.String(9), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('access_level', sa.String(20), nullable=False, server_default='full_read'),
        sa.Column('granted_at', sa.DateTime, nullable=False),
        sa.Column('revoked_at', sa.DateTime, nullable=True),
        sa.Column('last_viewed_at', sa.DateTime, nullable=True),
    )

    op.create_table(
        'advisor_notes',
        sa.Column('id', sa.String(9), primary_key=True, nullable=False),
        sa.Column('advisor_access_id', sa.String(9), sa.ForeignKey('advisor_access.id', ondelete='CASCADE'),
                  nullable=False, index=True),
        sa.Column('simulation_id', sa.String(9), sa.ForeignKey('simulations.id', ondelete='CASCADE'),
                  nullable=False, index=True),
        sa.Column('layer_number', sa.Integer, nullable=True),
        sa.Column('note_text', sa.Text, nullable=False),
        sa.Column('created_at', sa.DateTime, nullable=False),
        sa.Column('updated_at', sa.DateTime, nullable=False),
    )


def downgrade():
    op.drop_table('advisor_notes')
    op.drop_table('advisor_access')
    op.drop_table('partner_payouts')
    op.drop_table('commissions')
    op.drop_table('referral_signups')
    op.drop_table('referral_partners')
