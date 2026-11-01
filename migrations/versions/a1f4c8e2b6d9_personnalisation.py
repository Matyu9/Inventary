"""personnalisation de l'interface

Revision ID: a1f4c8e2b6d9
Revises: d2a6b8c4e1f3
Create Date: 2026-11-01 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a1f4c8e2b6d9'
down_revision = 'd2a6b8c4e1f3'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('app_setting',
                    sa.Column('key', sa.String(length=50), nullable=False),
                    sa.Column('value', sa.Text(), nullable=False),
                    sa.PrimaryKeyConstraint('key'))
    # ALTER TABLE natif : `user` n'est pas recréée (refusé à cause des clés étrangères).
    op.add_column('user', sa.Column('theme', sa.String(length=10), nullable=True))


def downgrade():
    op.drop_column('user', 'theme')
    op.drop_table('app_setting')
