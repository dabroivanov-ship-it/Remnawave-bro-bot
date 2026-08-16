"""Admin routes: users with positive balance."""

from __future__ import annotations

import math

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.crud.user import _build_user_search_conditions
from app.database.models import User, UserStatus

from ..dependencies import get_cabinet_db, require_permission


router = APIRouter(prefix='/admin/balances', tags=['Cabinet Admin Balances'])


class BalanceUserItem(BaseModel):
    user_id: int
    telegram_id: int | None = None
    username: str | None = None
    full_name: str
    email: str | None = None
    status: str
    balance_kopeks: int
    balance_rubles: float


class BalancesListResponse(BaseModel):
    items: list[BalanceUserItem]
    total: int
    page: int
    per_page: int
    pages: int


class BalancesStatsResponse(BaseModel):
    users_with_balance: int
    total_balance_kopeks: int
    total_balance_rubles: float
    avg_balance_kopeks: int
    avg_balance_rubles: float


def _base_filters(search: str | None):
    filters = [
        User.balance_kopeks > 0,
        User.status != UserStatus.DELETED.value,
    ]
    if search:
        conditions = _build_user_search_conditions(search)
        if conditions:
            filters.append(or_(*conditions))
    return filters


@router.get('/stats', response_model=BalancesStatsResponse)
async def balances_stats(
    search: str | None = Query(None, max_length=255),
    _: User = Depends(require_permission('users:read')),
    db: AsyncSession = Depends(get_cabinet_db),
) -> BalancesStatsResponse:
    filters = _base_filters(search)
    result = await db.execute(
        select(
            func.count(User.id),
            func.coalesce(func.sum(User.balance_kopeks), 0),
            func.coalesce(func.avg(User.balance_kopeks), 0),
        ).where(*filters)
    )
    count, total_sum, avg_balance = result.one()
    total_kopeks = int(total_sum or 0)
    avg_kopeks = int(round(float(avg_balance or 0)))
    return BalancesStatsResponse(
        users_with_balance=int(count or 0),
        total_balance_kopeks=total_kopeks,
        total_balance_rubles=total_kopeks / 100,
        avg_balance_kopeks=avg_kopeks,
        avg_balance_rubles=avg_kopeks / 100,
    )


@router.get('', response_model=BalancesListResponse)
async def list_users_with_balance(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    search: str | None = Query(None, max_length=255),
    _: User = Depends(require_permission('users:read')),
    db: AsyncSession = Depends(get_cabinet_db),
) -> BalancesListResponse:
    filters = _base_filters(search)

    total = int(
        (
            await db.execute(select(func.count(User.id)).where(*filters))
        ).scalar_one()
        or 0
    )
    pages = max(1, math.ceil(total / per_page)) if total else 1
    offset = (page - 1) * per_page

    result = await db.execute(
        select(User)
        .where(*filters)
        .order_by(User.balance_kopeks.desc(), User.id.desc())
        .offset(offset)
        .limit(per_page)
    )
    users = list(result.scalars().all())

    items = [
        BalanceUserItem(
            user_id=user.id,
            telegram_id=user.telegram_id,
            username=user.username,
            full_name=user.full_name or '',
            email=user.email,
            status=user.status if isinstance(user.status, str) else getattr(user.status, 'value', str(user.status)),
            balance_kopeks=user.balance_kopeks or 0,
            balance_rubles=(user.balance_kopeks or 0) / 100,
        )
        for user in users
    ]

    return BalancesListResponse(
        items=items,
        total=total,
        page=page,
        per_page=per_page,
        pages=pages,
    )
