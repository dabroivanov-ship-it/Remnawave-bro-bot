"""Periodic Remnawave infra-billing reminders for admin Telegram groups."""

from __future__ import annotations

import asyncio
import html
from datetime import UTC, date, datetime, timedelta

import structlog
from aiogram import Bot
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database.crud.infra_billing import compute_payment_status
from app.database.database import AsyncSessionLocal
from app.database.models import InfraServer
from app.utils.cache import cache


logger = structlog.get_logger(__name__)

# Warn admins N days before next_due_date (plus today / overdue).
_REMINDER_DAYS = frozenset({3, 2, 1, 0})


def _days_until_due(next_due_date: date | None) -> int | None:
    if next_due_date is None:
        return None
    return (next_due_date - date.today()).days


def _reminder_title(days_left: int) -> str:
    if days_left < 0:
        return '❗ Просрочена оплата сервера Remnawave'
    if days_left == 0:
        return '🔴 Оплата сервера Remnawave сегодня'
    if days_left == 1:
        return '⚠️ Оплата сервера Remnawave завтра'
    if days_left == 2:
        return '💳 Оплата сервера Remnawave через 2 дня'
    if days_left == 3:
        return '💳 Оплата сервера Remnawave через 3 дня'
    return f'💳 Оплата сервера Remnawave через {days_left} дн.'


class InfraBillingNotifyService:
    """Sends admin-chat alerts for upcoming / overdue infra bills."""

    def __init__(self) -> None:
        self.bot: Bot | None = None
        self._task: asyncio.Task | None = None
        self._memory_dedupe: dict[str, datetime] = {}

    def set_bot(self, bot: Bot) -> None:
        self.bot = bot

    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        await self.stop()
        if not settings.ADMIN_NOTIFICATIONS_ENABLED or not settings.WEBHOOK_NOTIFY_INFRA_BILLING:
            logger.info('Infra billing notify service disabled by settings')
            return
        if not self.bot:
            logger.warning('Infra billing notify service: bot is not set')
            return

        self._task = asyncio.create_task(self._loop())
        logger.info('Infra billing notify service started (reminders: 3/2/1/0 days + overdue)')

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None

    async def _loop(self) -> None:
        # First pass shortly after boot, then every 6 hours
        await asyncio.sleep(90)
        while True:
            try:
                await self.check_and_notify()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception('Infra billing notify check failed')
            await asyncio.sleep(6 * 60 * 60)

    async def check_and_notify(self) -> int:
        """Notify about payments due in 3/2/1 days, today, or overdue."""
        if not settings.ADMIN_NOTIFICATIONS_ENABLED or not settings.WEBHOOK_NOTIFY_INFRA_BILLING:
            return 0
        if not self.bot:
            return 0

        from app.services.admin_notification_service import AdminNotificationService

        notifier = AdminNotificationService(self.bot)
        sent = 0

        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(InfraServer)
                .where(InfraServer.is_active.is_(True))
                .options(selectinload(InfraServer.provider))
            )
            servers = list(result.scalars().all())

            changed = False
            for server in servers:
                live = compute_payment_status(server.next_due_date)
                if server.payment_status != live:
                    server.payment_status = live
                    changed = True
            if changed:
                await db.commit()

            for server in servers:
                days_left = _days_until_due(server.next_due_date)
                if days_left is None:
                    continue
                if days_left > 3:
                    continue
                if days_left >= 0 and days_left not in _REMINDER_DAYS:
                    continue
                # days_left < 0 → overdue (notify once per calendar day)

                if not await self._claim_alert(server, days_left):
                    continue

                text = self._format_status_alert(server, days_left)
                if await notifier.send_infra_billing_notification(text):
                    sent += 1

        if sent:
            logger.info('Infra billing alerts sent', count=sent)
        return sent

    async def notify_payment(
        self,
        *,
        server_name: str,
        provider_name: str | None,
        amount_kopeks: int,
        source: str,
        paid_at: datetime | None = None,
        actor: str | None = None,
    ) -> bool:
        if not settings.ADMIN_NOTIFICATIONS_ENABLED or not settings.WEBHOOK_NOTIFY_INFRA_BILLING:
            return False
        if not self.bot:
            return False

        from app.services.admin_notification_service import AdminNotificationService

        amount = (amount_kopeks or 0) / 100
        lines = [
            '<b>✅ Оплата сервера Remnawave</b>',
            f'Сервер: <code>{html.escape(server_name)}</code>',
            f'Провайдер: <code>{html.escape(provider_name or "—")}</code>',
            f'Сумма: <code>${amount:.2f}</code>',
            f'Источник: <code>{html.escape(source)}</code>',
        ]
        if paid_at:
            lines.append(
                f'Дата: <code>{html.escape(paid_at.astimezone(UTC).strftime("%d.%m.%Y %H:%M UTC"))}</code>'
            )
        if actor:
            lines.append(f'Кто: <code>{html.escape(actor)}</code>')

        return await AdminNotificationService(self.bot).send_infra_billing_notification('\n'.join(lines))

    async def notify_payments_imported(self, items: list[dict]) -> bool:
        """Digest for newly imported Remnawave history payments."""
        if not items:
            return False
        if not settings.ADMIN_NOTIFICATIONS_ENABLED or not settings.WEBHOOK_NOTIFY_INFRA_BILLING:
            return False
        if not self.bot:
            return False

        from app.services.admin_notification_service import AdminNotificationService

        total = sum(int(i.get('amount_kopeks') or 0) for i in items)
        lines = [
            f'<b>✅ Импорт оплат Remnawave: {len(items)}</b>',
            f'Сумма: <code>${total / 100:.2f}</code>',
            '',
        ]
        for item in items[:15]:
            name = html.escape(str(item.get('server_name') or '—'))
            provider = html.escape(str(item.get('provider_name') or '—'))
            amount = int(item.get('amount_kopeks') or 0) / 100
            lines.append(f'• <code>{name}</code> ({provider}) — ${amount:.2f}')
        if len(items) > 15:
            lines.append(f'… и ещё {len(items) - 15}')

        return await AdminNotificationService(self.bot).send_infra_billing_notification('\n'.join(lines))

    def _format_status_alert(self, server: InfraServer, days_left: int) -> str:
        title = _reminder_title(days_left)
        provider = server.provider.name if server.provider else '—'
        due = server.next_due_date.isoformat() if server.next_due_date else '—'
        amount = (
            f'${(server.billing_amount_kopeks or 0) / 100:.2f}'
            if server.billing_amount_kopeks
            else '—'
        )
        if days_left < 0:
            timing = f'Просрочка: <code>{abs(days_left)} дн.</code>'
        elif days_left == 0:
            timing = 'Срок: <code>сегодня</code>'
        elif days_left == 1:
            timing = 'Срок: <code>завтра</code>'
        else:
            timing = f'Осталось: <code>{days_left} дн.</code>'

        lines = [
            f'<b>{title}</b>',
            f'Сервер: <code>{html.escape(server.name)}</code>',
            f'Провайдер: <code>{html.escape(provider)}</code>',
            f'След. оплата: <code>{html.escape(due)}</code>',
            f'Сумма: <code>{html.escape(amount)}</code>',
            timing,
        ]
        if server.address:
            lines.append(f'Адрес: <code>{html.escape(server.address)}</code>')
        return '\n'.join(lines)

    async def _claim_alert(self, server: InfraServer, days_left: int) -> bool:
        """One alert per server / due-date / reminder milestone (or per overdue day)."""
        due = server.next_due_date.isoformat() if server.next_due_date else 'none'
        today = date.today().isoformat()
        if days_left < 0:
            key = f'infra_billing:alert:{server.id}:overdue:{due}:{today}'
        else:
            # Fire once for each milestone (3 / 2 / 1 / 0) for this due date
            key = f'infra_billing:alert:{server.id}:d{days_left}:{due}'

        claimed = await cache.setnx(key, True, expire=timedelta(days=14))
        if claimed:
            return True

        if getattr(cache, '_connected', False):
            return False

        now = datetime.now(UTC)
        expired = [k for k, ts in self._memory_dedupe.items() if now - ts > timedelta(days=14)]
        for k in expired:
            self._memory_dedupe.pop(k, None)
        if key in self._memory_dedupe:
            return False
        self._memory_dedupe[key] = now
        return True


infra_billing_notify_service = InfraBillingNotifyService()
