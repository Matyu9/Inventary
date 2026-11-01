"""tableau de bord à la carte

Revision ID: b8d2e6f4a1c7
Revises: a1f4c8e2b6d9
Create Date: 2026-11-01 16:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b8d2e6f4a1c7'
down_revision = 'a1f4c8e2b6d9'
branch_labels = None
depends_on = None


def upgrade():
    # ALTER TABLE natif : `user` n'est pas recréée (refusé à cause des clés étrangères).
    op.add_column('user', sa.Column('dashboard', sa.JSON(), nullable=True))


def downgrade():
    op.drop_column('user', 'dashboard')
