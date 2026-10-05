"""store IPs for the RemnaWave request map

Revision ID: 0077
Revises: 0076
Create Date: 2026-10-05

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = '0077'
down_revision: Union[str, None] = '0076'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if _table_exists('remnawave_request_events'):
        return
    op.create_table(
        'remnawave_request_events',
        sa.Column('id', sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column('event_type', sa.String(32), nullable=False),
        sa.Column('ip_address', sa.String(45), nullable=False),
        sa.Column('occurred_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('panel_user_id', sa.Integer(), nullable=True),
        sa.Column('dedupe_key', sa.String(160), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint('dedupe_key', name='uq_remnawave_request_events_dedupe'),
    )
    op.create_index('ix_remnawave_request_events_occurred', 'remnawave_request_events', ['occurred_at'])
    op.create_index(
        'ix_remnawave_request_events_type_occurred',
        'remnawave_request_events',
        ['event_type', 'occurred_at'],
    )


def downgrade() -> None:
    if _table_exists('remnawave_request_events'):
        op.drop_table('remnawave_request_events')


def _table_exists(table_name: str) -> bool:
    bind = op.get_bind()
    result = bind.execute(
        sa.text('SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = :name)'),
        {'name': table_name},
    )
    return bool(result.scalar())
