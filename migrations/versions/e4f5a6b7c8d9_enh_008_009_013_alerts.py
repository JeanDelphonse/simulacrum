"""Add ENH-008 user_insight, ENH-009 trust_level, ENH-013 unlock_all_layers, ENH-003 integration_signals"""

from alembic import op
import sqlalchemy as sa

revision = 'e4f5a6b7c8d9'
down_revision = 'd3e4f5a6b7c8'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('layer6_cycles',
        sa.Column('user_insight', sa.Text(), nullable=True))

    op.add_column('layer6_configs',
        sa.Column('trust_level', sa.String(20), nullable=False, server_default='balanced'))

    op.add_column('simulations',
        sa.Column('unlock_all_layers', sa.Boolean(), nullable=False, server_default='0'))

    op.add_column('layer6_momentum',
        sa.Column('last_milestone_reached_cents', sa.Integer(), nullable=True))

    op.create_table(
        'integration_signals',
        sa.Column('id', sa.String(9), nullable=False),
        sa.Column('simulation_id', sa.String(9), nullable=False),
        sa.Column('user_id', sa.String(9), nullable=False),
        sa.Column('signal_type', sa.String(50), nullable=False),
        sa.Column('payload', sa.Text(), nullable=True),
        sa.Column('alert_created', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_int_sig_sim', 'integration_signals', ['simulation_id'])
    op.create_index('ix_int_sig_user', 'integration_signals', ['user_id'])


def downgrade():
    op.drop_index('ix_int_sig_user', table_name='integration_signals')
    op.drop_index('ix_int_sig_sim', table_name='integration_signals')
    op.drop_table('integration_signals')
    op.drop_column('layer6_momentum', 'last_milestone_reached_cents')
    op.drop_column('simulations', 'unlock_all_layers')
    op.drop_column('layer6_configs', 'trust_level')
    op.drop_column('layer6_cycles', 'user_insight')
