from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database.models import (
    InfraPayment,
    InfraPaymentSource,
    InfraPaymentStatus,
    InfraProvider,
    InfraServer,
)


DUE_SOON_DAYS = 14


def compute_payment_status(next_due_date: date | None) -> str:
    if next_due_date is None:
        return InfraPaymentStatus.UNKNOWN.value
    today = date.today()
    if next_due_date < today:
        return InfraPaymentStatus.OVERDUE.value
    if next_due_date == today:
        return InfraPaymentStatus.DUE_TODAY.value
    if next_due_date <= today + timedelta(days=DUE_SOON_DAYS):
        return InfraPaymentStatus.DUE_SOON.value
    return InfraPaymentStatus.PAID.value


def _estimate_monthly_from_payments(
    payments: list[InfraPayment],
    *,
    active_servers_count: int = 0,
) -> int:
    """Estimate typical monthly infra spend from Remnawave CRM history.

    History records are usually one bill per node. Prefer:
    median(bill) × active billing nodes.
    Fallback: average of calendar-month totals (skip incomplete current month
    when older months exist).
    """
    amounts = [p.amount_kopeks for p in payments if p.amount_kopeks and p.amount_kopeks > 0]
    if not amounts:
        return 0

    amounts_sorted = sorted(amounts)
    median = amounts_sorted[len(amounts_sorted) // 2]
    if active_servers_count > 0:
        return int(median * active_servers_count)

    by_month: dict[tuple[int, int], int] = {}
    for payment in payments:
        if not payment.amount_kopeks or payment.paid_at is None:
            continue
        paid_at = payment.paid_at if payment.paid_at.tzinfo else payment.paid_at.replace(tzinfo=UTC)
        key = (paid_at.year, paid_at.month)
        by_month[key] = by_month.get(key, 0) + payment.amount_kopeks
    if not by_month:
        return int(sum(amounts))

    now = datetime.now(UTC)
    current_month = (now.year, now.month)
    complete = [total for month, total in by_month.items() if month != current_month]
    totals = complete if complete else list(by_month.values())
    return int(sum(totals) / len(totals))


def parse_amount_kopeks(value: object) -> int | None:
    if value is None or value == '':
        return None
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if amount <= 0:
        return None
    return int(amount * 100)


def parse_date_value(value: object) -> date | None:
    if value is None or value == '':
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    text = str(value).strip()
    if not text:
        return None
    if text.endswith('Z'):
        text = text[:-1] + '+00:00'
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        pass
    for fmt in ('%Y-%m-%d', '%d.%m.%Y'):
        try:
            return datetime.strptime(text[:10], fmt).date()
        except ValueError:
            continue
    return None


def parse_datetime_value(value: object) -> datetime | None:
    if value is None or value == '':
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time(), tzinfo=UTC)
    text = str(value).strip()
    if not text:
        return None
    if text.endswith('Z'):
        text = text[:-1] + '+00:00'
    try:
        parsed = datetime.fromisoformat(text)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except ValueError:
        return None


def status_from_event(event_name: str) -> str | None:
    if 'overdue' in event_name:
        return InfraPaymentStatus.OVERDUE.value
    if 'due_today' in event_name:
        return InfraPaymentStatus.DUE_TODAY.value
    if 'in_7_days' in event_name or 'in_48hrs' in event_name or 'in_24hrs' in event_name:
        return InfraPaymentStatus.DUE_SOON.value
    if 'paid' in event_name:
        return InfraPaymentStatus.PAID.value
    return None


# --- Providers ---


async def get_all_providers(db: AsyncSession) -> list[InfraProvider]:
    result = await db.execute(
        select(InfraProvider)
        .options(selectinload(InfraProvider.servers))
        .order_by(InfraProvider.name, InfraProvider.id)
    )
    return list(result.scalars().unique().all())


async def get_provider_by_id(db: AsyncSession, provider_id: int) -> InfraProvider | None:
    result = await db.execute(
        select(InfraProvider)
        .where(InfraProvider.id == provider_id)
        .options(selectinload(InfraProvider.servers))
    )
    return result.scalars().first()


async def get_or_create_provider_by_name(
    db: AsyncSession,
    name: str,
    *,
    login_url: str | None = None,
    website: str | None = None,
) -> InfraProvider:
    normalized = name.strip()
    result = await db.execute(select(InfraProvider).where(InfraProvider.name == normalized))
    provider = result.scalars().first()
    if provider:
        changed = False
        if login_url and provider.login_url != login_url:
            provider.login_url = login_url
            changed = True
        if website and provider.website != website:
            provider.website = website
            changed = True
        if changed:
            await db.flush()
        return provider

    provider = InfraProvider(name=normalized, login_url=login_url, website=website)
    db.add(provider)
    await db.flush()
    return provider


async def create_provider(
    db: AsyncSession,
    *,
    name: str,
    website: str | None = None,
    login_url: str | None = None,
    notes: str | None = None,
    currency: str = 'RUB',
) -> InfraProvider:
    provider = InfraProvider(
        name=name.strip(),
        website=website,
        login_url=login_url,
        notes=notes,
        currency=currency or 'RUB',
    )
    db.add(provider)
    await db.flush()
    return provider


async def update_provider(
    db: AsyncSession,
    provider: InfraProvider,
    *,
    name: str | None = None,
    website: str | None = None,
    login_url: str | None = None,
    notes: str | None = None,
    currency: str | None = None,
) -> InfraProvider:
    if name is not None:
        provider.name = name.strip()
    if website is not None:
        provider.website = website
    if login_url is not None:
        provider.login_url = login_url
    if notes is not None:
        provider.notes = notes
    if currency is not None:
        provider.currency = currency
    await db.flush()
    return provider


async def delete_provider(db: AsyncSession, provider: InfraProvider) -> None:
    await db.delete(provider)
    await db.flush()


# --- Servers ---


async def get_all_servers(
    db: AsyncSession,
    *,
    include_inactive: bool = True,
    provider_id: int | None = None,
    payment_status: str | None = None,
) -> list[InfraServer]:
    query = select(InfraServer).options(selectinload(InfraServer.provider))

    if not include_inactive:
        query = query.where(InfraServer.is_active.is_(True))
    if provider_id is not None:
        query = query.where(InfraServer.provider_id == provider_id)
    if payment_status:
        query = query.where(InfraServer.payment_status == payment_status)

    query = query.order_by(InfraServer.next_due_date.nulls_last(), InfraServer.name, InfraServer.id)
    result = await db.execute(query)
    return list(result.scalars().unique().all())


async def get_server_by_id(db: AsyncSession, server_id: int) -> InfraServer | None:
    result = await db.execute(
        select(InfraServer)
        .where(InfraServer.id == server_id)
        .options(selectinload(InfraServer.provider), selectinload(InfraServer.payments))
    )
    return result.scalars().unique().first()


async def get_server_by_node_uuid(db: AsyncSession, node_uuid: str) -> InfraServer | None:
    if not node_uuid:
        return None
    result = await db.execute(
        select(InfraServer)
        .where(InfraServer.remnawave_node_uuid == node_uuid)
        .options(selectinload(InfraServer.provider))
    )
    return result.scalars().first()


async def create_server(
    db: AsyncSession,
    *,
    name: str,
    provider_id: int | None = None,
    remnawave_node_uuid: str | None = None,
    address: str | None = None,
    country_code: str | None = None,
    billing_amount_kopeks: int | None = None,
    billing_cycle_days: int = 30,
    next_due_date: date | None = None,
    notes: str | None = None,
    is_active: bool = True,
) -> InfraServer:
    server = InfraServer(
        name=name.strip(),
        provider_id=provider_id,
        remnawave_node_uuid=remnawave_node_uuid,
        address=address,
        country_code=country_code,
        billing_amount_kopeks=billing_amount_kopeks,
        billing_cycle_days=billing_cycle_days,
        next_due_date=next_due_date,
        payment_status=compute_payment_status(next_due_date),
        notes=notes,
        is_active=is_active,
    )
    db.add(server)
    await db.flush()
    return server


async def update_server(
    db: AsyncSession,
    server: InfraServer,
    *,
    name: str | None = None,
    provider_id: int | None = None,
    remnawave_node_uuid: str | None = None,
    address: str | None = None,
    country_code: str | None = None,
    billing_amount_kopeks: int | None = None,
    billing_cycle_days: int | None = None,
    next_due_date: date | None = None,
    notes: str | None = None,
    is_active: bool | None = None,
    payment_status: str | None = None,
) -> InfraServer:
    if name is not None:
        server.name = name.strip()
    if provider_id is not None:
        server.provider_id = provider_id
    if remnawave_node_uuid is not None:
        server.remnawave_node_uuid = remnawave_node_uuid or None
    if address is not None:
        server.address = address
    if country_code is not None:
        server.country_code = country_code
    if billing_amount_kopeks is not None:
        server.billing_amount_kopeks = billing_amount_kopeks
    if billing_cycle_days is not None:
        server.billing_cycle_days = billing_cycle_days
    if next_due_date is not None:
        server.next_due_date = next_due_date
        server.payment_status = compute_payment_status(next_due_date)
    if notes is not None:
        server.notes = notes
    if is_active is not None:
        server.is_active = is_active
    if payment_status is not None:
        server.payment_status = payment_status
    await db.flush()
    return server


async def delete_server(db: AsyncSession, server: InfraServer) -> None:
    await db.delete(server)
    await db.flush()


async def upsert_server_from_node(
    db: AsyncSession,
    *,
    node_uuid: str,
    name: str,
    address: str | None = None,
    country_code: str | None = None,
    provider_name: str | None = None,
    provider_login_url: str | None = None,
    provider_website: str | None = None,
    next_due_date: date | None = None,
    is_active: bool = True,
) -> InfraServer:
    provider_id = None
    if provider_name and provider_name.strip():
        provider = await get_or_create_provider_by_name(
            db,
            provider_name.strip(),
            login_url=provider_login_url,
            website=provider_website,
        )
        provider_id = provider.id

    server = await get_server_by_node_uuid(db, node_uuid)
    if server:
        server.name = name or server.name
        if address:
            server.address = address
        if country_code:
            server.country_code = country_code
        if provider_id is not None:
            server.provider_id = provider_id
        if next_due_date is not None:
            server.next_due_date = next_due_date
            server.payment_status = compute_payment_status(next_due_date)
        server.is_active = is_active
        await db.flush()
        return server

    server = await create_server(
        db,
        name=name,
        provider_id=provider_id,
        remnawave_node_uuid=node_uuid,
        address=address,
        country_code=country_code,
        is_active=is_active,
        next_due_date=next_due_date,
    )
    return server


# --- Payments ---


async def get_payments(
    db: AsyncSession,
    *,
    server_id: int | None = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[InfraPayment], int]:
    query = select(InfraPayment).options(
        selectinload(InfraPayment.server),
        selectinload(InfraPayment.provider),
    )
    count_query = select(func.count(InfraPayment.id))

    if server_id is not None:
        query = query.where(InfraPayment.server_id == server_id)
        count_query = count_query.where(InfraPayment.server_id == server_id)

    query = query.order_by(InfraPayment.paid_at.desc(), InfraPayment.id.desc()).limit(limit).offset(offset)

    result = await db.execute(query)
    payments = list(result.scalars().unique().all())
    total = int((await db.execute(count_query)).scalar_one())
    return payments, total


async def create_payment(
    db: AsyncSession,
    *,
    server: InfraServer,
    amount_kopeks: int,
    paid_at: datetime | None = None,
    due_date: date | None = None,
    source: str = InfraPaymentSource.MANUAL.value,
    source_event: str | None = None,
    notes: str | None = None,
    created_by_user_id: int | None = None,
    extend_due_date: date | None = None,
    touch_server: bool = True,
) -> InfraPayment:
    paid_at = paid_at or datetime.now(UTC)
    payment = InfraPayment(
        server_id=server.id,
        provider_id=server.provider_id,
        amount_kopeks=amount_kopeks,
        paid_at=paid_at,
        due_date=due_date or server.next_due_date,
        source=source,
        source_event=source_event,
        notes=notes,
        created_by_user_id=created_by_user_id,
    )
    db.add(payment)

    if touch_server:
        server.last_paid_at = paid_at
        if extend_due_date is not None:
            server.next_due_date = extend_due_date
            server.payment_status = compute_payment_status(extend_due_date)
        elif server.billing_cycle_days and server.next_due_date:
            server.next_due_date = server.next_due_date + timedelta(days=server.billing_cycle_days)
            server.payment_status = compute_payment_status(server.next_due_date)
        else:
            server.payment_status = InfraPaymentStatus.PAID.value

    await db.flush()
    return payment


async def payment_exists_by_source_event(db: AsyncSession, source_event: str) -> bool:
    result = await db.execute(
        select(InfraPayment.id).where(InfraPayment.source_event == source_event).limit(1)
    )
    return result.scalar_one_or_none() is not None


async def get_first_server_for_provider(db: AsyncSession, provider_id: int) -> InfraServer | None:
    result = await db.execute(
        select(InfraServer)
        .where(InfraServer.provider_id == provider_id)
        .order_by(InfraServer.id.asc())
        .limit(1)
    )
    return result.scalars().first()

async def get_dashboard_stats(db: AsyncSession) -> dict:
    servers_result = await db.execute(
        select(InfraServer).where(InfraServer.is_active.is_(True)).options(selectinload(InfraServer.provider))
    )
    servers = list(servers_result.scalars().all())

    # Keep statuses aligned with next_due_date (do not trust stale DB values)
    status_changed = False
    for server in servers:
        live = compute_payment_status(server.next_due_date)
        if server.payment_status != live:
            server.payment_status = live
            status_changed = True
    if status_changed:
        await db.commit()
        for server in servers:
            await db.refresh(server)

    configured_monthly = sum(s.billing_amount_kopeks or 0 for s in servers)
    payments_result = await db.execute(select(InfraPayment).order_by(InfraPayment.paid_at.desc()))
    payments = list(payments_result.scalars().all())

    monthly_is_estimated = False
    if configured_monthly > 0:
        total_monthly_kopeks = configured_monthly
    else:
        total_monthly_kopeks = _estimate_monthly_from_payments(
            payments,
            active_servers_count=len(servers),
        )
        monthly_is_estimated = total_monthly_kopeks > 0

    now = datetime.now(UTC)
    month_start = datetime(now.year, now.month, 1, tzinfo=UTC)
    paid_this_month_kopeks = sum(
        p.amount_kopeks
        for p in payments
        if p.paid_at is not None
        and (p.paid_at if p.paid_at.tzinfo else p.paid_at.replace(tzinfo=UTC)) >= month_start
    )

    by_status: dict[str, int] = {status.value: 0 for status in InfraPaymentStatus}
    for server in servers:
        by_status[server.payment_status] = by_status.get(server.payment_status, 0) + 1

    today = date.today()
    overdue = [
        s
        for s in servers
        if s.next_due_date is not None and s.next_due_date < today
    ]
    overdue.sort(key=lambda s: (s.next_due_date or today, s.name))

    upcoming = [
        s
        for s in servers
        if s.next_due_date is not None
        and s.next_due_date >= today
        and s.next_due_date <= today + timedelta(days=DUE_SOON_DAYS)
    ]
    upcoming.sort(key=lambda s: (s.next_due_date or today, s.name))

    overdue_amount_kopeks = sum(s.billing_amount_kopeks or 0 for s in overdue)
    upcoming_amount_kopeks = sum(s.billing_amount_kopeks or 0 for s in upcoming)

    return {
        'total_monthly_kopeks': total_monthly_kopeks,
        'monthly_is_estimated': monthly_is_estimated,
        'paid_this_month_kopeks': paid_this_month_kopeks,
        'active_servers_count': len(servers),
        'providers_count': int((await db.execute(select(func.count(InfraProvider.id)))).scalar_one()),
        'by_status': by_status,
        'overdue_count': len(overdue),
        'overdue_amount_kopeks': overdue_amount_kopeks,
        'overdue': overdue[:20],
        'upcoming_count': len(upcoming),
        'upcoming_amount_kopeks': upcoming_amount_kopeks,
        'upcoming': upcoming[:20],
    }
