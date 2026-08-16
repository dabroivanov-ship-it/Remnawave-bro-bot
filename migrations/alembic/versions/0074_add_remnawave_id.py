"""add remnawave_id for panel API 3.x

Revision ID: 0074
Revises: 0073
Create Date: 2026-08-02

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = '0074'
down_revision: Union[str, None] = '0073'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(table: str, column: str) -> bool:
    bind = op.get_bind()
    result = bind.execute(
        sa.text(
            'SELECT 1 FROM information_schema.columns '
            'WHERE table_name = :table AND column_name = :column'
        ),
        {'table': table, 'column': column},
    )
    return result.scalar() is not None


def _index_exists(index_name: str) -> bool:
    bind = op.get_bind()
    result = bind.execute(
        sa.text('SELECT 1 FROM pg_indexes WHERE indexname = :name'),
        {'name': index_name},
    )
    return result.scalar() is not None


def upgrade() -> None:
    if not _column_exists('users', 'remnawave_id'):
        op.add_column('users', sa.Column('remnawave_id', sa.Integer(), nullable=True))
    if not _index_exists('ix_users_remnawave_id'):
        op.create_index('ix_users_remnawave_id', 'users', ['remnawave_id'], unique=True)

    if not _column_exists('subscriptions', 'remnawave_id'):
        op.add_column('subscriptions', sa.Column('remnawave_id', sa.Integer(), nullable=True))
    if not _index_exists('ix_subscriptions_remnawave_id'):
        op.create_index('ix_subscriptions_remnawave_id', 'subscriptions', ['remnawave_id'], unique=False)


def downgrade() -> None:
    if _index_exists('ix_subscriptions_remnawave_id'):
        op.drop_index('ix_subscriptions_remnawave_id', table_name='subscriptions')
    if _column_exists('subscriptions', 'remnawave_id'):
        op.drop_column('subscriptions', 'remnawave_id')

    if _index_exists('ix_users_remnawave_id'):
        op.drop_index('ix_users_remnawave_id', table_name='users')
    if _column_exists('users', 'remnawave_id'):
        op.drop_column('users', 'remnawave_id')
