"""create admin_login_attempts table

Revision ID: 0068
Revises: 0067
Create Date: 2026-04-29
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '0068'
down_revision: Union[str, None] = '0067'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(table_name: str) -> bool:
    conn = op.get_bind()
    result = conn.execute(
        sa.text(
            'SELECT EXISTS (SELECT 1 FROM information_schema.tables '
            "WHERE table_schema = 'public' AND table_name = :name)"
        ),
        {'name': table_name},
    )
    return result.scalar()


def upgrade() -> None:
    if _has_table('admin_login_attempts'):
        return

    op.create_table(
        'admin_login_attempts',
        sa.Column('id', sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('email', sa.String(255), nullable=True),
        sa.Column('telegram_id', sa.BigInteger(), nullable=True),
        sa.Column('auth_method', sa.String(50), nullable=False),
        sa.Column('status', sa.String(20), nullable=False),
        sa.Column('failure_reason', sa.String(255), nullable=True),
        sa.Column('ip_address', sa.String(45), nullable=True),
        sa.Column('user_agent', sa.Text(), nullable=True),
        sa.Column('is_legacy_admin', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index('ix_admin_login_attempts_created', 'admin_login_attempts', ['created_at'])
    op.create_index(
        'ix_admin_login_attempts_user_created',
        'admin_login_attempts',
        ['user_id', 'created_at'],
    )
    op.create_index(
        'ix_admin_login_attempts_status_created',
        'admin_login_attempts',
        ['status', 'created_at'],
    )


def downgrade() -> None:
    if not _has_table('admin_login_attempts'):
        return
    op.drop_index('ix_admin_login_attempts_status_created', table_name='admin_login_attempts')
    op.drop_index('ix_admin_login_attempts_user_created', table_name='admin_login_attempts')
    op.drop_index('ix_admin_login_attempts_created', table_name='admin_login_attempts')
    op.drop_table('admin_login_attempts')
