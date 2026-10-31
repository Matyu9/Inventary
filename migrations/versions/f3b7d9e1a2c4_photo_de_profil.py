"""photo de profil

Revision ID: f3b7d9e1a2c4
Revises: e8a4c6d2f1b7
Create Date: 2026-10-31 20:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f3b7d9e1a2c4'
down_revision = 'e8a4c6d2f1b7'
branch_labels = None
depends_on = None


def upgrade():
    # ALTER TABLE natif : `user` n'est pas recréée (refusé à cause des clés étrangères).
    op.add_column('user', sa.Column('avatar', sa.String(length=255), nullable=True))


def downgrade():
    op.drop_column('user', 'avatar')
