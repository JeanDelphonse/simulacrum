"""SIM-PRD-INTEG-001: Bayesian posteriors, Kajabi products, email click tracking,
integration metadata, and signing document decline reason.

Revision ID: e2f3a4b5c6d7
Revises: d1e2f3a4b5c6
Create Date: 2026-05-22

"""
from alembic import op
import sqlalchemy as sa


revision = 'e2f3a4b5c6d7'
down_revision = 'd1e2f3a4b5c6'
branch_labels = None
depends_on = None


def upgrade():
    # ── bayesian_posteriors ───────────────────────────────────────────────────
    op.create_table(
        'bayesian_posteriors',
        sa.Column('id',              sa.String(9),           nullable=False),
        sa.Column('simulation_id',   sa.String(9),           nullable=False),
        sa.Column('posterior_key',   sa.String(200),         nullable=False),
        sa.Column('value',           sa.Numeric(10, 6),      nullable=False),
        sa.Column('last_direction',  sa.String(1),           nullable=True),
        sa.Column('last_weight',     sa.Numeric(4, 3),       nullable=True),
        sa.Column('update_count',    sa.Integer(),           nullable=False),
        sa.Column('updated_at',      sa.DateTime(),          nullable=False),
        sa.Column('created_at',      sa.DateTime(),          nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('simulation_id', 'posterior_key', name='uq_bp_sim_key'),
    )
    with op.batch_alter_table('bayesian_posteriors') as batch_op:
        batch_op.create_index(
            batch_op.f('ix_bayesian_posteriors_simulation_id'),
            ['simulation_id'], unique=False,
        )

    # ── kajabi_products ───────────────────────────────────────────────────────
    op.create_table(
        'kajabi_products',
        sa.Column('id',                sa.String(9),    nullable=False),
        sa.Column('user_id',           sa.String(9),    nullable=False),
        sa.Column('simulation_id',     sa.String(9),    nullable=False),
        sa.Column('action_id',         sa.String(9),    nullable=True),
        sa.Column('artifact_id',       sa.String(9),    nullable=True),
        sa.Column('kajabi_product_id', sa.String(200),  nullable=True),
        sa.Column('product_type',      sa.String(50),   nullable=False),
        sa.Column('name',              sa.String(500),  nullable=False),
        sa.Column('checkout_url',      sa.String(1000), nullable=True),
        sa.Column('status',            sa.String(20),   nullable=False),
        sa.Column('created_at',        sa.DateTime(),   nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('kajabi_products') as batch_op:
        batch_op.create_index(
            batch_op.f('ix_kajabi_products_user_id'), ['user_id'], unique=False,
        )
        batch_op.create_index(
            batch_op.f('ix_kajabi_products_simulation_id'), ['simulation_id'], unique=False,
        )
        batch_op.create_index(
            batch_op.f('ix_kajabi_products_action_id'), ['action_id'], unique=False,
        )
        batch_op.create_index(
            batch_op.f('ix_kajabi_products_kajabi_product_id'),
            ['kajabi_product_id'], unique=False,
        )

    # ── email_campaigns: add click_count ──────────────────────────────────────
    with op.batch_alter_table('email_campaigns') as batch_op:
        batch_op.add_column(
            sa.Column('click_count', sa.SmallInteger(), nullable=False,
                      server_default='0'),
        )
        batch_op.add_column(
            sa.Column('open_count', sa.SmallInteger(), nullable=False,
                      server_default='0'),
        )

    # ── user_integrations: add metadata + warmup fields ──────────────────────
    with op.batch_alter_table('user_integrations') as batch_op:
        batch_op.add_column(
            sa.Column('meta_json', sa.Text(), nullable=True),
        )
        batch_op.add_column(
            sa.Column('warmup_started_at', sa.DateTime(), nullable=True),
        )

    # ── signing_documents: add decline_reason ────────────────────────────────
    with op.batch_alter_table('signing_documents') as batch_op:
        batch_op.add_column(
            sa.Column('declined_reason', sa.String(500), nullable=True),
        )


def downgrade():
    with op.batch_alter_table('signing_documents') as batch_op:
        batch_op.drop_column('declined_reason')

    with op.batch_alter_table('user_integrations') as batch_op:
        batch_op.drop_column('warmup_started_at')
        batch_op.drop_column('meta_json')

    with op.batch_alter_table('email_campaigns') as batch_op:
        batch_op.drop_column('open_count')
        batch_op.drop_column('click_count')

    with op.batch_alter_table('kajabi_products') as batch_op:
        batch_op.drop_index(batch_op.f('ix_kajabi_products_kajabi_product_id'))
        batch_op.drop_index(batch_op.f('ix_kajabi_products_action_id'))
        batch_op.drop_index(batch_op.f('ix_kajabi_products_simulation_id'))
        batch_op.drop_index(batch_op.f('ix_kajabi_products_user_id'))
    op.drop_table('kajabi_products')

    with op.batch_alter_table('bayesian_posteriors') as batch_op:
        batch_op.drop_index(batch_op.f('ix_bayesian_posteriors_simulation_id'))
    op.drop_table('bayesian_posteriors')
