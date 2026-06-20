"""Add bio page enhanced sections to user_profiles (SIM-PRD-BIO-003)

Revision ID: a7b8c9d0e1f2
Revises: f4a5b6c7d8e9
Create Date: 2026-06-19
"""
from alembic import op
import sqlalchemy as sa

revision = 'a7b8c9d0e1f2'
down_revision = 'f4a5b6c7d8e9'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('user_profiles') as batch_op:
        batch_op.add_column(sa.Column('career_history',      sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('notable_work',        sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('ventures',            sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('education',           sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('certifications',      sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('references_press',    sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('publications',        sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('projects',            sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('bio_sections_visible', sa.Text(), nullable=True))


def downgrade():
    with op.batch_alter_table('user_profiles') as batch_op:
        batch_op.drop_column('career_history')
        batch_op.drop_column('notable_work')
        batch_op.drop_column('ventures')
        batch_op.drop_column('education')
        batch_op.drop_column('certifications')
        batch_op.drop_column('references_press')
        batch_op.drop_column('publications')
        batch_op.drop_column('projects')
        batch_op.drop_column('bio_sections_visible')
