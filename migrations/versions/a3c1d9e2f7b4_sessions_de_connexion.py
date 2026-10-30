"""sessions de connexion

Revision ID: a3c1d9e2f7b4
Revises: 5e876e1c16d4
Create Date: 2026-10-30 21:40:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a3c1d9e2f7b4'
down_revision = '5e876e1c16d4'
branch_labels = None
depends_on = None


def upgrade():
    # Les cookies émis avant cette version ne portent pas de jeton : chacun devra se reconnecter une fois.
    op.create_table(
        'login_session',
        sa.Column('id', sa.String(length=64), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['user.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('login_session', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_login_session_user_id'), ['user_id'], unique=False)


def downgrade():
    with op.batch_alter_table('login_session', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_login_session_user_id'))
    op.drop_table('login_session')
