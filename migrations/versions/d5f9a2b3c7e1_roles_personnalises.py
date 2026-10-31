"""rôles personnalisés

Revision ID: d5f9a2b3c7e1
Revises: c4d8e1f2a6b9
Create Date: 2026-10-31 14:00:00.000000

"""
import re

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd5f9a2b3c7e1'
down_revision = 'c4d8e1f2a6b9'
branch_labels = None
depends_on = None

# Droits des rôles de base au moment de la migration (copie figée de app/permissions.py).
PROF = ["inventory.view", "item.edit", "note.add", "maintenance.edit", "reminder.edit", "stock.move", "scan",
        "label.print", "myloans.view", "calendar.view", "loan.request", "loan.guarantee", "loan.list_all",
        "loan.verify", "audit.scan", "report.view", "export", "borrower.create"]
ROLES = [("admin", "Administrateur", []), ("prof", "Professeur", PROF),
         ("emprunteur", "Emprunteur", ["myloans.view", "calendar.view"])]
CHECK = "CONSTRAINT ck_user_role CHECK (role IN ('admin', 'prof', 'emprunteur'))"


def _set_user_sql(transform):
    """Modifie le CREATE TABLE de `user` sans recréer la table.

    Procédure documentée par SQLite pour ajouter ou retirer une contrainte CHECK : recréer `user` est
    impossible ici (les clés étrangères ON DELETE CASCADE videraient sessions et notifications).
    """
    bind = op.get_bind()
    sql = bind.exec_driver_sql("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'user'").scalar()
    new_sql = transform(sql)
    if new_sql == sql:
        return
    version = bind.exec_driver_sql("PRAGMA schema_version").scalar()
    bind.exec_driver_sql("PRAGMA writable_schema = ON")
    bind.exec_driver_sql("UPDATE sqlite_master SET sql = ? WHERE type = 'table' AND name = 'user'", (new_sql,))
    bind.exec_driver_sql(f"PRAGMA schema_version = {version + 1}")
    bind.exec_driver_sql("PRAGMA writable_schema = OFF")


def upgrade():
    role = op.create_table(
        'role',
        sa.Column('key', sa.String(length=20), nullable=False),
        sa.Column('name', sa.String(length=80), nullable=False),
        sa.Column('description', sa.String(length=255), nullable=True),
        sa.Column('permissions', sa.JSON(), nullable=False),
        sa.Column('oidc_groups', sa.String(length=255), nullable=True),
        sa.PrimaryKeyConstraint('key'),
        sa.UniqueConstraint('name'),
    )
    op.bulk_insert(role, [{"key": k, "name": n, "permissions": p} for k, n, p in ROLES])
    _set_user_sql(lambda sql: re.sub(r",\s*CONSTRAINT ck_user_role CHECK \([^)]*\)\)", "", sql))


def downgrade():
    op.execute("UPDATE user SET role = 'emprunteur' WHERE role NOT IN ('admin', 'prof', 'emprunteur')")
    _set_user_sql(lambda sql: sql if "ck_user_role" in sql
                  else re.sub(r",(\s*)PRIMARY KEY \(id\)", rf",\1PRIMARY KEY (id),\1{CHECK}", sql, count=1))
    op.drop_table('role')
