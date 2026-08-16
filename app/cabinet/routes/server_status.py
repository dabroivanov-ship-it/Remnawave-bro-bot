"""User-facing server status route for cabinet.

Lists VPN nodes from RemnaWave with online/offline status and a best-effort
TCP latency ("ping") measured from the backend. Results are cached briefly so
that repeated requests don't hammer the panel or the nodes.
"""

import asyncio
import re
import time

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from app.cabinet.ip_utils import get_client_ip
from app.database.models import User
from app.utils.cache import RateLimitCache

from ..dependencies import get_current_cabinet_user


try:
    from app.services.remnawave_service import RemnaWaveService
except Exception:  # pragma: no cover - defensive import
    RemnaWaveService = None


logger = structlog.get_logger(__name__)

router = APIRouter(prefix='/server-status', tags=['Cabinet Server Status'])

_CACHE_TTL_SECONDS = 20.0
_PING_TIMEOUT_SECONDS = 1.5
_DEFAULT_PING_PORT = 443
_FLAG_RE = re.compile(r'^([\U0001F1E6-\U0001F1FF]{2})\s*(.*)$')

_cache: dict[str, object] = {'ts': 0.0, 'payload': None}


class ServerStatusItem(BaseModel):
    """Single server entry shown to the user."""

    id: str
    name: str
    country_code: str | None = None
    flag: str | None = None
    is_online: bool
    ping_ms: int | None = None


class ServerStatusResponse(BaseModel):
    """Server status payload."""

    enabled: bool
    updated_at: str | None = None
    total: int = 0
    online: int = 0
    servers: list[ServerStatusItem] = []


def _country_to_flag(country_code: str | None) -> str | None:
    if not country_code or len(country_code) != 2 or not country_code.isalpha():
        return None
    cc = country_code.upper()
    if cc == 'XX':
        return None
    return ''.join(chr(0x1F1E6 + (ord(ch) - ord('A'))) for ch in cc)


def _clean_name(name: str | None) -> tuple[str, str | None]:
    """Strip a leading flag emoji from the node name, returning (name, flag)."""
    raw = (name or '').strip()
    match = _FLAG_RE.match(raw)
    if match:
        flag, remainder = match.groups()
        return (remainder.strip() or raw), flag
    return raw or 'Server', None


async def _measure_ping(address: str | None) -> int | None:
    """Best-effort TCP connect latency to the node address."""
    if not address:
        return None

    host = address.strip()
    if not host:
        return None

    start = time.perf_counter()
    try:
        connect = asyncio.open_connection(host=host, port=_DEFAULT_PING_PORT)
        _reader, writer = await asyncio.wait_for(connect, timeout=_PING_TIMEOUT_SECONDS)
    except (TimeoutError, OSError, asyncio.TimeoutError):
        return None
    except Exception:  # pragma: no cover - defensive
        return None

    elapsed_ms = int(round((time.perf_counter() - start) * 1000))

    writer.close()
    try:
        await asyncio.wait_for(writer.wait_closed(), timeout=0.5)
    except Exception:
        pass

    return max(elapsed_ms, 0)


async def _build_payload() -> ServerStatusResponse:
    if RemnaWaveService is None:
        return ServerStatusResponse(enabled=False)

    service = RemnaWaveService()
    if not service.is_configured:
        return ServerStatusResponse(enabled=False)

    try:
        nodes = await service.get_all_nodes()
    except Exception as error:
        logger.warning('Failed to load nodes for server status', error=error)
        return ServerStatusResponse(enabled=True, total=0, online=0, servers=[])

    # Hide nodes that are intentionally disabled in the panel.
    visible_nodes = [n for n in nodes if not n.get('is_disabled')]

    ping_results = await asyncio.gather(
        *(_measure_ping(n.get('address')) for n in visible_nodes),
        return_exceptions=True,
    )

    servers: list[ServerStatusItem] = []
    for node, ping in zip(visible_nodes, ping_results, strict=False):
        is_online = bool(node.get('is_connected')) and not node.get('is_disabled')
        name, name_flag = _clean_name(node.get('name'))
        flag = name_flag or _country_to_flag(node.get('country_code'))
        ping_ms = ping if isinstance(ping, int) and is_online else None

        servers.append(
            ServerStatusItem(
                id=str(node.get('uuid') or name),
                name=name,
                country_code=(node.get('country_code') or None),
                flag=flag,
                is_online=is_online,
                ping_ms=ping_ms,
            )
        )

    # Online servers first, then alphabetical.
    servers.sort(key=lambda s: (0 if s.is_online else 1, s.name.lower()))

    online = sum(1 for s in servers if s.is_online)
    now_iso = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())

    return ServerStatusResponse(
        enabled=True,
        updated_at=now_iso,
        total=len(servers),
        online=online,
        servers=servers,
    )


async def _get_cached_payload() -> ServerStatusResponse:
    now = time.monotonic()
    cached_ts = float(_cache.get('ts') or 0.0)
    cached_payload = _cache.get('payload')

    if cached_payload is not None and (now - cached_ts) < _CACHE_TTL_SECONDS:
        return cached_payload  # type: ignore[return-value]

    payload = await _build_payload()
    _cache['ts'] = now
    _cache['payload'] = payload
    return payload


@router.get('/public', response_model=ServerStatusResponse)
async def get_public_server_status(
    raw_request: Request,
) -> ServerStatusResponse:
    """Public server status for the marketing homepage. No authentication required."""
    client_ip = get_client_ip(raw_request)
    if await RateLimitCache.is_ip_rate_limited(
        client_ip, 'server_status_public', limit=30, window=60, fail_closed=True
    ):
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail='Too many requests')

    return await _get_cached_payload()


@router.get('', response_model=ServerStatusResponse)
async def get_server_status(
    user: User = Depends(get_current_cabinet_user),
) -> ServerStatusResponse:
    """Get the list of servers with availability and best-effort ping."""
    if await RateLimitCache.is_rate_limited(
        user.id, 'server_status', limit=30, window=60, fail_closed=True
    ):
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail='Too many requests')

    return await _get_cached_payload()
