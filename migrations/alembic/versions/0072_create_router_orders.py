"""create router_orders table

Revision ID: 0072
Revises: 0071
Create Date: 2026-06-06

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '0072'
down_revision: Union[str, None] = '0071'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if not _table_exists('router_orders'):
        op.create_table(
            'router_orders',
            sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True),
            sa.Column(
                'router_product_id',
                sa.Integer(),
                sa.ForeignKey('router_products.id', ondelete='SET NULL'),
                nullable=True,
            ),
            sa.Column(
                'router_product_tariff_id',
                sa.Integer(),
                sa.ForeignKey('router_product_tariffs.id', ondelete='SET NULL'),
                nullable=True,
            ),
            sa.Column('product_name', sa.String(255), nullable=False),
            sa.Column('tariff_name', sa.String(255), nullable=False),
            sa.Column('amount_kopeks', sa.Integer(), nullable=False),
            sa.Column('status', sa.String(20), nullable=False, server_default='paid'),
            sa.Column('delivery_provider', sa.String(30), nullable=False),
            sa.Column('recipient_name', sa.String(255), nullable=False),
            sa.Column('recipient_phone', sa.String(50), nullable=False),
            sa.Column('recipient_address', sa.Text(), nullable=False),
            sa.Column('recipient_comment', sa.Text(), nullable=True),
            sa.Column('transaction_id', sa.Integer(), sa.ForeignKey('transactions.id', ondelete='SET NULL'), nullable=True),
            sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column('paid_at', sa.DateTime(timezone=True), nullable=True),
            sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        )


def downgrade() -> None:
    op.drop_table('router_orders')


def _table_exists(table_name: str) -> bool:
    bind = op.get_bind()
    result = bind.execute(
        sa.text('SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = :name)'),
        {'name': table_name},
    )
    return result.scalar()
