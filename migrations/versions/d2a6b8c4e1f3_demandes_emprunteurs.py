"""demandes de prêt des emprunteurs

Revision ID: d2a6b8c4e1f3
Revises: c7f1a3e9d5b2
Create Date: 2026-11-01 18:00:00.000000

"""
import json

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd2a6b8c4e1f3'
down_revision = 'c7f1a3e9d5b2'
branch_labels = None
depends_on = None

PERMISSION = 'loan.request_own'


def _update_roles(change, keys=None):
    bind = op.get_bind()
    for key, perms in bind.execute(sa.text("SELECT key, permissions FROM role")).all():
        if keys is not None and key not in keys:
            continue
        perms = perms if isinstance(perms, list) else json.loads(perms or "[]")
        bind.execute(sa.text("UPDATE role SET permissions = :p WHERE key = :k"),
                     {"p": json.dumps(sorted(change(set(perms)))), "k": key})


def upgrade():
    # ALTER TABLE ADD COLUMN natif : `loan` est référencée par les lignes, pointages, mouvements de stock…
    # Une colonne plutôt qu'un nouveau statut : la contrainte ck_loan_status ne se modifie pas sans recréer la table.
    op.add_column('loan', sa.Column('awaiting_guarantor', sa.Boolean(), nullable=False, server_default='0'))
    _update_roles(lambda perms: perms | {PERMISSION}, keys={'emprunteur'})


def downgrade():
    _update_roles(lambda perms: perms - {PERMISSION})
    # Les demandes encore en attente du garant redeviennent de simples demandes, à valider par un admin.
    op.drop_column('loan', 'awaiting_guarantor')
