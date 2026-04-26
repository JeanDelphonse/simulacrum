"""partner program v2 — new columns and referral_invitations table

Revision ID: b3c4d5e6f7a8
Revises: a1b2c3d4e5f6
Create Date: 2026-04-09

"""
from alembic import op
import sqlalchemy as sa

revision = 'b3c4d5e6f7a8'
down_revision = 'a1b2c3d4e5f6'
branch_labels = None
depends_on = None


def upgrade():
    # users: dual-role partner flags
    op.add_column('users', sa.Column('is_partner', sa.Boolean(), nullable=False, server_default='0'))
    op.add_column('users', sa.Column('partner_welcome_shown', sa.Boolean(), nullable=False, server_default='0'))

    # referral_partners: new fields
    op.add_column('referral_partners', sa.Column('commission_rate_override', sa.Numeric(5, 4), nullable=True))
    op.add_column('referral_partners', sa.Column('application_source', sa.String(20), nullable=False, server_default='public'))
    op.add_column('referral_partners', sa.Column('simulations_at_apply', sa.Integer(), nullable=True))
    op.add_column('referral_partners', sa.Column('last_declined_at', sa.DateTime(), nullable=True))
    op.add_column('referral_partners', sa.Column('declined_reason', sa.String(500), nullable=True))

    # referral_invitations
    op.create_table(
        'referral_invitations',
        sa.Column('id', sa.String(9), primary_key=True, nullable=False),
        sa.Column('partner_id', sa.String(9), sa.ForeignKey('referral_partners.id', ondelete='CASCADE'),
                  nullable=False, index=True),
        sa.Column('recipient_email', sa.String(255), nullable=False),
        sa.Column('recipient_first_name', sa.String(100), nullable=True),
        sa.Column('personal_message', sa.String(500), nullable=True),
        sa.Column('status', sa.String(20), nullable=False, server_default='sent'),
        sa.Column('sent_at', sa.DateTime(), nullable=False),
        sa.Column('opened_at', sa.DateTime(), nullable=True),
        sa.Column('converted_at', sa.DateTime(), nullable=True),
    )


def downgrade():
    op.drop_table('referral_invitations')
    op.drop_column('referral_partners', 'declined_reason')
    op.drop_column('referral_partners', 'last_declined_at')
    op.drop_column('referral_partners', 'simulations_at_apply')
    op.drop_column('referral_partners', 'application_source')
    op.drop_column('referral_partners', 'commission_rate_override')
    op.drop_column('users', 'partner_welcome_shown')
    op.drop_column('users', 'is_partner')
