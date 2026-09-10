"""Контроль уникальных IP по данным Remnawave — детекция шаринга подписок."""

from __future__ import annotations

import asyncio
import html
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog

from app.config import settings
from app.database.crud.user import get_user_by_remnawave_id
from app.database.database import AsyncSessionLocal
from app.external.remnawave_api import UserStatus
from app.services.admin_notification_service import AdminNotificationService
from app.services.ip_control_utils import (
    count_sources,
    extract_ip,
    extract_panel_user_id,
    is_sharing_threshold,
    normalize_chat_id,
    normalize_topic_id,
    parse_request_time,
    resolve_alert_destination,
    unique_ips_from_history,
)
from app.services.remnawave_service import RemnaWaveService
from app.utils.cache import cache, cache_key


logger = structlog.get_logger(__name__)

IP_CONTROL_RESULTS_KEY = 'ip_control:last_scan'
IP_CONTROL_NOTIFY_KEY = 'ip_control:notifications'
IP_SAMPLE_LIMIT = 8
HISTORY_LIMIT = 100
MAX_ALERTS_PER_RUN = 10


@dataclass
class IpControlOffender:
    panel_user_id: int
    unique_ip_count: int
    address_count: int
    device_count: int
    ips: list[str]
    telegram_id: int | None = None
    username: str | None = None
    full_name: str | None = None
    email: str | None = None
    local_user_id: int | None = None
    device_limit: int | None = None


@dataclass
class IpControlScanResult:
    scanned_at: str
    threshold: int
    window_hours: int
    users_checked: int
    offender_count: int
    alerts_sent: int
    offenders: list[IpControlOffender] = field(default_factory=list)


class IpControlService:
    def __init__(self) -> None:
        self.remnawave_service = RemnaWaveService()
        self._memory_results: dict[str, Any] | None = None
        self._memory_notifications: dict[str, datetime] = {}
        self._scan_lock = asyncio.Lock()

    def is_enabled(self) -> bool:
        return bool(getattr(settings, 'IP_CONTROL_ENABLED', False))

    def get_threshold(self) -> int:
        return max(2, int(getattr(settings, 'IP_CONTROL_UNIQUE_IP_THRESHOLD', 5) or 5))

    def get_window_hours(self) -> int:
        return max(1, int(getattr(settings, 'IP_CONTROL_WINDOW_HOURS', 24) or 24))

    def get_interval_seconds(self) -> int:
        minutes = max(5, int(getattr(settings, 'IP_CONTROL_INTERVAL_MINUTES', 60) or 60))
        return minutes * 60

    def get_cooldown_seconds(self) -> int:
        hours = max(1, int(getattr(settings, 'IP_CONTROL_COOLDOWN_HOURS', 12) or 12))
        return hours * 3600

    def get_concurrency(self) -> int:
        return max(1, int(getattr(settings, 'TRAFFIC_CHECK_CONCURRENCY', 5) or 5))

    def get_batch_size(self) -> int:
        return max(50, int(getattr(settings, 'TRAFFIC_CHECK_BATCH_SIZE', 1000) or 1000))

    def get_settings_snapshot(self) -> dict[str, Any]:
        chat_id, topic_id = self.get_alert_destination()
        return {
            'enabled': self.is_enabled(),
            'threshold': self.get_threshold(),
            'window_hours': self.get_window_hours(),
            'interval_minutes': self.get_interval_seconds() // 60,
            'cooldown_hours': self.get_cooldown_seconds() // 3600,
            'chat_id': self.get_configured_chat_id(),
            'topic_id': self.get_configured_topic_id(),
            'default_chat_id': self.get_default_chat_id(),
            'default_topic_id': self.get_default_topic_id(),
            'effective_chat_id': chat_id,
            'effective_topic_id': topic_id,
        }

    def get_configured_chat_id(self) -> str | None:
        return normalize_chat_id(getattr(settings, 'IP_CONTROL_CHAT_ID', None))

    def get_configured_topic_id(self) -> int | None:
        return normalize_topic_id(getattr(settings, 'IP_CONTROL_TOPIC_ID', None))

    def get_default_chat_id(self) -> str | None:
        return normalize_chat_id(getattr(settings, 'ADMIN_NOTIFICATIONS_CHAT_ID', None))

    def get_default_topic_id(self) -> int | None:
        return normalize_topic_id(getattr(settings, 'SUSPICIOUS_NOTIFICATIONS_TOPIC_ID', None))

    def get_alert_destination(self) -> tuple[str | None, int | None]:
        return resolve_alert_destination(
            self.get_configured_chat_id(),
            self.get_configured_topic_id(),
            default_chat_id=self.get_default_chat_id(),
            default_topic_id=self.get_default_topic_id(),
            fallback_topic_id=getattr(settings, 'ADMIN_NOTIFICATIONS_TOPIC_ID', None),
        )

    async def get_last_scan(self) -> dict[str, Any] | None:
        try:
            data = await cache.get(IP_CONTROL_RESULTS_KEY)
            if isinstance(data, dict):
                return data
        except Exception as error:
            logger.warning('Failed to load IP control scan from Redis', error=error)
        return self._memory_results

    async def _save_last_scan(self, result: IpControlScanResult) -> None:
        payload = {
            'scanned_at': result.scanned_at,
            'threshold': result.threshold,
            'window_hours': result.window_hours,
            'users_checked': result.users_checked,
            'offender_count': result.offender_count,
            'alerts_sent': result.alerts_sent,
            'offenders': [asdict(item) for item in result.offenders],
        }
        ttl = max(self.get_window_hours() * 3600, 6 * 3600)
        try:
            saved = await cache.set(IP_CONTROL_RESULTS_KEY, payload, expire=ttl)
            if saved:
                self._memory_results = payload
                return
        except Exception as error:
            logger.warning('Failed to save IP control scan to Redis', error=error)
        self._memory_results = payload

    async def _should_notify(self, panel_user_id: int) -> bool:
        key = cache_key(IP_CONTROL_NOTIFY_KEY, str(panel_user_id))
        last: datetime | None = None
        try:
            raw = await cache.get(key)
            last = parse_request_time(raw)
        except Exception as error:
            logger.debug('IP control notify cache read failed', error=error)
        if last is None:
            last = self._memory_notifications.get(str(panel_user_id))
        if last is None:
            return True
        return (datetime.now(UTC) - last).total_seconds() >= self.get_cooldown_seconds()

    async def _record_notify(self, panel_user_id: int) -> None:
        now = datetime.now(UTC)
        key = cache_key(IP_CONTROL_NOTIFY_KEY, str(panel_user_id))
        try:
            await cache.set(key, now.isoformat(), expire=self.get_cooldown_seconds())
        except Exception as error:
            logger.debug('IP control notify cache write failed', error=error)
        self._memory_notifications[str(panel_user_id)] = now

    async def _load_panel_users(self, api) -> list[Any]:
        users: list[Any] = []
        offset = 0
        batch_size = self.get_batch_size()
        while True:
            result = await api.get_all_users(start=offset, size=batch_size)
            batch = result.get('users') or []
            if not batch:
                break
            users.extend(batch)
            if len(batch) < batch_size:
                break
            offset += batch_size
        return users

    async def _collect_device_ips(self, api) -> tuple[dict[int, set[str]], dict[int, int]]:
        ips_by_user: dict[int, set[str]] = defaultdict(set)
        devices_by_user: dict[int, int] = defaultdict(int)
        try:
            payload = await api.get_all_hwid_devices()
        except Exception as error:
            logger.warning('Failed to load HWID devices for IP control', error=error)
            return ips_by_user, devices_by_user

        for device in payload.get('devices') or []:
            panel_id = extract_panel_user_id(device)
            if panel_id is None:
                continue
            devices_by_user[panel_id] += 1
            ip = extract_ip(device)
            if ip:
                ips_by_user[panel_id].add(ip)
        return ips_by_user, devices_by_user

    async def _merge_history_ips(
        self,
        api,
        users: list[Any],
        ips_by_user: dict[int, set[str]],
        cutoff: datetime,
    ) -> None:
        semaphore = asyncio.Semaphore(self.get_concurrency())

        async def fetch_one(user: Any) -> None:
            panel_id = getattr(user, 'id', None)
            if panel_id is None:
                return
            async with semaphore:
                try:
                    history = await api.get_subscription_request_history(
                        panel_id, offset=0, limit=HISTORY_LIMIT
                    )
                except Exception as error:
                    logger.debug('IP control history fetch failed', panel_user_id=panel_id, error=error)
                    return
            records = history.get('records') or history.get('items') or []
            if not isinstance(records, list):
                return
            for ip in unique_ips_from_history(records, cutoff=cutoff):
                ips_by_user[int(panel_id)].add(ip)

        await asyncio.gather(*(fetch_one(user) for user in users), return_exceptions=True)

    async def _enrich_offenders(self, offenders: list[IpControlOffender]) -> None:
        if not offenders:
            return
        async with AsyncSessionLocal() as db:
            for offender in offenders:
                try:
                    db_user = await get_user_by_remnawave_id(db, offender.panel_user_id)
                except Exception as error:
                    logger.debug(
                        'Failed to map Remnawave user for IP control',
                        panel_user_id=offender.panel_user_id,
                        error=error,
                    )
                    continue
                if not db_user:
                    continue
                offender.local_user_id = db_user.id
                offender.telegram_id = db_user.telegram_id
                offender.username = db_user.username
                offender.full_name = db_user.full_name
                offender.email = db_user.email
                subscription = getattr(db_user, 'subscription', None)
                if subscription is not None:
                    offender.device_limit = getattr(subscription, 'device_limit', None)

    async def _send_alerts(self, offenders: list[IpControlOffender], bot) -> int:
        if not bot or not offenders:
            return 0
        admin_service = AdminNotificationService(bot)
        chat_id, topic_id = self.get_alert_destination()
        sent = 0
        for offender in offenders[:MAX_ALERTS_PER_RUN]:
            if not await self._should_notify(offender.panel_user_id):
                continue
            identity = html.escape(offender.full_name or offender.username or 'Без имени')
            user_lines = [f'👤 <b>{identity}</b>']
            if offender.telegram_id:
                user_lines.append(f'🆔 Telegram: <code>{offender.telegram_id}</code>')
            elif offender.email:
                user_lines.append(f'✉️ Email: <code>{html.escape(offender.email)}</code>')
            if offender.username:
                user_lines.append(f'📱 @{html.escape(offender.username)}')
            if offender.local_user_id:
                user_lines.append(f'cabinet: #{offender.local_user_id}')

            ip_preview = ', '.join(f'<code>{html.escape(ip)}</code>' for ip in offender.ips[:IP_SAMPLE_LIMIT])
            extra = '' if len(offender.ips) <= IP_SAMPLE_LIMIT else f' (+{len(offender.ips) - IP_SAMPLE_LIMIT})'
            sources_line = f'🌐 Источников: <b>{offender.unique_ip_count}</b> (порог {self.get_threshold()})'
            if offender.address_count > offender.unique_ip_count:
                sources_line = (
                    f'🌐 Источников: <b>{offender.unique_ip_count}</b> '
                    f'({offender.address_count} адресов, порог {self.get_threshold()})'
                )

            message = (
                '⚠️ <b>Возможный шаринг подписки</b>\n\n'
                + '\n'.join(user_lines)
                + f'\n🔑 Panel ID: <code>{offender.panel_user_id}</code>\n\n'
                f'{sources_line}\n'
                f'📱 HWID-устройств: <b>{offender.device_count}</b>\n'
            )
            if offender.device_limit:
                message += f'📋 Лимит устройств: <b>{offender.device_limit}</b>\n'
            if ip_preview:
                message += f'\nIP: {ip_preview}{extra}'
            message += f'\n\n⏰ {datetime.now(UTC).strftime("%d.%m.%Y %H:%M")} UTC'

            try:
                ok = await admin_service.send_suspicious_traffic_notification(
                    message, bot, topic_id, chat_id=chat_id
                )
            except Exception as error:
                logger.warning(
                    'Failed to send IP control alert',
                    panel_user_id=offender.panel_user_id,
                    error=error,
                )
                continue
            if ok:
                await self._record_notify(offender.panel_user_id)
                sent += 1
        return sent

    async def run_scan(self, bot=None, *, notify: bool = True) -> IpControlScanResult:
        async with self._scan_lock:
            return await self._run_scan_locked(bot=bot, notify=notify)

    async def _run_scan_locked(self, bot=None, *, notify: bool = True) -> IpControlScanResult:
        threshold = self.get_threshold()
        window_hours = self.get_window_hours()
        cutoff = datetime.now(UTC) - timedelta(hours=window_hours)
        scanned_at = datetime.now(UTC).isoformat()
        empty = IpControlScanResult(
            scanned_at=scanned_at,
            threshold=threshold,
            window_hours=window_hours,
            users_checked=0,
            offender_count=0,
            alerts_sent=0,
        )

        if not self.remnawave_service.is_configured:
            logger.warning('IP control skipped: Remnawave is not configured')
            await self._save_last_scan(empty)
            return empty

        async with self.remnawave_service.get_api_client() as api:
            ips_by_user, devices_by_user = await self._collect_device_ips(api)
            users = await self._load_panel_users(api)
            candidates = []
            for user in users:
                status = getattr(user, 'status', None)
                if status not in (UserStatus.ACTIVE, UserStatus.LIMITED):
                    continue
                online_at = parse_request_time(getattr(user, 'online_at', None))
                recently_online = online_at is not None and online_at >= cutoff
                panel_id = int(user.id)
                already_hot = count_sources(ips_by_user.get(panel_id, set())) >= threshold
                if recently_online or already_hot:
                    candidates.append(user)

            await self._merge_history_ips(api, candidates, ips_by_user, cutoff)

        offenders: list[IpControlOffender] = []
        checked_ids = {int(user.id) for user in candidates}
        hot_from_devices = {
            pid
            for pid, ips in ips_by_user.items()
            if is_sharing_threshold(count_sources(ips), threshold)
        }
        for panel_id in sorted(checked_ids | hot_from_devices):
            ips = sorted(ips_by_user.get(panel_id, set()))
            source_count = count_sources(ips)
            if not is_sharing_threshold(source_count, threshold):
                continue
            offenders.append(
                IpControlOffender(
                    panel_user_id=panel_id,
                    unique_ip_count=source_count,
                    address_count=len(ips),
                    device_count=devices_by_user.get(panel_id, 0),
                    ips=ips,
                )
            )

        offenders.sort(key=lambda item: item.unique_ip_count, reverse=True)
        await self._enrich_offenders(offenders)
        alerts_sent = 0
        if notify:
            alerts_sent = await self._send_alerts(offenders, bot)

        result = IpControlScanResult(
            scanned_at=scanned_at,
            threshold=threshold,
            window_hours=window_hours,
            users_checked=len(candidates),
            offender_count=len(offenders),
            alerts_sent=alerts_sent,
            offenders=offenders,
        )
        await self._save_last_scan(result)
        logger.info(
            'IP control scan finished',
            users_checked=result.users_checked,
            offenders=result.offender_count,
            alerts_sent=result.alerts_sent,
            threshold=threshold,
        )
        return result


class IpControlScheduler:
    def __init__(self, service: IpControlService) -> None:
        self.service = service
        self.bot = None
        self._task: asyncio.Task | None = None
        self._is_running = False

    def set_bot(self, bot) -> None:
        self.bot = bot

    def is_running(self) -> bool:
        return self._is_running

    async def start(self) -> None:
        if self._is_running:
            return
        self._is_running = True
        self._task = asyncio.create_task(self._loop())
        logger.info('IP control scheduler started')

    async def stop(self) -> None:
        self._is_running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info('IP control scheduler stopped')

    async def start_monitoring(self) -> None:
        await self.start()
        while self._is_running:
            await asyncio.sleep(1)

    def stop_monitoring(self) -> None:
        self._is_running = False
        if self._task:
            self._task.cancel()

    async def _loop(self) -> None:
        first_delay = min(self.service.get_interval_seconds(), 120)
        await asyncio.sleep(first_delay)
        while self._is_running:
            try:
                if self.service.is_enabled():
                    await self.service.run_scan(self.bot, notify=True)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.error('IP control scan failed', error=error)
            await asyncio.sleep(self.service.get_interval_seconds())


ip_control_service = IpControlService()
ip_control_scheduler = IpControlScheduler(ip_control_service)
