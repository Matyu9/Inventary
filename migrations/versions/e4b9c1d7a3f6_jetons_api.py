"""jetons d'accès à l'API en lecture seule

Revision ID: e4b9c1d7a3f6
Revises: c3e7a9d1f5b2
Create Date: 2026-11-02 10:00:00.000000

"""
import json

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e4b9c1d7a3f6'
down_revision = 'c3e7a9d1f5b2'
branch_labels = None
depends_on = None

PERMISSION = 'api.use'


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
        'api_token',
        sa.Column('id', sa.String(length=64), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=80), nullable=False),
        sa.Column('prefix', sa.String(length=16), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=True),
        sa.Column('last_used_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['user.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_api_token_user_id', 'api_token', ['user_id'], unique=False)
    _update_roles(lambda perms: perms | {PERMISSION}, keys={'prof'})


def downgrade():
    _update_roles(lambda perms: perms - {PERMISSION})
    op.drop_index('ix_api_token_user_id', table_name='api_token')
    op.drop_table('api_token')
