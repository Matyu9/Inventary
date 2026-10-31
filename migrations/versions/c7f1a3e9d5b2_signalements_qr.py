"""signalements par QR code

Revision ID: c7f1a3e9d5b2
Revises: b4e8c2d6f9a1
Create Date: 2026-11-01 14:00:00.000000

"""
import json

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c7f1a3e9d5b2'
down_revision = 'b4e8c2d6f9a1'
branch_labels = None
depends_on = None

PERMISSION = 'report.handle'


def _update_roles(change, keys=None):
    bind = op.get_bind()
    for key, perms in bind.execute(sa.text("SELECT key, permissions FROM role")).all():
        if keys is not None and key not in keys:
            continue
        perms = perms if isinstance(perms, list) else json.loads(perms or "[]")
        bind.execute(sa.text("UPDATE role SET permissions = :p WHERE key = :k"),
                     {"p": json.dumps(sorted(change(set(perms)))), "k": key})


def upgrade():
    op.create_table(
        'public_report',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('item_id', sa.Integer(), nullable=True),
        sa.Column('kit_id', sa.Integer(), nullable=True),
        sa.Column('kind', sa.String(length=20), nullable=False),
        sa.Column('message', sa.Text(), nullable=False),
        sa.Column('name', sa.String(length=120), nullable=True),
        sa.Column('contact', sa.String(length=160), nullable=True),
        sa.Column('reported_by_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('handled_at', sa.DateTime(), nullable=True),
        sa.Column('handled_by_id', sa.Integer(), nullable=True),
        sa.Column('handled_note', sa.String(length=500), nullable=True),
        sa.CheckConstraint('(item_id IS NULL) != (kit_id IS NULL)', name='ck_public_report_item_xor_kit'),
        sa.CheckConstraint("kind IN ('probleme', 'trouve')", name='ck_public_report_kind'),
        sa.ForeignKeyConstraint(['item_id'], ['item.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['kit_id'], ['kit.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['reported_by_id'], ['user.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['handled_by_id'], ['user.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_public_report_item_id', 'public_report', ['item_id'], unique=False)
    op.create_index('ix_public_report_kit_id', 'public_report', ['kit_id'], unique=False)
    op.create_index('ix_public_report_created_at', 'public_report', ['created_at'], unique=False)

    # `notification` n'est référencée par aucune table : la recréer (mode batch) pour la clé étrangère est sans risque.
    with op.batch_alter_table('notification', schema=None) as batch_op:
        batch_op.add_column(sa.Column('report_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key('fk_notification_report_id_public_report', 'public_report', ['report_id'],
                                    ['id'], ondelete='SET NULL')

    # Les rôles enregistrés ne reprennent pas les droits par défaut : le professeur reçoit le nouveau droit.
    _update_roles(lambda perms: perms | {PERMISSION}, keys={'prof'})


def downgrade():
    _update_roles(lambda perms: perms - {PERMISSION})
    with op.batch_alter_table('notification', schema=None) as batch_op:
        batch_op.drop_constraint('fk_notification_report_id_public_report', type_='foreignkey')
        batch_op.drop_column('report_id')
    op.drop_index('ix_public_report_created_at', table_name='public_report')
    op.drop_index('ix_public_report_kit_id', table_name='public_report')
    op.drop_index('ix_public_report_item_id', table_name='public_report')
    op.drop_table('public_report')
