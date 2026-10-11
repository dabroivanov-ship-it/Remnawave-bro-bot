"""User-facing router shop routes."""

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.crud.router_order import create_router_order, get_user_router_orders
from app.database.crud.router_product import get_all_router_products, get_router_product_by_id
from app.database.crud.system_setting import get_setting_value
from app.database.crud.user import subtract_user_balance
from app.database.models import PaymentMethod, RouterOrderStatus, Transaction, TransactionType, User

from ..dependencies import get_cabinet_db, get_current_cabinet_user

ROUTERS_ENABLED_KEY = 'CABINET_ROUTERS_ENABLED'


async def _ensure_routers_enabled(db: AsyncSession) -> None:
    value = await get_setting_value(db, ROUTERS_ENABLED_KEY)
    # Unset → enabled (legacy). Explicit "false" disables the shop.
    if value is not None and value.lower() != 'true':
        raise HTTPException(status.HTTP_403_FORBIDDEN, 'Router shop is disabled')
from ..schemas.routers import (
    RouterOrderCreateRequest,
    RouterOrderResponse,
    RouterShopListResponse,
    RouterShopProduct,
    RouterShopTariff,
)


logger = structlog.get_logger(__name__)

router = APIRouter(prefix='/routers', tags=['Cabinet Routers Shop'])


def _serialize_shop_product(product) -> RouterShopProduct:
    active_tariffs = [
        RouterShopTariff(
            id=t.id,
            name=t.name,
            description=t.description,
            price_kopeks=t.price_kopeks,
        )
        for t in product.tariffs
        if t.is_active
    ]
    return RouterShopProduct(
        id=product.id,
        name=product.name,
        description=product.description,
        image_url=product.image_url,
        tariffs=active_tariffs,
    )


async def _send_router_order_notification(user: User, order) -> None:
    try:
        from aiogram import Bot

        from app.services.admin_notification_service import AdminNotificationService

        if getattr(settings, 'ADMIN_NOTIFICATIONS_ENABLED', False) and settings.BOT_TOKEN:
            bot = Bot(token=settings.BOT_TOKEN)
            try:
                notification_service = AdminNotificationService(bot)
                await notification_service.send_router_order_notification(user=user, order=order)
            finally:
                await bot.session.close()
    except Exception as e:
        logger.error('Failed to send admin notification for router order', error=e, order_id=order.id)


@router.get('', response_model=RouterShopListResponse)
async def list_shop_routers(
    db: AsyncSession = Depends(get_cabinet_db),
) -> RouterShopListResponse:
    """Public catalog. Same active products and tariffs as the logged-in cabinet."""
    await _ensure_routers_enabled(db)
    products = await get_all_router_products(db, include_inactive=False)
    items = [_serialize_shop_product(p) for p in products if any(t.is_active for t in p.tariffs)]
    return RouterShopListResponse(items=items)


@router.get('/orders/history', response_model=list[RouterOrderResponse])
async def get_router_orders_history(
    user: User = Depends(get_current_cabinet_user),
    db: AsyncSession = Depends(get_cabinet_db),
) -> list[RouterOrderResponse]:
    await _ensure_routers_enabled(db)
    orders = await get_user_router_orders(db, user.id)
    return [RouterOrderResponse.model_validate(o) for o in orders]


@router.post('/orders', response_model=RouterOrderResponse, status_code=status.HTTP_201_CREATED)
async def create_router_order_endpoint(
    payload: RouterOrderCreateRequest,
    user: User = Depends(get_current_cabinet_user),
    db: AsyncSession = Depends(get_cabinet_db),
) -> RouterOrderResponse:
    await _ensure_routers_enabled(db)
    product = await get_router_product_by_id(db, payload.router_product_id)
    if not product or not product.is_active:
        raise HTTPException(status.HTTP_404_NOT_FOUND, 'Router not found')

    tariff = next((t for t in product.tariffs if t.id == payload.router_product_tariff_id), None)
    if not tariff or not tariff.is_active:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, 'Selected tariff is not available')

    amount_kopeks = tariff.price_kopeks
    if amount_kopeks <= 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, 'Invalid tariff price')

    if user.balance_kopeks < amount_kopeks:
        missing = amount_kopeks - user.balance_kopeks
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail={
                'code': 'insufficient_funds',
                'message': 'Insufficient balance',
                'missing_amount': missing,
                'required_amount': amount_kopeks,
                'current_balance': user.balance_kopeks,
            },
        )

    description = f'Покупка роутера: {product.name} — {tariff.name}'
    success = await subtract_user_balance(
        db,
        user,
        amount_kopeks,
        description,
        create_transaction=True,
        payment_method=PaymentMethod.BALANCE,
        transaction_type=TransactionType.ROUTER_ORDER,
        commit=False,
    )
    if not success:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail={
                'code': 'insufficient_funds',
                'message': 'Insufficient balance',
                'missing_amount': max(amount_kopeks - user.balance_kopeks, 0),
                'required_amount': amount_kopeks,
                'current_balance': user.balance_kopeks,
            },
        )

    tx_result = await db.execute(
        select(Transaction)
        .where(Transaction.user_id == user.id, Transaction.type == TransactionType.ROUTER_ORDER.value)
        .order_by(desc(Transaction.id))
        .limit(1)
    )
    transaction = tx_result.scalars().first()

    order = await create_router_order(
        db,
        user_id=user.id,
        router_product_id=product.id,
        router_product_tariff_id=tariff.id,
        product_name=product.name,
        tariff_name=tariff.name,
        amount_kopeks=amount_kopeks,
        delivery_provider=payload.delivery_provider,
        recipient_name=payload.recipient_name.strip(),
        recipient_phone=payload.recipient_phone.strip(),
        recipient_address=payload.recipient_address.strip(),
        recipient_comment=(payload.recipient_comment or '').strip() or None,
        transaction_id=transaction.id if transaction else None,
        status=RouterOrderStatus.PAID.value,
    )

    await db.commit()
    await db.refresh(order)
    await db.refresh(user)

    await _send_router_order_notification(user, order)

    return RouterOrderResponse.model_validate(order)


@router.get('/{product_id}', response_model=RouterShopProduct)
async def get_shop_router(
    product_id: int,
    user: User = Depends(get_current_cabinet_user),
    db: AsyncSession = Depends(get_cabinet_db),
) -> RouterShopProduct:
    await _ensure_routers_enabled(db)
    product = await get_router_product_by_id(db, product_id)
    if not product or not product.is_active:
        raise HTTPException(status.HTTP_404_NOT_FOUND, 'Router not found')
    shop_product = _serialize_shop_product(product)
    if not shop_product.tariffs:
        raise HTTPException(status.HTTP_404_NOT_FOUND, 'Router not available')
    return shop_product
