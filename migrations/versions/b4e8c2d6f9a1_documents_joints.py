"""documents joints

Revision ID: b4e8c2d6f9a1
Revises: a9d3e5f7b2c8
Create Date: 2026-11-01 10:00:00.000000

"""
import json

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b4e8c2d6f9a1'
down_revision = 'a9d3e5f7b2c8'
branch_labels = None
depends_on = None

NEW_PERMISSIONS = ('document.view', 'document.edit')
KINDS = ('facture', 'devis', 'notice', 'garantie', 'certificat', 'assurance', 'autre')


def _set_prof_permissions(change):
    """Les rôles enregistrés ne reprennent pas les droits par défaut : on ajoute (ou retire) ceux du professeur."""
    bind = op.get_bind()
    row = bind.execute(sa.text("SELECT permissions FROM role WHERE key = 'prof'")).first()
    if row is None:
        return
    perms = row[0] if isinstance(row[0], list) else json.loads(row[0] or "[]")
    bind.execute(sa.text("UPDATE role SET permissions = :p WHERE key = 'prof'"),
                 {"p": json.dumps(sorted(change(set(perms))))})


def upgrade():
    op.create_table(
        'document',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('item_id', sa.Integer(), nullable=True),
        sa.Column('kit_id', sa.Integer(), nullable=True),
        sa.Column('title', sa.String(length=160), nullable=False),
        sa.Column('kind', sa.String(length=20), nullable=False),
        sa.Column('filename', sa.String(length=255), nullable=False),
        sa.Column('original_name', sa.String(length=255), nullable=False),
        sa.Column('mime', sa.String(length=120), nullable=False),
        sa.Column('size', sa.Integer(), nullable=False),
        sa.Column('uploaded_by_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.CheckConstraint('(item_id IS NULL) != (kit_id IS NULL)', name='ck_document_item_xor_kit'),
        sa.CheckConstraint(f"kind IN ({', '.join(repr(k) for k in KINDS)})", name='ck_document_kind'),
        sa.ForeignKeyConstraint(['item_id'], ['item.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['kit_id'], ['kit.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['uploaded_by_id'], ['user.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_document_item_id', 'document', ['item_id'], unique=False)
    op.create_index('ix_document_kit_id', 'document', ['kit_id'], unique=False)
    _set_prof_permissions(lambda perms: perms | set(NEW_PERMISSIONS))


def downgrade():
    # Ces droits n'existent pas dans la version précédente : on les retire de tous les rôles.
    bind = op.get_bind()
    for key, perms in bind.execute(sa.text("SELECT key, permissions FROM role")).all():
        perms = perms if isinstance(perms, list) else json.loads(perms or "[]")
        bind.execute(sa.text("UPDATE role SET permissions = :p WHERE key = :k"),
                     {"p": json.dumps(sorted(set(perms) - set(NEW_PERMISSIONS))), "k": key})
    op.drop_index('ix_document_kit_id', table_name='document')
    op.drop_index('ix_document_item_id', table_name='document')
    op.drop_table('document')
