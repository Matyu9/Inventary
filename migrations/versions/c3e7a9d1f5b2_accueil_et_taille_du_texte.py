"""page d'accueil et taille du texte

Revision ID: c3e7a9d1f5b2
Revises: b8d2e6f4a1c7
Create Date: 2026-11-01 19:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c3e7a9d1f5b2'
down_revision = 'b8d2e6f4a1c7'
branch_labels = None
depends_on = None


def upgrade():
    # ALTER TABLE natif : `user` n'est pas recréée (refusé à cause des clés étrangères).
    op.add_column('user', sa.Column('home', sa.String(length=20), nullable=True))
    op.add_column('user', sa.Column('text_size', sa.String(length=10), nullable=True))


def downgrade():
    op.drop_column('user', 'text_size')
    op.drop_column('user', 'home')
