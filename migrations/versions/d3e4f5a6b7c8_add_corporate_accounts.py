"""add_corporate_accounts — CorporateAccount + CorporateEmployee tables (B9 outplacement)

Revision ID: d3e4f5a6b7c8
Revises: c2d3e4f5a6b7
Create Date: 2026-06-06
"""
from alembic import op
import sqlalchemy as sa

revision = 'd3e4f5a6b7c8'
down_revision = 'c2d3e4f5a6b7'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'corporate_accounts',
        sa.Column('id', sa.String(9), primary_key=True),
        sa.Column('org_name', sa.String(200), nullable=False),
        sa.Column('contact_name', sa.String(200), nullable=False),
        sa.Column('contact_email', sa.String(255), nullable=False),
        sa.Column('admin_user_id', sa.String(9), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('license_tier', sa.String(20), nullable=False, server_default='starter'),
        sa.Column('seat_count', sa.Integer, nullable=False, server_default='25'),
        sa.Column('seats_used', sa.Integer, nullable=False, server_default='0'),
        sa.Column('status', sa.String(20), nullable=False, server_default='pending'),
        sa.Column('white_label_name', sa.String(200), nullable=True),
        sa.Column('white_label_logo_url', sa.String(500), nullable=True),
        sa.Column('notes', sa.Text, nullable=True),
        sa.Column('created_at', sa.DateTime, nullable=False),
        sa.Column('activated_at', sa.DateTime, nullable=True),
        sa.Column('suspended_at', sa.DateTime, nullable=True),
    )
    op.create_index('ix_corporate_accounts_contact_email', 'corporate_accounts', ['contact_email'])
    op.create_index('ix_corporate_accounts_admin_user_id', 'corporate_accounts', ['admin_user_id'])
    op.create_index('ix_corporate_accounts_status', 'corporate_accounts', ['status'])

    op.create_table(
        'corporate_employees',
        sa.Column('id', sa.String(9), primary_key=True),
        sa.Column('org_id', sa.String(9), sa.ForeignKey('corporate_accounts.id', ondelete='CASCADE'), nullable=False),
        sa.Column('user_id', sa.String(9), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('email', sa.String(255), nullable=False),
        sa.Column('full_name', sa.String(200), nullable=True),
        sa.Column('status', sa.String(20), nullable=False, server_default='invited'),
        sa.Column('simulation_id', sa.String(9), nullable=True),
        sa.Column('invite_token', sa.String(64), nullable=True),
        sa.Column('provisioned_at', sa.DateTime, nullable=False),
        sa.Column('activated_at', sa.DateTime, nullable=True),
        sa.Column('completed_at', sa.DateTime, nullable=True),
        sa.UniqueConstraint('org_id', 'email', name='uq_corp_emp_org_email'),
        sa.UniqueConstraint('invite_token', name='uq_corp_emp_token'),
    )
    op.create_index('ix_corporate_employees_org_id', 'corporate_employees', ['org_id'])
    op.create_index('ix_corporate_employees_user_id', 'corporate_employees', ['user_id'])
    op.create_index('ix_corporate_employees_status', 'corporate_employees', ['status'])
    op.create_index('ix_corporate_employees_invite_token', 'corporate_employees', ['invite_token'])


def downgrade():
    op.drop_table('corporate_employees')
    op.drop_table('corporate_accounts')
