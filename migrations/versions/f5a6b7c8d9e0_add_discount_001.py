"""SIM-REQ-DISCOUNT-001: simulation_discounts table + discount columns on simulations"""

from alembic import op
import sqlalchemy as sa

revision = 'f5a6b7c8d9e0'
down_revision = 'e4f5a6b7c8d9'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'simulation_discounts',
        sa.Column('id', sa.String(9), primary_key=True),
        sa.Column('discount_percentage', sa.Integer(), nullable=False),
        sa.Column('start_at', sa.DateTime(), nullable=False),
        sa.Column('end_at', sa.DateTime(), nullable=False),
        sa.Column('label', sa.String(30), nullable=True),
        sa.Column('created_by', sa.String(9), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint('discount_percentage IN (10,15,20,50,100)', name='ck_discount_pct_valid'),
        sa.CheckConstraint('end_at > start_at', name='ck_discount_dates'),
    )
    op.create_index('ix_simulation_discounts_start_end', 'simulation_discounts', ['start_at', 'end_at'])

    op.add_column('simulations',
        sa.Column('base_price_at_purchase_cents', sa.Integer(), nullable=True))
    op.add_column('simulations',
        sa.Column('discount_applied_percentage', sa.Integer(), nullable=True, server_default='0'))


def downgrade():
    op.drop_column('simulations', 'discount_applied_percentage')
    op.drop_column('simulations', 'base_price_at_purchase_cents')
    op.drop_index('ix_simulation_discounts_start_end', table_name='simulation_discounts')
    op.drop_table('simulation_discounts')
