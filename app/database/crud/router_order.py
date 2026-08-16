from datetime import UTC, datetime

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import RouterOrder, RouterOrderStatus


async def create_router_order(
    db: AsyncSession,
    *,
    user_id: int,
    router_product_id: int | None,
    router_product_tariff_id: int | None,
    product_name: str,
    tariff_name: str,
    amount_kopeks: int,
    delivery_provider: str,
    recipient_name: str,
    recipient_phone: str,
    recipient_address: str,
    recipient_comment: str | None = None,
    transaction_id: int | None = None,
    status: str = RouterOrderStatus.PAID.value,
) -> RouterOrder:
    now = datetime.now(UTC)
    order = RouterOrder(
        user_id=user_id,
        router_product_id=router_product_id,
        router_product_tariff_id=router_product_tariff_id,
        product_name=product_name,
        tariff_name=tariff_name,
        amount_kopeks=amount_kopeks,
        status=status,
        delivery_provider=delivery_provider,
        recipient_name=recipient_name,
        recipient_phone=recipient_phone,
        recipient_address=recipient_address,
        recipient_comment=recipient_comment,
        transaction_id=transaction_id,
        paid_at=now if status == RouterOrderStatus.PAID.value else None,
    )
    db.add(order)
    await db.flush()
    await db.refresh(order)
    return order


async def get_user_router_orders(
    db: AsyncSession,
    user_id: int,
    *,
    limit: int = 20,
) -> list[RouterOrder]:
    result = await db.execute(
        select(RouterOrder)
        .where(RouterOrder.user_id == user_id)
        .order_by(desc(RouterOrder.created_at))
        .limit(limit)
    )
    return result.scalars().all()


async def get_router_order_by_id(db: AsyncSession, order_id: int) -> RouterOrder | None:
    result = await db.execute(select(RouterOrder).where(RouterOrder.id == order_id))
    return result.scalars().first()
