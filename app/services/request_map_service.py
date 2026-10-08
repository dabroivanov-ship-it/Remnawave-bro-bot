"""Store and locate IPs from VPN connects and subscription updates.

Webhook payloads often omit the client address. Opening the map also reads
real IPs that RemnaWave already stored: subscription-request history
(`requestIp`) and HWID devices (`requestIp` / `ip` / `ipAddress`).
"""

import asyncio
import ipaddress
import time
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.database import AsyncSessionLocal
from app.database.models import RemnawaveRequestEvent, Subscription, User
from app.services.ip_control_utils import extract_ip as extract_record_ip
from app.services.ip_control_utils import extract_panel_user_id, parse_request_time


logger = structlog.get_logger(__name__)

_CONNECT_EVENTS = {'user.first_connected', 'user_hwid_devices.added'}
_SUBSCRIPTION_EVENTS = {'user.modified', 'user.created', 'user.enabled'}
_IP_KEYS = {'ip', 'ipaddress', 'requestip', 'clientip', 'lastconnectedip'}


def event_kind(event_name: str) -> str | None:
    if event_name in _CONNECT_EVENTS:
        return 'connect'
    if event_name in _SUBSCRIPTION_EVENTS:
        return 'subscription_update'
    return None


def extract_ip(payload: object) -> str | None:
    """Pull the first public-looking IP out of a webhook payload."""
    found: list[str] = []

    def walk(value: object) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if str(key).lower() in _IP_KEYS and isinstance(item, str):
                    found.append(item.strip())
                else:
                    walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(payload)
    for candidate in found:
        if candidate and candidate not in {'0.0.0.0', '::'} and not candidate.startswith(('10.', '127.', '192.168.', '172.')):
            return candidate[:45]
    return None


async def record_request_event(
    db: AsyncSession,
    *,
    event_name: str,
    payload: dict,
    user_id: int | None,
) -> None:
    """Remember an IP when the webhook actually contains one. Never raises."""
    kind = event_kind(event_name)
    ip_address = extract_ip(payload)
    if kind is None or ip_address is None:
        return
    occurred_at = datetime.now(UTC)
    dedupe_key = f'{kind}:{user_id or 0}:{ip_address}:{occurred_at.strftime("%Y%m%d%H%M")}'[:160]
    try:
        await db.execute(
            pg_insert(RemnawaveRequestEvent)
            .values(
                event_type=kind,
                ip_address=ip_address,
                occurred_at=occurred_at,
                user_id=user_id,
                dedupe_key=dedupe_key,
            )
            .on_conflict_do_nothing(constraint='uq_remnawave_request_events_dedupe')
        )
    except Exception as error:
        logger.warning('Failed to store request-map event', error=error)


_PANEL_SYNC_LOCK = asyncio.Lock()
_last_panel_sync_at = 0.0
_PANEL_SYNC_COOLDOWN_SECONDS = 90.0
_PANEL_USER_CAP = 60
_PANEL_HISTORY_LIMIT = 30
_PANEL_DEVICE_CAP = 1500
_PANEL_CONCURRENCY = 6
_HISTORY_CALL_TIMEOUT_SECONDS = 8.0
_DEVICE_CALL_TIMEOUT_SECONDS = 12.0
_HISTORY_BUDGET_SECONDS = 15.0
_INSERT_CHUNK = 200
_EXTRA_IP_KEYS = ('lastConnectedIp', 'last_connected_ip', 'clientIp', 'client_ip')
_HISTORY_TIME_KEYS = ('requestAt', 'request_at', 'requestedAt', 'createdAt')
_DEVICE_TIME_KEYS = ('updatedAt', 'lastSeen', 'lastActiveAt', 'createdAt')


def _global_ip(value: str) -> str | None:
    text = value.strip()
    if not text or len(text) > 45:
        return None
    try:
        parsed = ipaddress.ip_address(text)
    except ValueError:
        return None
    if not parsed.is_global:
        return None
    return str(parsed)


def public_request_ip(payload: object) -> str | None:
    """Public IP from a history record or HWID device. Missing fields stay empty."""
    if not isinstance(payload, dict):
        return None
    raw = extract_record_ip(payload)
    if not raw:
        for key in _EXTRA_IP_KEYS:
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                raw = value.strip()
                break
    if not raw:
        return None
    return _global_ip(raw)


def _record_time(payload: dict, keys: tuple[str, ...]) -> datetime | None:
    for key in keys:
        if key not in payload:
            continue
        parsed = parse_request_time(payload.get(key))
        if parsed is not None:
            return parsed
    return None


def subscription_events_from_history(
    records: object,
    *,
    panel_user_id: int,
    cutoff: datetime,
) -> list[dict]:
    """Subscription-fetch points from requestIp values inside the window."""
    if not isinstance(records, list):
        return []
    events: list[dict] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        ip_address = public_request_ip(record)
        occurred_at = _record_time(record, _HISTORY_TIME_KEYS)
        if ip_address is None or occurred_at is None or occurred_at < cutoff:
            continue
        owner = extract_panel_user_id(record) or panel_user_id
        events.append(
            {
                'event_type': 'subscription_update',
                'panel_user_id': owner,
                'ip_address': ip_address,
                'occurred_at': occurred_at,
            }
        )
    return events


def connect_events_from_devices(
    devices: object,
    *,
    cutoff: datetime,
) -> tuple[list[dict], list[int]]:
    """Connect points from device IPs, plus panel ids touched inside the window."""
    if not isinstance(devices, list):
        return [], []
    events: list[dict] = []
    panel_ids: list[int] = []
    seen_ids: set[int] = set()
    for device in devices:
        if not isinstance(device, dict):
            continue
        occurred_at = _record_time(device, _DEVICE_TIME_KEYS)
        if occurred_at is None or occurred_at < cutoff:
            continue
        panel_user_id = extract_panel_user_id(device)
        if panel_user_id is not None and panel_user_id not in seen_ids:
            seen_ids.add(panel_user_id)
            panel_ids.append(panel_user_id)
        ip_address = public_request_ip(device)
        if ip_address is None:
            continue
        events.append(
            {
                'event_type': 'connect',
                'panel_user_id': panel_user_id,
                'ip_address': ip_address,
                'occurred_at': occurred_at,
            }
        )
    return events, panel_ids


def _history_records(history: object) -> list:
    if not isinstance(history, dict):
        return []
    records = history.get('records') or history.get('items') or []
    return records if isinstance(records, list) else []


def _dedupe_key(event: dict, local_user_id: int | None) -> str:
    occurred_at = event['occurred_at'].astimezone(UTC)
    panel_user_id = event.get('panel_user_id') or 0
    return (
        f'{event["event_type"]}:{local_user_id or 0}:{panel_user_id}:{event["ip_address"]}:'
        f'{occurred_at.strftime("%Y%m%d%H%M")}'
    )[:160]


async def _local_panel_ids(db: AsyncSession, cutoff: datetime, cap: int) -> list[int]:
    selected: list[int] = []
    seen: set[int] = set()

    def add(value: object) -> None:
        if value is None or len(selected) >= cap:
            return
        try:
            panel_id = int(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return
        if panel_id in seen:
            return
        seen.add(panel_id)
        selected.append(panel_id)

    recent_users = await db.execute(
        select(User.remnawave_id)
        .where(User.remnawave_id.is_not(None), User.last_activity >= cutoff)
        .order_by(User.last_activity.desc())
        .limit(cap)
    )
    for (panel_id,) in recent_users:
        add(panel_id)

    if len(selected) < cap:
        recent_subs = await db.execute(
            select(Subscription.remnawave_id)
            .where(Subscription.remnawave_id.is_not(None), Subscription.updated_at >= cutoff)
            .order_by(Subscription.updated_at.desc())
            .limit(cap)
        )
        for (panel_id,) in recent_subs:
            add(panel_id)

    if len(selected) < cap:
        filler = await db.execute(
            select(User.remnawave_id)
            .where(User.remnawave_id.is_not(None))
            .order_by(User.last_activity.desc())
            .limit(cap)
        )
        for (panel_id,) in filler:
            add(panel_id)
    return selected


async def _local_user_ids(db: AsyncSession, panel_ids: set[int]) -> dict[int, int]:
    if not panel_ids:
        return {}
    mapping: dict[int, int] = {}
    user_rows = await db.execute(
        select(User.id, User.remnawave_id).where(User.remnawave_id.in_(panel_ids))
    )
    for local_id, panel_id in user_rows:
        if panel_id is not None:
            mapping[int(panel_id)] = int(local_id)
    missing = panel_ids.difference(mapping)
    if not missing:
        return mapping
    sub_rows = await db.execute(
        select(Subscription.user_id, Subscription.remnawave_id).where(Subscription.remnawave_id.in_(missing))
    )
    for local_id, panel_id in sub_rows:
        if panel_id is None:
            continue
        mapping.setdefault(int(panel_id), int(local_id))
    return mapping


async def _history_events(api, panel_ids: list[int], cutoff: datetime) -> list[dict]:
    if not panel_ids:
        return []
    semaphore = asyncio.Semaphore(_PANEL_CONCURRENCY)
    deadline = time.monotonic() + _HISTORY_BUDGET_SECONDS

    async def fetch_one(panel_id: int) -> list[dict]:
        if time.monotonic() >= deadline:
            return []
        async with semaphore:
            if time.monotonic() >= deadline:
                return []
            try:
                history = await asyncio.wait_for(
                    api.get_subscription_request_history(panel_id, offset=0, limit=_PANEL_HISTORY_LIMIT),
                    timeout=_HISTORY_CALL_TIMEOUT_SECONDS,
                )
            except Exception as error:
                logger.debug('Request map history fetch failed', panel_user_id=panel_id, error=error)
                return []
        return subscription_events_from_history(_history_records(history), panel_user_id=panel_id, cutoff=cutoff)

    batches = await asyncio.gather(*(fetch_one(panel_id) for panel_id in panel_ids), return_exceptions=True)
    events: list[dict] = []
    for batch in batches:
        if isinstance(batch, list):
            events.extend(batch)
    return events


async def _device_snapshot(api, cutoff: datetime) -> tuple[list[dict], list[int]]:
    try:
        payload = await asyncio.wait_for(
            api.get_all_hwid_devices(max_devices=_PANEL_DEVICE_CAP),
            timeout=_DEVICE_CALL_TIMEOUT_SECONDS,
        )
    except Exception as error:
        logger.warning('Request map device fetch failed', error=error)
        return [], []
    devices = payload.get('devices') if isinstance(payload, dict) else None
    return connect_events_from_devices(devices, cutoff=cutoff)


def _rows_for_insert(events: list[dict], local_ids: dict[int, int]) -> list[dict]:
    unique: dict[str, dict] = {}
    for event in events:
        panel_user_id = event.get('panel_user_id')
        local_user_id = local_ids.get(int(panel_user_id)) if panel_user_id is not None else None
        dedupe_key = _dedupe_key(event, local_user_id)
        unique[dedupe_key] = {
            'event_type': event['event_type'],
            'ip_address': event['ip_address'],
            'occurred_at': event['occurred_at'],
            'user_id': local_user_id,
            'panel_user_id': panel_user_id,
            'dedupe_key': dedupe_key,
        }
    return list(unique.values())


async def _insert_events(db: AsyncSession, rows: list[dict]) -> None:
    for offset in range(0, len(rows), _INSERT_CHUNK):
        chunk = rows[offset : offset + _INSERT_CHUNK]
        await db.execute(
            pg_insert(RemnawaveRequestEvent)
            .values(chunk)
            .on_conflict_do_nothing(constraint='uq_remnawave_request_events_dedupe')
        )


async def _sync_panel_request_events(db: AsyncSession, service, *, days: int) -> None:
    cutoff = datetime.now(UTC) - timedelta(days=days)
    local_ids = await _local_panel_ids(db, cutoff, _PANEL_USER_CAP)
    async with service.get_api_client() as api:
        device_events, device_panel_ids = await _device_snapshot(api, cutoff)
        history_ids: list[int] = []
        seen: set[int] = set()
        for panel_id in [*device_panel_ids, *local_ids]:
            if panel_id in seen:
                continue
            seen.add(panel_id)
            history_ids.append(panel_id)
            if len(history_ids) >= _PANEL_USER_CAP:
                break
        history_events = await _history_events(api, history_ids, cutoff)

    events = [*device_events, *history_events]
    panel_ids = {int(event['panel_user_id']) for event in events if event.get('panel_user_id') is not None}
    rows = _rows_for_insert(events, await _local_user_ids(db, panel_ids))
    if rows:
        await _insert_events(db, rows)
    logger.info(
        'Request map synced panel IPs',
        stored=len(rows),
        connects=sum(1 for event in events if event['event_type'] == 'connect'),
        subscription_updates=sum(1 for event in events if event['event_type'] == 'subscription_update'),
        users=len(history_ids),
    )


async def sync_request_events_from_panel(*, days: int = 30) -> None:
    """Copy real RemnaWave IPs into the map table. Never raises.

    A failed panel call leaves existing rows in place so the map stays empty
    instead of returning an error.
    """
    global _last_panel_sync_at
    if time.monotonic() - _last_panel_sync_at < _PANEL_SYNC_COOLDOWN_SECONDS:
        return

    async with _PANEL_SYNC_LOCK:
        if time.monotonic() - _last_panel_sync_at < _PANEL_SYNC_COOLDOWN_SECONDS:
            return
        try:
            from app.services.remnawave_service import RemnaWaveService

            service = RemnaWaveService()
            if service.is_configured:
                async with AsyncSessionLocal() as db:
                    await _sync_panel_request_events(db, service, days=max(1, min(int(days), 365)))
                    await db.commit()
        except Exception as error:
            logger.warning('Request map panel sync failed', error=error)
        finally:
            _last_panel_sync_at = time.monotonic()
