from datetime import UTC, datetime

import structlog
from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database.models import SavedPaymentMethod, Subscription, User


logger = structlog.get_logger(__name__)


async def create_saved_payment_method(
    db: AsyncSession,
    user_id: int,
    yookassa_payment_method_id: str,
    method_type: str = 'bank_card',
    card_first6: str | None = None,
    card_last4: str | None = None,
    card_type: str | None = None,
    card_expiry_month: str | None = None,
    card_expiry_year: str | None = None,
    title: str | None = None,
) -> SavedPaymentMethod | None:
    """Создаёт или реактивирует сохранённый метод оплаты."""

    # Проверяем, есть ли уже такой метод (включая деактивированные)
    result = await db.execute(
        update(SavedPaymentMethod)
        .where(
            SavedPaymentMethod.yookassa_payment_method_id == yookassa_payment_method_id,
            SavedPaymentMethod.user_id == user_id,
        )
        .values(
            is_active=True,
            method_type=method_type,
            card_first6=card_first6,
            card_last4=card_last4,
            card_type=card_type,
            card_expiry_month=card_expiry_month,
            card_expiry_year=card_expiry_year,
            title=title,
            updated_at=datetime.now(UTC),
        )
        .returning(SavedPaymentMethod)
    )
    reactivated = result.scalar_one_or_none()
    if reactivated:
        await db.commit()
        logger.info(
            'Реактивирован сохранённый метод оплаты',
            saved_method_id=reactivated.id,
            user_id=user_id,
            method_type=method_type,
            card_last4=card_last4,
        )
        return reactivated

    method = SavedPaymentMethod(
        user_id=user_id,
        yookassa_payment_method_id=yookassa_payment_method_id,
        method_type=method_type,
        card_first6=card_first6,
        card_last4=card_last4,
        card_type=card_type,
        card_expiry_month=card_expiry_month,
        card_expiry_year=card_expiry_year,
        title=title,
    )

    db.add(method)
    try:
        await db.commit()
    except IntegrityError as e:
        await db.rollback()
        logger.error(
            'Ошибка создания сохранённого метода оплаты',
            yookassa_payment_method_id=yookassa_payment_method_id,
            user_id=user_id,
            e=e,
        )
        return None
    await db.refresh(method)

    logger.info(
        'Создан сохранённый метод оплаты',
        saved_method_id=method.id,
        user_id=user_id,
        method_type=method_type,
        card_last4=card_last4,
    )
    return method


async def get_active_payment_methods_by_user(
    db: AsyncSession,
    user_id: int,
) -> list[SavedPaymentMethod]:
    """Получить все активные сохранённые методы оплаты пользователя."""
    result = await db.execute(
        select(SavedPaymentMethod)
        .where(
            SavedPaymentMethod.user_id == user_id,
            SavedPaymentMethod.is_active == True,
        )
        .order_by(SavedPaymentMethod.created_at.desc())
    )
    return list(result.scalars().all())


async def get_user_ids_with_active_payment_methods(
    db: AsyncSession,
    user_ids: list[int],
) -> set[int]:
    """Вернуть подмножество user_ids, у которых есть хотя бы один активный метод оплаты."""
    if not user_ids:
        return set()
    result = await db.execute(
        select(SavedPaymentMethod.user_id)
        .where(
            SavedPaymentMethod.user_id.in_(user_ids),
            SavedPaymentMethod.is_active == True,
        )
        .distinct()
    )
    return set(result.scalars().all())


async def get_payment_method_by_yookassa_id(
    db: AsyncSession,
    yookassa_payment_method_id: str,
    include_inactive: bool = False,
) -> SavedPaymentMethod | None:
    """Найти сохранённый метод по YooKassa payment_method.id."""
    query = select(SavedPaymentMethod).where(
        SavedPaymentMethod.yookassa_payment_method_id == yookassa_payment_method_id,
    )
    if not include_inactive:
        query = query.where(SavedPaymentMethod.is_active == True)
    result = await db.execute(query)
    return result.scalar_one_or_none()


async def get_saved_payment_method_by_id(
    db: AsyncSession,
    saved_method_id: int,
    *,
    active_only: bool = True,
) -> SavedPaymentMethod | None:
    """Найти сохранённый метод оплаты по локальному id."""
    query = select(SavedPaymentMethod).where(SavedPaymentMethod.id == saved_method_id)
    if active_only:
        query = query.where(SavedPaymentMethod.is_active == True)
    result = await db.execute(query)
    return result.scalar_one_or_none()


async def deactivate_payment_method(
    db: AsyncSession,
    saved_method_id: int,
    user_id: int,
) -> bool:
    """Деактивировать (soft-delete) сохранённый метод оплаты."""
    result = await db.execute(
        update(SavedPaymentMethod)
        .where(
            SavedPaymentMethod.id == saved_method_id,
            SavedPaymentMethod.user_id == user_id,
            SavedPaymentMethod.is_active == True,
        )
        .values(is_active=False, updated_at=datetime.now(UTC))
    )
    await db.commit()

    if result.rowcount > 0:
        logger.info(
            'Метод оплаты деактивирован',
            saved_method_id=saved_method_id,
            user_id=user_id,
        )
        return True
    return False


async def deactivate_all_user_payment_methods(
    db: AsyncSession,
    user_id: int,
) -> int:
    """Деактивировать все методы оплаты пользователя. Возвращает количество деактивированных."""
    result = await db.execute(
        update(SavedPaymentMethod)
        .where(
            SavedPaymentMethod.user_id == user_id,
            SavedPaymentMethod.is_active == True,
        )
        .values(is_active=False, updated_at=datetime.now(UTC))
    )
    await db.commit()

    if result.rowcount > 0:
        logger.info(
            'Все методы оплаты пользователя деактивированы',
            user_id=user_id,
            count=result.rowcount,
        )
    return result.rowcount


def _active_cards_base_filters(
    search: str | None = None,
    method_type: str | None = None,
    autopay_only: bool = False,
):
    filters = [SavedPaymentMethod.is_active == True]

    if method_type:
        filters.append(SavedPaymentMethod.method_type == method_type)

    if search:
        term = search.strip()
        if term:
            like = f'%{term}%'
            search_filters = [
                User.username.ilike(like),
                User.first_name.ilike(like),
                User.last_name.ilike(like),
                User.email.ilike(like),
            ]
            if term.isdigit():
                search_filters.append(User.telegram_id == int(term))
                search_filters.append(User.id == int(term))
            filters.append(or_(*search_filters))

    if autopay_only:
        filters.append(
            SavedPaymentMethod.user_id.in_(
                select(Subscription.user_id).where(Subscription.autopay_enabled == True).distinct()
            )
        )

    return filters


async def get_autopayments_stats(
    db: AsyncSession,
    search: str | None = None,
    method_type: str | None = None,
    autopay_only: bool = False,
) -> dict[str, int | dict[str, int]]:
    """Сводка по пользователям с привязанными способами оплаты."""
    filters = _active_cards_base_filters(search=search, method_type=method_type, autopay_only=autopay_only)

    users_q = (
        select(func.count(func.distinct(SavedPaymentMethod.user_id)))
        .select_from(SavedPaymentMethod)
        .join(User, User.id == SavedPaymentMethod.user_id)
        .where(and_(*filters))
    )
    cards_q = (
        select(func.count(SavedPaymentMethod.id))
        .select_from(SavedPaymentMethod)
        .join(User, User.id == SavedPaymentMethod.user_id)
        .where(and_(*filters))
    )
    by_type_q = (
        select(SavedPaymentMethod.method_type, func.count(SavedPaymentMethod.id))
        .select_from(SavedPaymentMethod)
        .join(User, User.id == SavedPaymentMethod.user_id)
        .where(and_(*filters))
        .group_by(SavedPaymentMethod.method_type)
    )

    autopay_filters = list(filters)
    if not autopay_only:
        autopay_filters.append(
            SavedPaymentMethod.user_id.in_(
                select(Subscription.user_id).where(Subscription.autopay_enabled == True).distinct()
            )
        )
    autopay_users_q = (
        select(func.count(func.distinct(SavedPaymentMethod.user_id)))
        .select_from(SavedPaymentMethod)
        .join(User, User.id == SavedPaymentMethod.user_id)
        .where(and_(*autopay_filters))
    )

    users_with_cards = int((await db.execute(users_q)).scalar() or 0)
    total_active_cards = int((await db.execute(cards_q)).scalar() or 0)
    users_with_autopay = int((await db.execute(autopay_users_q)).scalar() or 0)
    by_method_rows = (await db.execute(by_type_q)).all()
    by_method_type = {str(method or 'unknown'): int(count) for method, count in by_method_rows}

    return {
        'users_with_cards': users_with_cards,
        'total_active_cards': total_active_cards,
        'users_with_autopay': users_with_autopay,
        'by_method_type': by_method_type,
    }


async def list_users_with_saved_payment_methods(
    db: AsyncSession,
    *,
    offset: int = 0,
    limit: int = 20,
    search: str | None = None,
    method_type: str | None = None,
    autopay_only: bool = False,
) -> tuple[list[tuple[User, list[SavedPaymentMethod]]], int]:
    """Пагинированный список пользователей с активными сохранёнными методами оплаты."""
    filters = _active_cards_base_filters(search=search, method_type=method_type, autopay_only=autopay_only)

    count_q = (
        select(func.count(func.distinct(SavedPaymentMethod.user_id)))
        .select_from(SavedPaymentMethod)
        .join(User, User.id == SavedPaymentMethod.user_id)
        .where(and_(*filters))
    )
    total = int((await db.execute(count_q)).scalar() or 0)
    if total == 0:
        return [], 0

    last_linked = func.max(SavedPaymentMethod.created_at).label('last_linked')
    page_q = (
        select(SavedPaymentMethod.user_id, last_linked)
        .select_from(SavedPaymentMethod)
        .join(User, User.id == SavedPaymentMethod.user_id)
        .where(and_(*filters))
        .group_by(SavedPaymentMethod.user_id)
        .order_by(last_linked.desc())
        .offset(offset)
        .limit(limit)
    )
    page_rows = (await db.execute(page_q)).all()
    user_ids = [row[0] for row in page_rows]
    if not user_ids:
        return [], total

    users_result = await db.execute(
        select(User).where(User.id.in_(user_ids)).options(selectinload(User.subscriptions))
    )
    users_by_id = {user.id: user for user in users_result.scalars().unique().all()}

    cards_result = await db.execute(
        select(SavedPaymentMethod)
        .where(
            SavedPaymentMethod.user_id.in_(user_ids),
            SavedPaymentMethod.is_active == True,
        )
        .order_by(SavedPaymentMethod.created_at.desc())
    )
    cards_by_user: dict[int, list[SavedPaymentMethod]] = {}
    for card in cards_result.scalars().all():
        cards_by_user.setdefault(card.user_id, []).append(card)

    ordered: list[tuple[User, list[SavedPaymentMethod]]] = []
    for user_id in user_ids:
        user = users_by_id.get(user_id)
        if not user:
            continue
        ordered.append((user, cards_by_user.get(user_id, [])))
    return ordered, total
