"""gestion des comptes

Revision ID: e8a4c6d2f1b7
Revises: d5f9a2b3c7e1
Create Date: 2026-10-31 18:50:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e8a4c6d2f1b7'
down_revision = 'd5f9a2b3c7e1'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'account_token',
        sa.Column('id', sa.String(length=64), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('purpose', sa.String(length=20), nullable=False),
        sa.Column('email', sa.String(length=255), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['user.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_account_token_user_id', 'account_token', ['user_id'], unique=False)

    # `activity` n'est référencée par aucune table : la recréer (mode batch) pour la clé étrangère est sans risque.
    with op.batch_alter_table('activity', schema=None) as batch_op:
        batch_op.add_column(sa.Column('target_user_id', sa.Integer(), nullable=True))
        batch_op.create_index('ix_activity_target_user_id', ['target_user_id'], unique=False)
        batch_op.create_foreign_key('fk_activity_target_user_id_user', 'user', ['target_user_id'], ['id'],
                                    ondelete='SET NULL')

    # ALTER TABLE ADD COLUMN natif : `user` n'est pas recréée (refusé à cause des clés étrangères).
    op.add_column('login_session', sa.Column('last_seen_at', sa.DateTime(), nullable=True))
    op.add_column('login_session', sa.Column('user_agent', sa.String(length=255), nullable=True))
    op.add_column('login_session', sa.Column('ip', sa.String(length=45), nullable=True))
    op.add_column('user', sa.Column('last_login_at', sa.DateTime(), nullable=True))
    op.add_column('user', sa.Column('archived_at', sa.DateTime(), nullable=True))


def downgrade():
    # DROP COLUMN natif, sans recréer `user`.
    op.drop_column('user', 'archived_at')
    op.drop_column('user', 'last_login_at')
    op.drop_column('login_session', 'ip')
    op.drop_column('login_session', 'user_agent')
    op.drop_column('login_session', 'last_seen_at')

    with op.batch_alter_table('activity', schema=None) as batch_op:
        batch_op.drop_constraint('fk_activity_target_user_id_user', type_='foreignkey')
        batch_op.drop_index('ix_activity_target_user_id')
        batch_op.drop_column('target_user_id')

    op.drop_index('ix_account_token_user_id', table_name='account_token')
    op.drop_table('account_token')
