"""Store and locate IPs from VPN connects and subscription updates."""

from datetime import UTC, datetime

import structlog
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import RemnawaveRequestEvent


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
