from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database.models import RouterProduct, RouterProductTariff


async def get_all_router_products(
    db: AsyncSession,
    *,
    include_inactive: bool = True,
) -> list[RouterProduct]:
    query = select(RouterProduct).options(selectinload(RouterProduct.tariffs))

    if not include_inactive:
        query = query.where(RouterProduct.is_active.is_(True))

    query = query.order_by(RouterProduct.display_order, RouterProduct.id)
    result = await db.execute(query)
    return result.scalars().unique().all()


async def get_router_product_by_id(db: AsyncSession, product_id: int) -> RouterProduct | None:
    query = (
        select(RouterProduct)
        .where(RouterProduct.id == product_id)
        .options(selectinload(RouterProduct.tariffs))
    )
    result = await db.execute(query)
    return result.scalars().unique().first()


async def create_router_product(
    db: AsyncSession,
    *,
    name: str,
    description: str | None = None,
    image_url: str | None = None,
    display_order: int = 0,
    is_active: bool = True,
    tariffs: list[dict] | None = None,
) -> RouterProduct:
    product = RouterProduct(
        name=name,
        description=description,
        image_url=image_url,
        display_order=display_order,
        is_active=is_active,
    )
    db.add(product)
    await db.flush()

    for index, tariff_data in enumerate(tariffs or []):
        tariff = RouterProductTariff(
            router_product_id=product.id,
            name=tariff_data['name'],
            description=tariff_data.get('description'),
            price_kopeks=tariff_data.get('price_kopeks', 0),
            display_order=tariff_data.get('display_order', index),
            is_active=tariff_data.get('is_active', True),
        )
        db.add(tariff)

    await db.flush()
    await db.refresh(product, attribute_names=['tariffs'])
    return product


async def update_router_product(
    db: AsyncSession,
    product: RouterProduct,
    *,
    name: str | None = None,
    description: str | None = None,
    image_url: str | None = None,
    display_order: int | None = None,
    is_active: bool | None = None,
    tariffs: list[dict] | None = None,
) -> RouterProduct:
    if name is not None:
        product.name = name
    if description is not None:
        product.description = description
    if image_url is not None:
        product.image_url = image_url
    if display_order is not None:
        product.display_order = display_order
    if is_active is not None:
        product.is_active = is_active

    if tariffs is not None:
        existing_by_id = {t.id: t for t in product.tariffs}
        payload_ids = {t['id'] for t in tariffs if t.get('id')}

        for tariff in list(product.tariffs):
            if tariff.id not in payload_ids:
                await db.delete(tariff)

        for index, tariff_data in enumerate(tariffs):
            tariff_id = tariff_data.get('id')
            if tariff_id and tariff_id in existing_by_id:
                tariff = existing_by_id[tariff_id]
                tariff.name = tariff_data['name']
                tariff.description = tariff_data.get('description')
                tariff.price_kopeks = tariff_data.get('price_kopeks', 0)
                tariff.display_order = tariff_data.get('display_order', index)
                tariff.is_active = tariff_data.get('is_active', True)
            else:
                db.add(
                    RouterProductTariff(
                        router_product_id=product.id,
                        name=tariff_data['name'],
                        description=tariff_data.get('description'),
                        price_kopeks=tariff_data.get('price_kopeks', 0),
                        display_order=tariff_data.get('display_order', index),
                        is_active=tariff_data.get('is_active', True),
                    )
                )

    await db.flush()
    await db.refresh(product, attribute_names=['tariffs'])
    return product


async def delete_router_product(db: AsyncSession, product: RouterProduct) -> None:
    await db.delete(product)
    await db.flush()


async def toggle_router_product(db: AsyncSession, product: RouterProduct) -> RouterProduct:
    product.is_active = not product.is_active
    await db.flush()
    return product


async def reorder_router_products(db: AsyncSession, order: list[int]) -> None:
    for index, product_id in enumerate(order):
        result = await db.execute(select(RouterProduct).where(RouterProduct.id == product_id))
        product = result.scalars().first()
        if product:
            product.display_order = index
    await db.flush()


async def count_router_products(db: AsyncSession, *, include_inactive: bool = True) -> int:
    query = select(func.count(RouterProduct.id))
    if not include_inactive:
        query = query.where(RouterProduct.is_active.is_(True))
    result = await db.execute(query)
    return int(result.scalar_one())
