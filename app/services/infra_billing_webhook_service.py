"""Persist RemnaWave CRM infra billing webhook events."""

from __future__ import annotations

from datetime import UTC, datetime

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.crud.infra_billing import (
    compute_payment_status,
    create_payment,
    create_server,
    get_or_create_provider_by_name,
    parse_amount_kopeks,
    parse_date_value,
    status_from_event,
    update_server,
    upsert_server_from_node,
)
from app.database.models import InfraPaymentSource, InfraPaymentStatus


logger = structlog.get_logger(__name__)

_PAID_EVENT_MARKERS = ('payment_completed', 'payment_paid', 'node_paid')


class InfraBillingWebhookService:
    async def handle_event(self, db: AsyncSession, event_name: str, data: dict) -> None:
        node_uuid = str(data.get('uuid') or data.get('nodeUuid') or '').strip()
        name = str(data.get('nodeName') or data.get('name') or '').strip() or 'Unknown node'
        address = data.get('address') or data.get('ip')
        provider_name = data.get('providerName')
        login_url = data.get('loginUrl')
        amount_kopeks = parse_amount_kopeks(data.get('amount') or data.get('price'))
        due_date = parse_date_value(data.get('dueDate') or data.get('nextBillingAt'))
        payment_date = parse_date_value(data.get('paymentDate'))
        event_status = status_from_event(event_name)
        is_paid_event = any(marker in event_name for marker in _PAID_EVENT_MARKERS)

        provider = None
        if provider_name and str(provider_name).strip():
            provider = await get_or_create_provider_by_name(
                db,
                str(provider_name).strip(),
                login_url=str(login_url).strip() if login_url else None,
            )

        server = None
        if node_uuid:
            server = await upsert_server_from_node(
                db,
                node_uuid=node_uuid,
                name=name,
                address=str(address).strip() if address else None,
                provider_name=str(provider_name).strip() if provider_name else None,
                provider_login_url=str(login_url).strip() if login_url else None,
                next_due_date=due_date,
            )
        elif name:
            from sqlalchemy import select

            from app.database.models import InfraServer

            result = await db.execute(select(InfraServer).where(InfraServer.name == name).limit(1))
            server = result.scalars().first()
            if not server:
                server = await create_server(
                    db,
                    name=name,
                    address=str(address).strip() if address else None,
                    provider_id=provider.id if provider else None,
                )

        if not server:
            logger.warning('Infra billing webhook: server not resolved', event_name=event_name)
            return

        if provider and not server.provider_id:
            server.provider_id = provider.id

        if amount_kopeks is not None and server.billing_amount_kopeks is None:
            server.billing_amount_kopeks = amount_kopeks

        if due_date is not None:
            server.next_due_date = due_date

        if event_status:
            server.payment_status = event_status
        elif due_date is not None:
            server.payment_status = compute_payment_status(due_date)

        if is_paid_event and amount_kopeks:
            paid_at = datetime.now(UTC)
            if payment_date:
                paid_at = datetime.combine(payment_date, datetime.min.time(), tzinfo=UTC)
            await create_payment(
                db,
                server=server,
                amount_kopeks=amount_kopeks,
                paid_at=paid_at,
                due_date=due_date,
                source=InfraPaymentSource.WEBHOOK.value,
                source_event=event_name,
                notes='Auto-recorded from RemnaWave webhook',
                extend_due_date=due_date,
            )
            server.payment_status = InfraPaymentStatus.PAID.value

        await update_server(db, server)

        logger.info(
            'Infra billing webhook persisted',
            event_name=event_name,
            server_id=server.id,
            node_uuid=node_uuid or None,
        )
