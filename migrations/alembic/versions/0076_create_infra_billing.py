"""create infra billing tables

Revision ID: 0076
Revises: 0075
Create Date: 2026-08-12

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '0076'
down_revision: Union[str, None] = '0075'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if not _table_exists('infra_providers'):
        op.create_table(
            'infra_providers',
            sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column('name', sa.String(255), nullable=False),
            sa.Column('website', sa.String(500), nullable=True),
            sa.Column('login_url', sa.String(500), nullable=True),
            sa.Column('notes', sa.Text(), nullable=True),
            sa.Column('currency', sa.String(10), nullable=False, server_default='RUB'),
            sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.UniqueConstraint('name', name='uq_infra_providers_name'),
        )

    if not _table_exists('infra_servers'):
        op.create_table(
            'infra_servers',
            sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                'provider_id',
                sa.Integer(),
                sa.ForeignKey('infra_providers.id', ondelete='SET NULL'),
                nullable=True,
                index=True,
            ),
            sa.Column('remnawave_node_uuid', sa.String(64), nullable=True),
            sa.Column('name', sa.String(255), nullable=False),
            sa.Column('address', sa.String(255), nullable=True),
            sa.Column('country_code', sa.String(10), nullable=True),
            sa.Column('billing_amount_kopeks', sa.Integer(), nullable=True),
            sa.Column('billing_cycle_days', sa.Integer(), nullable=False, server_default='30'),
            sa.Column('next_due_date', sa.Date(), nullable=True),
            sa.Column('last_paid_at', sa.DateTime(timezone=True), nullable=True),
            sa.Column('payment_status', sa.String(20), nullable=False, server_default='unknown'),
            sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
            sa.Column('notes', sa.Text(), nullable=True),
            sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.UniqueConstraint('remnawave_node_uuid', name='uq_infra_servers_remnawave_node_uuid'),
        )

    if not _table_exists('infra_payments'):
        op.create_table(
            'infra_payments',
            sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                'server_id',
                sa.Integer(),
                sa.ForeignKey('infra_servers.id', ondelete='CASCADE'),
                nullable=False,
                index=True,
            ),
            sa.Column(
                'provider_id',
                sa.Integer(),
                sa.ForeignKey('infra_providers.id', ondelete='SET NULL'),
                nullable=True,
                index=True,
            ),
            sa.Column('amount_kopeks', sa.Integer(), nullable=False),
            sa.Column('paid_at', sa.DateTime(timezone=True), nullable=False),
            sa.Column('due_date', sa.Date(), nullable=True),
            sa.Column('source', sa.String(20), nullable=False, server_default='manual'),
            sa.Column('source_event', sa.String(100), nullable=True),
            sa.Column('notes', sa.Text(), nullable=True),
            sa.Column(
                'created_by_user_id',
                sa.Integer(),
                sa.ForeignKey('users.id', ondelete='SET NULL'),
                nullable=True,
            ),
            sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        )


def downgrade() -> None:
    if _table_exists('infra_payments'):
        op.drop_table('infra_payments')
    if _table_exists('infra_servers'):
        op.drop_table('infra_servers')
    if _table_exists('infra_providers'):
        op.drop_table('infra_providers')


def _table_exists(table_name: str) -> bool:
    bind = op.get_bind()
    result = bind.execute(
        sa.text('SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = :name)'),
        {'name': table_name},
    )
    return result.scalar()
