"""déconnexion OIDC : sid et jeton d'identité des sessions, identifiant externe des personnes

Revision ID: f5c2a8e4d1b3
Revises: e4b9c1d7a3f6
Create Date: 2026-11-07 14:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f5c2a8e4d1b3'
down_revision = 'e4b9c1d7a3f6'
branch_labels = None
depends_on = None


def upgrade():
    # Ajouts simples (ALTER TABLE) : user est référencé par d'autres tables, il ne doit pas être recréé.
    with op.batch_alter_table('login_session', schema=None) as batch_op:
        batch_op.add_column(sa.Column('oidc_sid', sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column('oidc_id_token', sa.Text(), nullable=True))
        batch_op.create_index(batch_op.f('ix_login_session_oidc_sid'), ['oidc_sid'], unique=False)

    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.add_column(sa.Column('external_id', sa.String(length=64), nullable=True))
        batch_op.create_index(batch_op.f('ix_user_external_id'), ['external_id'], unique=True)


def downgrade():
    # Hors mode batch : ALTER TABLE DROP COLUMN natif, sans recréer `user` (refusé à cause des clés étrangères).
    op.drop_index('ix_user_external_id', table_name='user')
    op.drop_column('user', 'external_id')

    with op.batch_alter_table('login_session', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_login_session_oidc_sid'))
        batch_op.drop_column('oidc_id_token')
        batch_op.drop_column('oidc_sid')
