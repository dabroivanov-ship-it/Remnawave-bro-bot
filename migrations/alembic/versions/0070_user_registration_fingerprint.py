"""add registration_ip and registration_user_agent to users

Revision ID: 0070
Revises: 0069
Create Date: 2026-06-01
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = '0070'
down_revision: Union[str, None] = '0069'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(table_name: str, column_name: str) -> bool:
    conn = op.get_bind()
    result = conn.execute(
        sa.text(
            'SELECT EXISTS (SELECT 1 FROM information_schema.columns '
            'WHERE table_schema = \'public\' AND table_name = :table AND column_name = :column)'
        ),
        {'table': table_name, 'column': column_name},
    )
    return bool(result.scalar())


def upgrade() -> None:
    if not _has_column('users', 'registration_ip'):
        op.add_column('users', sa.Column('registration_ip', sa.String(45), nullable=True))
    if not _has_column('users', 'registration_user_agent'):
        op.add_column('users', sa.Column('registration_user_agent', sa.Text(), nullable=True))
    # Index to quickly find accounts sharing the same registration IP
    conn = op.get_bind()
    existing_indexes = conn.execute(
        sa.text("SELECT indexname FROM pg_indexes WHERE tablename = 'users'")
    ).scalars().all()
    if 'ix_users_registration_ip' not in existing_indexes:
        op.create_index('ix_users_registration_ip', 'users', ['registration_ip'])


def downgrade() -> None:
    conn = op.get_bind()
    existing_indexes = conn.execute(
        sa.text("SELECT indexname FROM pg_indexes WHERE tablename = 'users'")
    ).scalars().all()
    if 'ix_users_registration_ip' in existing_indexes:
        op.drop_index('ix_users_registration_ip', table_name='users')
    if _has_column('users', 'registration_user_agent'):
        op.drop_column('users', 'registration_user_agent')
    if _has_column('users', 'registration_ip'):
        op.drop_column('users', 'registration_ip')
