"""Admin routes for autopayments / saved payment methods analytics."""

import math
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.crud.saved_payment_method import (
    deactivate_payment_method,
    get_autopayments_stats,
    get_saved_payment_method_by_id,
    list_users_with_saved_payment_methods,
)
from app.database.models import SavedPaymentMethod, User
from app.services.permission_service import PermissionService
from app.utils.payment_logger import payment_logger as logger

from ..dependencies import get_cabinet_db, require_permission


router = APIRouter(prefix='/admin/autopayments', tags=['Cabinet Admin Autopayments'])


class SavedCardItem(BaseModel):
    id: int
    method_type: str
    card_last4: str | None = None
    card_type: str | None = None
    card_expiry_month: str | None = None
    card_expiry_year: str | None = None
    title: str | None = None
    created_at: datetime


class AutopaymentUserItem(BaseModel):
    user_id: int
    telegram_id: int | None = None
    username: str | None = None
    full_name: str
    email: str | None = None
    balance_kopeks: int
    autopay_enabled: bool
    cards_count: int
    cards: list[SavedCardItem]
    last_linked_at: datetime | None = None


class AutopaymentsListResponse(BaseModel):
    items: list[AutopaymentUserItem]
    total: int
    page: int
    per_page: int
    pages: int


class AutopaymentsStatsResponse(BaseModel):
    users_with_cards: int
    total_active_cards: int
    users_with_autopay: int
    by_method_type: dict[str, int]


class ChargeSavedCardRequest(BaseModel):
    amount_kopeks: int = Field(..., gt=0, description='Сумма списания в копейках')


class ChargeSavedCardResponse(BaseModel):
    success: bool
    message: str
    yookassa_payment_id: str | None = None
    local_payment_id: int | None = None
    status: str | None = None
    paid: bool = False
    amount_kopeks: int | None = None


class UnlinkSavedCardResponse(BaseModel):
    success: bool
    message: str


def _user_has_autopay(user: User) -> bool:
    return any(getattr(sub, 'autopay_enabled', False) for sub in (user.subscriptions or []))


def _serialize_card(card: SavedPaymentMethod) -> SavedCardItem:
    return SavedCardItem(
        id=card.id,
        method_type=card.method_type or 'bank_card',
        card_last4=card.card_last4,
        card_type=card.card_type,
        card_expiry_month=card.card_expiry_month,
        card_expiry_year=card.card_expiry_year,
        title=card.title,
        created_at=card.created_at,
    )


@router.get('/stats', response_model=AutopaymentsStatsResponse)
async def autopayments_stats(
    search: str | None = Query(None, max_length=255),
    method_type: str | None = Query(None, max_length=50),
    autopay_only: bool = Query(False),
    _: User = Depends(require_permission('payments:read')),
    db: AsyncSession = Depends(get_cabinet_db),
) -> AutopaymentsStatsResponse:
    stats = await get_autopayments_stats(
        db,
        search=search,
        method_type=method_type,
        autopay_only=autopay_only,
    )
    return AutopaymentsStatsResponse(**stats)


@router.get('', response_model=AutopaymentsListResponse)
async def list_autopayment_users(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    search: str | None = Query(None, max_length=255),
    method_type: str | None = Query(None, max_length=50),
    autopay_only: bool = Query(False),
    _: User = Depends(require_permission('payments:read')),
    db: AsyncSession = Depends(get_cabinet_db),
) -> AutopaymentsListResponse:
    offset = (page - 1) * per_page
    rows, total = await list_users_with_saved_payment_methods(
        db,
        offset=offset,
        limit=per_page,
        search=search,
        method_type=method_type,
        autopay_only=autopay_only,
    )

    items: list[AutopaymentUserItem] = []
    for user, cards in rows:
        active_cards = cards
        if method_type:
            active_cards = [c for c in cards if (c.method_type or '') == method_type]
        last_linked = max((c.created_at for c in active_cards), default=None)
        items.append(
            AutopaymentUserItem(
                user_id=user.id,
                telegram_id=user.telegram_id,
                username=user.username,
                full_name=user.full_name,
                email=user.email,
                balance_kopeks=user.balance_kopeks or 0,
                autopay_enabled=_user_has_autopay(user),
                cards_count=len(active_cards),
                cards=[_serialize_card(c) for c in active_cards],
                last_linked_at=last_linked,
            )
        )

    pages = math.ceil(total / per_page) if total else 0
    return AutopaymentsListResponse(
        items=items,
        total=total,
        page=page,
        per_page=per_page,
        pages=pages,
    )


@router.post('/cards/{card_id}/charge', response_model=ChargeSavedCardResponse)
async def charge_saved_card(
    card_id: int,
    request: ChargeSavedCardRequest,
    admin: User = Depends(require_permission('payments:edit')),
    db: AsyncSession = Depends(get_cabinet_db),
) -> ChargeSavedCardResponse:
    """Списать сумму с привязанной карты и пополнить баланс пользователя."""
    if not settings.YOOKASSA_ENABLED or not settings.YOOKASSA_RECURRENT_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Recurrent YooKassa payments are not enabled',
        )

    min_amount = settings.YOOKASSA_MIN_AMOUNT_KOPEKS
    if request.amount_kopeks < min_amount:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f'Minimum amount is {min_amount} kopeks',
        )

    saved_method = await get_saved_payment_method_by_id(db, card_id, active_only=True)
    if not saved_method:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='Saved payment method not found',
        )

    method_type = (saved_method.method_type or 'bank_card').lower()
    if method_type == 'sbp' and not settings.YOOKASSA_RECURRENT_SBP_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Recurrent SBP payments are not enabled',
        )

    from app.database.crud.user import get_user_by_id
    from app.database.crud.yookassa import create_yookassa_payment
    from app.services.payment_service import PaymentService

    user = await get_user_by_id(db, saved_method.user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='User not found',
        )

    payment_service = PaymentService()
    yookassa_service = payment_service.yookassa_service
    if not yookassa_service or not yookassa_service.configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail='YooKassa is not configured',
        )

    amount_kopeks = request.amount_kopeks
    amount_rubles = amount_kopeks / 100
    description = settings.get_balance_payment_description(
        amount_kopeks, telegram_user_id=user.telegram_id, user_db_id=user.id
    )
    metadata = {
        'user_id': str(user.id),
        'user_telegram_id': str(user.telegram_id) if user.telegram_id else '',
        'amount_kopeks': str(amount_kopeks),
        'type': 'balance_topup',
        'purpose': 'admin_card_charge',
        'source': 'admin_autopayments',
        'admin_id': str(admin.id),
        'saved_method_id': str(saved_method.id),
    }

    idem_key = f'admin_charge_{admin.id}_{saved_method.id}_{amount_kopeks}_{datetime.now(UTC).strftime("%Y%m%d%H%M%S")}'
    result = await yookassa_service.create_autopayment(
        amount=amount_rubles,
        currency='RUB',
        description=description,
        payment_method_id=saved_method.yookassa_payment_method_id,
        metadata=metadata,
        idempotence_key=idem_key,
    )

    if not result:
        logger.warning(
            'Admin card charge failed at YooKassa',
            admin_id=admin.id,
            user_id=user.id,
            card_id=card_id,
            amount_kopeks=amount_kopeks,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail='YooKassa rejected the charge (card may be invalid or declined)',
        )

    yookassa_created_at = None
    if result.get('created_at'):
        try:
            yookassa_created_at = datetime.fromisoformat(result['created_at'].replace('Z', '+00:00'))
        except Exception:
            yookassa_created_at = None

    local_payment = await create_yookassa_payment(
        db=db,
        user_id=user.id,
        yookassa_payment_id=result['id'],
        amount_kopeks=amount_kopeks,
        currency='RUB',
        description=description,
        status=result.get('status', 'pending'),
        metadata_json=metadata,
        yookassa_created_at=yookassa_created_at,
        test_mode=result.get('test_mode', False),
    )

    paid = bool(result.get('paid')) or result.get('status') == 'succeeded'
    if paid and local_payment is not None:
        try:
            await payment_service._process_successful_yookassa_payment(db, local_payment)
        except Exception as process_error:
            logger.error(
                'Admin card charge created but balance processing failed',
                admin_id=admin.id,
                user_id=user.id,
                yookassa_payment_id=result['id'],
                error=process_error,
                exc_info=True,
            )

    await PermissionService.log_action(
        db,
        user_id=admin.id,
        action='autopayments.charge_card',
        resource_type='saved_payment_method',
        resource_id=str(card_id),
        details={
            'target_user_id': user.id,
            'amount_kopeks': amount_kopeks,
            'yookassa_payment_id': result['id'],
            'status': result.get('status'),
            'paid': paid,
        },
    )
    await db.commit()

    logger.info(
        'Admin charged saved card',
        admin_id=admin.id,
        user_id=user.id,
        card_id=card_id,
        amount_kopeks=amount_kopeks,
        yookassa_payment_id=result['id'],
        paid=paid,
        status=result.get('status'),
    )

    if paid:
        message = f'Списано {amount_rubles:.2f} ₽, баланс пополнен'
    else:
        message = f'Платёж создан ({result.get("status", "pending")}), баланс пополнится после подтверждения банка'

    return ChargeSavedCardResponse(
        success=True,
        message=message,
        yookassa_payment_id=result['id'],
        local_payment_id=getattr(local_payment, 'id', None),
        status=result.get('status'),
        paid=paid,
        amount_kopeks=amount_kopeks,
    )


@router.delete('/cards/{card_id}', response_model=UnlinkSavedCardResponse)
async def unlink_saved_card(
    card_id: int,
    admin: User = Depends(require_permission('payments:edit')),
    db: AsyncSession = Depends(get_cabinet_db),
) -> UnlinkSavedCardResponse:
    """Удалить (деактивировать) привязку карты у пользователя."""
    saved_method = await get_saved_payment_method_by_id(db, card_id, active_only=True)
    if not saved_method:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='Saved payment method not found',
        )

    user_id = saved_method.user_id
    success = await deactivate_payment_method(db, card_id, user_id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='Saved payment method not found',
        )

    await PermissionService.log_action(
        db,
        user_id=admin.id,
        action='autopayments.unlink_card',
        resource_type='saved_payment_method',
        resource_id=str(card_id),
        details={
            'target_user_id': user_id,
            'method_type': saved_method.method_type,
            'card_last4': saved_method.card_last4,
        },
    )
    await db.commit()

    logger.info(
        'Admin unlinked saved card',
        admin_id=admin.id,
        user_id=user_id,
        card_id=card_id,
    )

    return UnlinkSavedCardResponse(success=True, message='Привязка удалена')
