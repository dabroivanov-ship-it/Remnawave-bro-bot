"""landing router products support

Revision ID: 0073
Revises: 0072
Create Date: 2026-06-06

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '0073'
down_revision: Union[str, None] = '0072'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(table: str, column: str) -> bool:
    bind = op.get_bind()
    result = bind.execute(
        sa.text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = :table AND column_name = :column"
        ),
        {'table': table, 'column': column},
    )
    return result.scalar() is not None


def upgrade() -> None:
    if not _column_exists('landing_pages', 'product_type'):
        op.add_column(
            'landing_pages',
            sa.Column(
                'product_type',
                sa.String(20),
                nullable=False,
                server_default='tariff',
            ),
        )
    if not _column_exists('landing_pages', 'allowed_router_product_ids'):
        op.add_column(
            'landing_pages',
            sa.Column('allowed_router_product_ids', sa.JSON(), nullable=False, server_default='[]'),
        )

    if not _column_exists('guest_purchases', 'router_product_id'):
        op.add_column(
            'guest_purchases',
            sa.Column(
                'router_product_id',
                sa.Integer(),
                sa.ForeignKey('router_products.id', ondelete='SET NULL'),
                nullable=True,
            ),
        )
    if not _column_exists('guest_purchases', 'router_product_tariff_id'):
        op.add_column(
            'guest_purchases',
            sa.Column(
                'router_product_tariff_id',
                sa.Integer(),
                sa.ForeignKey('router_product_tariffs.id', ondelete='SET NULL'),
                nullable=True,
            ),
        )
    if not _column_exists('guest_purchases', 'router_order_id'):
        op.add_column(
            'guest_purchases',
            sa.Column(
                'router_order_id',
                sa.Integer(),
                sa.ForeignKey('router_orders.id', ondelete='SET NULL'),
                nullable=True,
            ),
        )
    for col_name, col_type in (
        ('delivery_provider', sa.String(30)),
        ('recipient_name', sa.String(255)),
        ('recipient_phone', sa.String(50)),
        ('recipient_address', sa.Text()),
        ('recipient_comment', sa.Text()),
    ):
        if not _column_exists('guest_purchases', col_name):
            op.add_column('guest_purchases', sa.Column(col_name, col_type, nullable=True))


def downgrade() -> None:
    for col in (
        'recipient_comment',
        'recipient_address',
        'recipient_phone',
        'recipient_name',
        'delivery_provider',
        'router_order_id',
        'router_product_tariff_id',
        'router_product_id',
    ):
        if _column_exists('guest_purchases', col):
            op.drop_column('guest_purchases', col)

    if _column_exists('landing_pages', 'allowed_router_product_ids'):
        op.drop_column('landing_pages', 'allowed_router_product_ids')
    if _column_exists('landing_pages', 'product_type'):
        op.drop_column('landing_pages', 'product_type')
