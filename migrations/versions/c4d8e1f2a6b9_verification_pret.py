"""vérification du matériel d'un prêt

Revision ID: c4d8e1f2a6b9
Revises: b7e2f4a9c1d3
Create Date: 2026-10-31 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c4d8e1f2a6b9'
down_revision = 'b7e2f4a9c1d3'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'loan_check',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('loan_id', sa.Integer(), nullable=False),
        sa.Column('item_id', sa.Integer(), nullable=False),
        sa.Column('quantity', sa.Integer(), nullable=False),
        sa.Column('checked_by_id', sa.Integer(), nullable=True),
        sa.Column('checked_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.CheckConstraint('quantity > 0', name='ck_loan_check_quantity'),
        sa.ForeignKeyConstraint(['checked_by_id'], ['user.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['item_id'], ['item.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['loan_id'], ['loan.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('loan_id', 'item_id'),
    )


def downgrade():
    op.drop_table('loan_check')
