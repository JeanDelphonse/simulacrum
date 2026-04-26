"""add email_verify_token_expires to users

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-04-08

"""
from alembic import op
import sqlalchemy as sa

revision = 'c3d4e5f6a7b8'
down_revision = 'b2c3d4e5f6a7'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('users', sa.Column('email_verify_token_expires', sa.DateTime(), nullable=True))


def downgrade():
    op.drop_column('users', 'email_verify_token_expires')
