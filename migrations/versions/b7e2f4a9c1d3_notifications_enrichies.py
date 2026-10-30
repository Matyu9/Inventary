"""notifications enrichies (type, prêt et objet liés) et textes d'emails personnalisables

Revision ID: b7e2f4a9c1d3
Revises: a3c1d9e2f7b4
Create Date: 2026-10-30 23:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b7e2f4a9c1d3'
down_revision = 'a3c1d9e2f7b4'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'email_template',
        sa.Column('kind', sa.String(length=30), nullable=False),
        sa.Column('subject', sa.String(length=200), nullable=False),
        sa.Column('intro', sa.Text(), nullable=False),
        sa.Column('button', sa.String(length=60), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('kind'),
    )
    with op.batch_alter_table('notification', schema=None) as batch_op:
        batch_op.add_column(sa.Column('kind', sa.String(length=30), nullable=True))
        batch_op.add_column(sa.Column('loan_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('item_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key('fk_notification_loan_id_loan', 'loan', ['loan_id'], ['id'], ondelete='SET NULL')
        batch_op.create_foreign_key('fk_notification_item_id_item', 'item', ['item_id'], ['id'], ondelete='SET NULL')


def downgrade():
    with op.batch_alter_table('notification', schema=None) as batch_op:
        batch_op.drop_constraint('fk_notification_item_id_item', type_='foreignkey')
        batch_op.drop_constraint('fk_notification_loan_id_loan', type_='foreignkey')
        batch_op.drop_column('item_id')
        batch_op.drop_column('loan_id')
        batch_op.drop_column('kind')
    op.drop_table('email_template')
