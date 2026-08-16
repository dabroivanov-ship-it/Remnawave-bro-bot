"""add is_brute_force to admin_login_attempts and cabinet_error_logs table

Revision ID: 0069
Revises: 0068
Create Date: 2026-05-20
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '0069'
down_revision: Union[str, None] = '0068'
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


def _has_column(table_name: str, column_name: str) -> bool:
    conn = op.get_bind()
    result = conn.execute(
        sa.text(
            'SELECT EXISTS (SELECT 1 FROM information_schema.columns '
            "WHERE table_schema = 'public' AND table_name = :table AND column_name = :col)"
        ),
        {'table': table_name, 'col': column_name},
    )
    return result.scalar()


def upgrade() -> None:
    if _has_table('admin_login_attempts') and not _has_column('admin_login_attempts', 'is_brute_force'):
        op.add_column(
            'admin_login_attempts',
            sa.Column('is_brute_force', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        )
        op.create_index(
            'ix_admin_login_attempts_ip_created',
            'admin_login_attempts',
            ['ip_address', 'created_at'],
        )

    if _has_table('cabinet_error_logs'):
        return

    op.create_table(
        'cabinet_error_logs',
        sa.Column('id', sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('source', sa.String(20), nullable=False),
        sa.Column('level', sa.String(20), server_default='error', nullable=False),
        sa.Column('message', sa.Text(), nullable=False),
        sa.Column('stack_trace', sa.Text(), nullable=True),
        sa.Column('page_url', sa.Text(), nullable=True),
        sa.Column('component', sa.String(255), nullable=True),
        sa.Column('ip_address', sa.String(45), nullable=True),
        sa.Column('user_agent', sa.Text(), nullable=True),
        sa.Column('context_json', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index('ix_cabinet_error_logs_created', 'cabinet_error_logs', ['created_at'])
    op.create_index('ix_cabinet_error_logs_source_created', 'cabinet_error_logs', ['source', 'created_at'])
    op.create_index('ix_cabinet_error_logs_level_created', 'cabinet_error_logs', ['level', 'created_at'])


def downgrade() -> None:
    if _has_table('cabinet_error_logs'):
        op.drop_index('ix_cabinet_error_logs_level_created', table_name='cabinet_error_logs')
        op.drop_index('ix_cabinet_error_logs_source_created', table_name='cabinet_error_logs')
        op.drop_index('ix_cabinet_error_logs_created', table_name='cabinet_error_logs')
        op.drop_table('cabinet_error_logs')

    if _has_table('admin_login_attempts'):
        if _has_column('admin_login_attempts', 'is_brute_force'):
            op.drop_index('ix_admin_login_attempts_ip_created', table_name='admin_login_attempts')
            op.drop_column('admin_login_attempts', 'is_brute_force')
