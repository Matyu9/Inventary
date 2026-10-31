"""corbeille

Revision ID: a9d3e5f7b2c8
Revises: f3b7d9e1a2c4
Create Date: 2026-10-31 22:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a9d3e5f7b2c8'
down_revision = 'f3b7d9e1a2c4'
branch_labels = None
depends_on = None

TABLES = ('item', 'kit')


def upgrade():
    # ALTER TABLE ADD COLUMN natif : `item` et `kit` sont référencées partout, le mode batch les recréerait.
    # `deleted_by_id` reste un simple entier en base (SQLite ne sait pas supprimer une colonne qui porte une clé
    # étrangère, ce qui bloquerait le retour arrière) ; le modèle déclare la relation, que la fusion de personnes
    # et la suppression d'une fiche prennent en compte.
    for table in TABLES:
        op.add_column(table, sa.Column('deleted_at', sa.DateTime(), nullable=True))
        op.add_column(table, sa.Column('deleted_by_id', sa.Integer(), nullable=True))
        op.create_index(f'ix_{table}_deleted_at', table, ['deleted_at'], unique=False)
    op.add_column('kit', sa.Column('trashed_item_ids', sa.JSON(), nullable=True))


def downgrade():
    op.drop_column('kit', 'trashed_item_ids')
    for table in reversed(TABLES):
        op.drop_index(f'ix_{table}_deleted_at', table_name=table)
        op.drop_column(table, 'deleted_by_id')
        op.drop_column(table, 'deleted_at')
