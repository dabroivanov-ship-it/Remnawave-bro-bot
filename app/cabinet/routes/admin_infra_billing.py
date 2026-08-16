"""Admin routes for infrastructure billing (Remnawave nodes / hosters)."""

import html

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.crud.infra_billing import (
    compute_payment_status,
    create_payment,
    create_provider,
    create_server,
    delete_provider,
    delete_server,
    get_all_providers,
    get_all_servers,
    get_dashboard_stats,
    get_first_server_for_provider,
    get_or_create_provider_by_name,
    get_payments,
    get_provider_by_id,
    get_server_by_id,
    get_server_by_node_uuid,
    parse_amount_kopeks,
    parse_date_value,
    parse_datetime_value,
    payment_exists_by_source_event,
    update_provider,
    update_server,
    upsert_server_from_node,
)
from app.config import settings
from app.database.models import InfraPayment, InfraPaymentSource, InfraProvider, InfraServer, User
from app.services.remnawave_service import RemnaWaveService

from ..dependencies import get_cabinet_db, require_permission
from ..schemas.infra_billing import (
    InfraDashboardResponse,
    InfraMarkPaidRequest,
    InfraPaymentListResponse,
    InfraPaymentResponse,
    InfraProviderCreateRequest,
    InfraProviderListResponse,
    InfraProviderResponse,
    InfraProviderUpdateRequest,
    InfraServerCreateRequest,
    InfraServerListResponse,
    InfraServerResponse,
    InfraServerUpdateRequest,
    InfraSyncResponse,
    InfraUpcomingServer,
)


router = APIRouter(prefix='/admin/infra-billing', tags=['Cabinet Admin Infra Billing'])


async def _notify_infra_billing(text: str) -> None:
    if not settings.ADMIN_NOTIFICATIONS_ENABLED or not settings.WEBHOOK_NOTIFY_INFRA_BILLING:
        return
    try:
        from app.services.infra_billing_notify_service import infra_billing_notify_service

        if infra_billing_notify_service.bot:
            from app.services.admin_notification_service import AdminNotificationService

            await AdminNotificationService(infra_billing_notify_service.bot).send_infra_billing_notification(text)
            return
    except Exception:
        pass

    if not settings.BOT_TOKEN:
        return
    try:
        from aiogram import Bot

        from app.services.admin_notification_service import AdminNotificationService

        bot = Bot(token=settings.BOT_TOKEN)
        try:
            service = AdminNotificationService(bot)
            await service.send_infra_billing_notification(text)
        finally:
            await bot.session.close()
    except Exception:
        pass


async def _notify_payment_event(
    *,
    server_name: str,
    provider_name: str | None,
    amount_kopeks: int,
    source: str,
    paid_at=None,
    actor: str | None = None,
) -> None:
    try:
        from app.services.infra_billing_notify_service import infra_billing_notify_service

        if infra_billing_notify_service.bot:
            await infra_billing_notify_service.notify_payment(
                server_name=server_name,
                provider_name=provider_name,
                amount_kopeks=amount_kopeks,
                source=source,
                paid_at=paid_at,
                actor=actor,
            )
            return
    except Exception:
        pass

    amount_rub = (amount_kopeks or 0) / 100
    lines = [
        '<b>✅ Оплата сервера Remnawave</b>',
        f'Сервер: <code>{html.escape(server_name)}</code>',
        f'Провайдер: <code>{html.escape(provider_name or "—")}</code>',
        f'Сумма: <code>{amount_rub:.2f}</code>',
        f'Источник: <code>{html.escape(source)}</code>',
    ]
    if actor:
        lines.append(f'Кто: <code>{html.escape(actor)}</code>')
    await _notify_infra_billing('\n'.join(lines))


def _serialize_provider(provider: InfraProvider) -> InfraProviderResponse:
    return InfraProviderResponse(
        id=provider.id,
        name=provider.name,
        website=provider.website,
        login_url=provider.login_url,
        notes=provider.notes,
        currency=provider.currency,
        servers_count=len(provider.servers) if provider.servers else 0,
        created_at=provider.created_at,
        updated_at=provider.updated_at,
    )


def _serialize_server(server: InfraServer) -> InfraServerResponse:
    return InfraServerResponse(
        id=server.id,
        provider_id=server.provider_id,
        provider_name=server.provider.name if server.provider else None,
        remnawave_node_uuid=server.remnawave_node_uuid,
        name=server.name,
        address=server.address,
        country_code=server.country_code,
        billing_amount_kopeks=server.billing_amount_kopeks,
        billing_cycle_days=server.billing_cycle_days,
        next_due_date=server.next_due_date,
        last_paid_at=server.last_paid_at,
        payment_status=compute_payment_status(server.next_due_date),
        is_active=server.is_active,
        notes=server.notes,
        created_at=server.created_at,
        updated_at=server.updated_at,
    )


def _serialize_payment(payment: InfraPayment) -> InfraPaymentResponse:
    return InfraPaymentResponse(
        id=payment.id,
        server_id=payment.server_id,
        server_name=payment.server.name if payment.server else None,
        provider_id=payment.provider_id,
        provider_name=payment.provider.name if payment.provider else None,
        amount_kopeks=payment.amount_kopeks,
        paid_at=payment.paid_at,
        due_date=payment.due_date,
        source=payment.source,
        source_event=payment.source_event,
        notes=payment.notes,
        created_by_user_id=payment.created_by_user_id,
        created_at=payment.created_at,
    )


@router.get('/dashboard', response_model=InfraDashboardResponse)
async def get_infra_dashboard(
    admin: User = Depends(require_permission('infra_billing:read')),
    db: AsyncSession = Depends(get_cabinet_db),
) -> InfraDashboardResponse:
    stats = await get_dashboard_stats(db)

    def _map_due_servers(items: list) -> list[InfraUpcomingServer]:
        return [
            InfraUpcomingServer(
                id=s.id,
                name=s.name,
                provider_name=s.provider.name if s.provider else None,
                next_due_date=s.next_due_date,
                payment_status=s.payment_status,
                billing_amount_kopeks=s.billing_amount_kopeks,
            )
            for s in items
        ]

    return InfraDashboardResponse(
        total_monthly_kopeks=stats['total_monthly_kopeks'],
        monthly_is_estimated=stats['monthly_is_estimated'],
        paid_this_month_kopeks=stats['paid_this_month_kopeks'],
        active_servers_count=stats['active_servers_count'],
        providers_count=stats['providers_count'],
        by_status=stats['by_status'],
        overdue_count=stats['overdue_count'],
        overdue_amount_kopeks=stats['overdue_amount_kopeks'],
        overdue=_map_due_servers(stats['overdue']),
        upcoming_count=stats['upcoming_count'],
        upcoming_amount_kopeks=stats['upcoming_amount_kopeks'],
        upcoming=_map_due_servers(stats['upcoming']),
    )


# --- Providers ---


@router.get('/providers', response_model=InfraProviderListResponse)
async def list_providers(
    admin: User = Depends(require_permission('infra_billing:read')),
    db: AsyncSession = Depends(get_cabinet_db),
) -> InfraProviderListResponse:
    providers = await get_all_providers(db)
    items = [_serialize_provider(p) for p in providers]
    return InfraProviderListResponse(items=items, total=len(items))


@router.post('/providers', response_model=InfraProviderResponse, status_code=status.HTTP_201_CREATED)
async def create_provider_endpoint(
    payload: InfraProviderCreateRequest,
    admin: User = Depends(require_permission('infra_billing:create')),
    db: AsyncSession = Depends(get_cabinet_db),
) -> InfraProviderResponse:
    provider = await create_provider(
        db,
        name=payload.name,
        website=payload.website,
        login_url=payload.login_url,
        notes=payload.notes,
        currency=payload.currency,
    )
    await db.commit()
    provider = await get_provider_by_id(db, provider.id)
    assert provider is not None
    return _serialize_provider(provider)


@router.get('/providers/{provider_id}', response_model=InfraProviderResponse)
async def get_provider(
    provider_id: int,
    admin: User = Depends(require_permission('infra_billing:read')),
    db: AsyncSession = Depends(get_cabinet_db),
) -> InfraProviderResponse:
    provider = await get_provider_by_id(db, provider_id)
    if not provider:
        raise HTTPException(status.HTTP_404_NOT_FOUND, 'Provider not found')
    return _serialize_provider(provider)


@router.patch('/providers/{provider_id}', response_model=InfraProviderResponse)
async def update_provider_endpoint(
    provider_id: int,
    payload: InfraProviderUpdateRequest,
    admin: User = Depends(require_permission('infra_billing:edit')),
    db: AsyncSession = Depends(get_cabinet_db),
) -> InfraProviderResponse:
    provider = await get_provider_by_id(db, provider_id)
    if not provider:
        raise HTTPException(status.HTTP_404_NOT_FOUND, 'Provider not found')
    provider = await update_provider(
        db,
        provider,
        name=payload.name,
        website=payload.website,
        login_url=payload.login_url,
        notes=payload.notes,
        currency=payload.currency,
    )
    await db.commit()
    provider = await get_provider_by_id(db, provider.id)
    assert provider is not None
    return _serialize_provider(provider)


@router.delete('/providers/{provider_id}', status_code=status.HTTP_204_NO_CONTENT)
async def delete_provider_endpoint(
    provider_id: int,
    admin: User = Depends(require_permission('infra_billing:delete')),
    db: AsyncSession = Depends(get_cabinet_db),
) -> None:
    provider = await get_provider_by_id(db, provider_id)
    if not provider:
        raise HTTPException(status.HTTP_404_NOT_FOUND, 'Provider not found')
    await delete_provider(db, provider)
    await db.commit()


# --- Servers ---


@router.get('/servers', response_model=InfraServerListResponse)
async def list_servers(
    include_inactive: bool = True,
    provider_id: int | None = None,
    payment_status: str | None = None,
    admin: User = Depends(require_permission('infra_billing:read')),
    db: AsyncSession = Depends(get_cabinet_db),
) -> InfraServerListResponse:
    servers = await get_all_servers(
        db,
        include_inactive=include_inactive,
        provider_id=provider_id,
        payment_status=payment_status,
    )
    items = [_serialize_server(s) for s in servers]
    return InfraServerListResponse(items=items, total=len(items))


@router.post('/servers', response_model=InfraServerResponse, status_code=status.HTTP_201_CREATED)
async def create_server_endpoint(
    payload: InfraServerCreateRequest,
    admin: User = Depends(require_permission('infra_billing:create')),
    db: AsyncSession = Depends(get_cabinet_db),
) -> InfraServerResponse:
    if payload.provider_id:
        provider = await get_provider_by_id(db, payload.provider_id)
        if not provider:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, 'Provider not found')
    server = await create_server(
        db,
        name=payload.name,
        provider_id=payload.provider_id,
        remnawave_node_uuid=payload.remnawave_node_uuid,
        address=payload.address,
        country_code=payload.country_code,
        billing_amount_kopeks=payload.billing_amount_kopeks,
        billing_cycle_days=payload.billing_cycle_days,
        next_due_date=payload.next_due_date,
        notes=payload.notes,
        is_active=payload.is_active,
    )
    await db.commit()
    server = await get_server_by_id(db, server.id)
    return _serialize_server(server)


@router.get('/servers/{server_id}', response_model=InfraServerResponse)
async def get_server(
    server_id: int,
    admin: User = Depends(require_permission('infra_billing:read')),
    db: AsyncSession = Depends(get_cabinet_db),
) -> InfraServerResponse:
    server = await get_server_by_id(db, server_id)
    if not server:
        raise HTTPException(status.HTTP_404_NOT_FOUND, 'Server not found')
    return _serialize_server(server)


@router.patch('/servers/{server_id}', response_model=InfraServerResponse)
async def update_server_endpoint(
    server_id: int,
    payload: InfraServerUpdateRequest,
    admin: User = Depends(require_permission('infra_billing:edit')),
    db: AsyncSession = Depends(get_cabinet_db),
) -> InfraServerResponse:
    server = await get_server_by_id(db, server_id)
    if not server:
        raise HTTPException(status.HTTP_404_NOT_FOUND, 'Server not found')
    if payload.provider_id is not None:
        provider = await get_provider_by_id(db, payload.provider_id)
        if not provider:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, 'Provider not found')
    server = await update_server(
        db,
        server,
        name=payload.name,
        provider_id=payload.provider_id,
        remnawave_node_uuid=payload.remnawave_node_uuid,
        address=payload.address,
        country_code=payload.country_code,
        billing_amount_kopeks=payload.billing_amount_kopeks,
        billing_cycle_days=payload.billing_cycle_days,
        next_due_date=payload.next_due_date,
        notes=payload.notes,
        is_active=payload.is_active,
    )
    await db.commit()
    server = await get_server_by_id(db, server.id)
    return _serialize_server(server)


@router.delete('/servers/{server_id}', status_code=status.HTTP_204_NO_CONTENT)
async def delete_server_endpoint(
    server_id: int,
    admin: User = Depends(require_permission('infra_billing:delete')),
    db: AsyncSession = Depends(get_cabinet_db),
) -> None:
    server = await get_server_by_id(db, server_id)
    if not server:
        raise HTTPException(status.HTTP_404_NOT_FOUND, 'Server not found')
    await delete_server(db, server)
    await db.commit()


@router.post('/servers/{server_id}/mark-paid', response_model=InfraPaymentResponse)
async def mark_server_paid(
    server_id: int,
    payload: InfraMarkPaidRequest,
    admin: User = Depends(require_permission('infra_billing:edit')),
    db: AsyncSession = Depends(get_cabinet_db),
) -> InfraPaymentResponse:
    server = await get_server_by_id(db, server_id)
    if not server:
        raise HTTPException(status.HTTP_404_NOT_FOUND, 'Server not found')
    payment = await create_payment(
        db,
        server=server,
        amount_kopeks=payload.amount_kopeks,
        notes=payload.notes,
        created_by_user_id=admin.id,
        extend_due_date=payload.extend_due_date,
    )
    await db.commit()
    server = await get_server_by_id(db, server.id)
    await db.refresh(payment, attribute_names=['server', 'provider'])

    provider_label = server.provider.name if server and server.provider else '—'
    server_name = server.name if server else '—'
    await _notify_payment_event(
        server_name=server_name,
        provider_name=provider_label,
        amount_kopeks=payload.amount_kopeks,
        source='Вручную в кабинете',
        paid_at=payment.paid_at,
        actor=admin.email or str(admin.telegram_id or admin.id),
    )

    return _serialize_payment(payment)


# --- Payments ---


@router.get('/payments', response_model=InfraPaymentListResponse)
async def list_payments(
    server_id: int | None = None,
    limit: int = 100,
    offset: int = 0,
    admin: User = Depends(require_permission('infra_billing:read')),
    db: AsyncSession = Depends(get_cabinet_db),
) -> InfraPaymentListResponse:
    payments, total = await get_payments(db, server_id=server_id, limit=limit, offset=offset)
    return InfraPaymentListResponse(
        items=[_serialize_payment(p) for p in payments],
        total=total,
    )


# --- Sync ---


@router.post('/sync/nodes', response_model=InfraSyncResponse)
async def sync_nodes_from_remnawave(
    admin: User = Depends(require_permission('infra_billing:sync')),
    db: AsyncSession = Depends(get_cabinet_db),
) -> InfraSyncResponse:
    """Sync providers and billing nodes from RemnaWave /api/infra-billing/*."""
    service = RemnaWaveService()

    providers = await service.get_infra_providers()
    billing_nodes = await service.get_infra_billing_nodes()

    if not providers and not billing_nodes:
        # Fallback: plain nodes list (legacy panels without infra-billing module)
        nodes = await service.get_all_nodes()
        if not nodes:
            return InfraSyncResponse(
                synced=0,
                created=0,
                updated=0,
                message='No infra-billing data from RemnaWave (check API / panel CRM)',
            )
        created = 0
        updated = 0
        for node in nodes:
            node_uuid = node.get('uuid', '')
            if not node_uuid:
                continue
            existing = await get_server_by_node_uuid(db, node_uuid)
            await upsert_server_from_node(
                db,
                node_uuid=node_uuid,
                name=node.get('name') or node_uuid[:8],
                address=node.get('address'),
                country_code=node.get('country_code'),
                is_active=not node.get('is_disabled', False),
            )
            if existing:
                updated += 1
            else:
                created += 1
        await db.commit()
        synced = created + updated
        return InfraSyncResponse(
            synced=synced,
            created=created,
            updated=updated,
            payments_imported=0,
            message=f'Synced {synced} nodes (legacy /api/nodes)',
        )

    for item in providers:
        name = str(item.get('name') or '').strip()
        if not name:
            continue
        await get_or_create_provider_by_name(
            db,
            name,
            login_url=str(item.get('loginUrl') or '').strip() or None,
            website=str(item.get('faviconLink') or '').strip() or None,
        )

    # Map panel nodes for address enrichment
    panel_nodes = await service.get_all_nodes()
    address_by_uuid = {
        n.get('uuid', ''): n.get('address') for n in panel_nodes if n.get('uuid')
    }

    created = 0
    updated = 0

    for item in billing_nodes:
        node_uuid = str(item.get('nodeUuid') or '').strip()
        if not node_uuid:
            continue

        node_info = item.get('node') if isinstance(item.get('node'), dict) else {}
        provider_info = item.get('provider') if isinstance(item.get('provider'), dict) else {}

        name = (
            str(item.get('name') or '').strip()
            or str(node_info.get('name') or '').strip()
            or node_uuid[:8]
        )
        country_code = node_info.get('countryCode') or node_info.get('country_code')
        provider_name = str(provider_info.get('name') or '').strip() or None
        provider_login = str(provider_info.get('loginUrl') or '').strip() or None
        provider_website = str(provider_info.get('faviconLink') or '').strip() or None
        next_due = parse_date_value(item.get('nextBillingAt'))

        existing = await get_server_by_node_uuid(db, node_uuid)
        await upsert_server_from_node(
            db,
            node_uuid=node_uuid,
            name=name,
            address=address_by_uuid.get(node_uuid),
            country_code=str(country_code).strip() if country_code else None,
            provider_name=provider_name,
            provider_login_url=provider_login,
            provider_website=provider_website,
            next_due_date=next_due,
            is_active=True,
        )
        if existing:
            updated += 1
        else:
            created += 1

    payments_imported = 0
    imported_items: list[dict] = []
    history = await service.get_infra_billing_history()
    for record in history:
        history_uuid = str(record.get('uuid') or '').strip()
        if not history_uuid:
            continue
        source_event = f'rw_history:{history_uuid}'
        if await payment_exists_by_source_event(db, source_event):
            continue

        provider_info = record.get('provider') if isinstance(record.get('provider'), dict) else {}
        provider_name = str(provider_info.get('name') or '').strip()
        if not provider_name:
            continue

        provider = await get_or_create_provider_by_name(
            db,
            provider_name,
            login_url=str(provider_info.get('loginUrl') or '').strip() or None,
            website=str(provider_info.get('faviconLink') or '').strip() or None,
        )
        server = await get_first_server_for_provider(db, provider.id)
        if not server:
            # Provider-level CRM payment without linked local servers yet
            server = await create_server(
                db,
                name=f'{provider_name} (CRM)',
                provider_id=provider.id,
                is_active=True,
            )

        amount_kopeks = parse_amount_kopeks(record.get('amount'))
        if not amount_kopeks:
            continue
        paid_at = parse_datetime_value(record.get('billedAt'))
        await create_payment(
            db,
            server=server,
            amount_kopeks=amount_kopeks,
            paid_at=paid_at,
            source=InfraPaymentSource.REMNAWAVE.value,
            source_event=source_event,
            notes=f'Remnawave history {history_uuid}',
            touch_server=False,
        )
        payments_imported += 1
        imported_items.append(
            {
                'server_name': server.name,
                'provider_name': provider_name,
                'amount_kopeks': amount_kopeks,
            }
        )

    await db.commit()
    synced = created + updated

    if imported_items:
        try:
            from app.services.infra_billing_notify_service import infra_billing_notify_service

            await infra_billing_notify_service.notify_payments_imported(imported_items)
        except Exception:
            pass

    return InfraSyncResponse(
        synced=synced,
        created=created,
        updated=updated,
        payments_imported=payments_imported,
        message=(
            f'Synced {synced} billing nodes, {len(providers)} providers, '
            f'{payments_imported} payments from RemnaWave'
        ),
    )
