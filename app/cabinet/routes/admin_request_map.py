"""GeoLite2 map of where users connected or refreshed a subscription.

A local GeoLite2 file is used when it exists. Nothing is sent off the server in
that case. If the file is missing, public IPs are resolved one at a time with
ipwho.is (no API key). Successful online results stay in memory for the life
of the process.
"""

import ipaddress
import os
import time
from datetime import UTC, datetime, timedelta
from typing import Literal
from urllib.parse import quote

import structlog
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.models import RemnawaveRequestEvent, User
from app.services.request_map_service import sync_request_events_from_panel

from ..dependencies import get_cabinet_db, require_permission


logger = structlog.get_logger(__name__)

router = APIRouter(prefix='/admin/remnawave', tags=['Cabinet Admin RemnaWave'])

_ONLINE_IP_LIMIT = 40
_ONLINE_TIMEOUT_SECONDS = 3.0
_ONLINE_BUDGET_SECONDS = 12.0
_IPWHO_URL = 'https://ipwho.is/'
LookupSource = Literal['geolite', 'online', 'none']

# ip -> (latitude, longitude, country, city). Successful lookups only.
_online_cache: dict[str, tuple[float, float, str | None, str | None]] = {}


class RequestMapPoint(BaseModel):
    latitude: float
    longitude: float
    country: str | None = None
    city: str | None = None
    event_type: str
    count: int


class RequestMapResponse(BaseModel):
    database_ready: bool
    lookup_source: LookupSource
    total_events: int
    points: list[RequestMapPoint]


def _database_path() -> str:
    return (settings.GEOLITE2_DB_PATH or '').strip()


def _geolite_ready() -> bool:
    path = _database_path()
    return bool(path and os.path.isfile(path))


def _is_public_ip(value: str) -> bool:
    try:
        ip = ipaddress.ip_address((value or '').strip())
    except ValueError:
        return False
    return bool(ip.is_global)


def _accumulate(
    grouped: dict[tuple[float, float, str], RequestMapPoint],
    *,
    latitude: float,
    longitude: float,
    country: str | None,
    city: str | None,
    event_type: str,
    count: int,
) -> None:
    key = (latitude, longitude, event_type)
    current = grouped.get(key)
    if current is None:
        grouped[key] = RequestMapPoint(
            latitude=latitude,
            longitude=longitude,
            country=country,
            city=city,
            event_type=event_type,
            count=count,
        )
        return
    current.count += count


def _lookup_grouped(rows: list[tuple[str, str, int]]) -> list[RequestMapPoint]:
    """Local GeoLite2 only. Never calls the network."""
    path = _database_path()
    if not path or not os.path.isfile(path):
        return []
    import geoip2.database
    import geoip2.errors

    grouped: dict[tuple[float, float, str], RequestMapPoint] = {}
    with geoip2.database.Reader(path) as reader:
        for ip_address, event_type, count in rows:
            if not _is_public_ip(ip_address):
                continue
            try:
                city = reader.city(ip_address)
            except geoip2.errors.AddressNotFoundError:
                continue
            except Exception as error:
                logger.warning('GeoLite lookup failed', error=error)
                continue
            if city.location.latitude is None or city.location.longitude is None:
                continue
            _accumulate(
                grouped,
                latitude=round(city.location.latitude, 2),
                longitude=round(city.location.longitude, 2),
                country=city.country.name,
                city=city.city.name,
                event_type=event_type,
                count=count,
            )
    return list(grouped.values())


def _cache_online_result(ip_address: str, payload: object) -> bool:
    if not isinstance(payload, dict) or payload.get('success') is not True:
        return False
    raw_latitude = payload.get('latitude')
    raw_longitude = payload.get('longitude')
    if raw_latitude is None or raw_longitude is None:
        return False
    try:
        latitude = round(float(raw_latitude), 2)
        longitude = round(float(raw_longitude), 2)
    except (TypeError, ValueError):
        return False
    country = payload.get('country')
    city = payload.get('city')
    _online_cache[ip_address] = (
        latitude,
        longitude,
        country if isinstance(country, str) and country else None,
        city if isinstance(city, str) and city else None,
    )
    return True


async def _lookup_online_grouped(rows: list[tuple[str, str, int]]) -> tuple[list[RequestMapPoint], bool]:
    """Resolve at most 40 uncached public IPs via ipwho.is.

    Returns (points, lookup_failed). lookup_failed is set when a lookup was
    needed and every attempt failed, so the page can explain an empty map.
    Failures are not cached. A timeout or HTTP error skips that IP.
    """
    public_rows = [(ip, event_type, count) for ip, event_type, count in rows if _is_public_ip(ip)]
    pending: list[str] = []
    seen: set[str] = set()
    for ip_address, _, _ in public_rows:
        if ip_address in _online_cache or ip_address in seen:
            continue
        seen.add(ip_address)
        pending.append(ip_address)
        if len(pending) >= _ONLINE_IP_LIMIT:
            break

    attempted = 0
    failed_lookups = 0
    client_failed = False
    if pending:
        try:
            import httpx
        except ImportError:
            logger.warning('Online IP lookup unavailable')
            client_failed = True
        else:
            deadline = time.monotonic() + _ONLINE_BUDGET_SECONDS
            try:
                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(_ONLINE_TIMEOUT_SECONDS),
                    follow_redirects=False,
                    headers={'User-Agent': 'cabinet-request-map'},
                ) as client:
                    for ip_address in pending:
                        if time.monotonic() >= deadline:
                            break
                        attempted += 1
                        try:
                            response = await client.get(f'{_IPWHO_URL}{quote(ip_address, safe=":.")}')
                            response.raise_for_status()
                            _cache_online_result(ip_address, response.json())
                        except Exception:
                            failed_lookups += 1
            except Exception:
                logger.warning('Online IP lookup client failed')
                client_failed = True
        if failed_lookups:
            logger.warning('Online IP lookup skipped some addresses', failed=failed_lookups, attempted=attempted)

    grouped: dict[tuple[float, float, str], RequestMapPoint] = {}
    placed = 0
    for ip_address, event_type, count in public_rows:
        cached = _online_cache.get(ip_address)
        if cached is None:
            continue
        latitude, longitude, country, city = cached
        _accumulate(
            grouped,
            latitude=latitude,
            longitude=longitude,
            country=country,
            city=city,
            event_type=event_type,
            count=count,
        )
        placed += 1

    lookup_failed = placed == 0 and (attempted > 0 or client_failed)
    return list(grouped.values()), lookup_failed


def _map_response(
    *,
    database_ready: bool,
    lookup_source: LookupSource,
    total_events: int,
    points: list[RequestMapPoint],
) -> RequestMapResponse:
    return RequestMapResponse(
        database_ready=database_ready,
        lookup_source=lookup_source,
        total_events=total_events,
        points=points,
    )


@router.get('/request-map', response_model=RequestMapResponse)
async def get_request_map(
    days: int = Query(default=30, ge=1, le=365),
    _admin: User = Depends(require_permission('remnawave:read')),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Points for the request map. A missing GeoLite2 file uses ipwho.is instead of failing."""
    since = datetime.now(UTC) - timedelta(days=days)
    geolite_ready = _geolite_ready()
    try:
        await sync_request_events_from_panel(days=days)
    except Exception as error:
        logger.warning('Request map panel sync failed', error=error)
    try:
        total_result = await db.execute(
            select(func.count(RemnawaveRequestEvent.id)).where(RemnawaveRequestEvent.occurred_at >= since)
        )
        total_events = int(total_result.scalar() or 0)
        if total_events == 0:
            source: LookupSource = 'geolite' if geolite_ready else 'online'
            return _map_response(database_ready=True, lookup_source=source, total_events=0, points=[])

        count_expr = func.count(RemnawaveRequestEvent.id)
        grouped = await db.execute(
            select(
                RemnawaveRequestEvent.ip_address,
                RemnawaveRequestEvent.event_type,
                count_expr,
            )
            .where(RemnawaveRequestEvent.occurred_at >= since)
            .group_by(RemnawaveRequestEvent.ip_address, RemnawaveRequestEvent.event_type)
            .order_by(count_expr.desc())
            .limit(5000)
        )
        rows = [(str(row[0]), str(row[1]), int(row[2])) for row in grouped]
        if geolite_ready:
            return _map_response(
                database_ready=True,
                lookup_source='geolite',
                total_events=total_events,
                points=_lookup_grouped(rows),
            )

        points, lookup_failed = await _lookup_online_grouped(rows)
        if lookup_failed:
            return _map_response(
                database_ready=False,
                lookup_source='none',
                total_events=total_events,
                points=[],
            )
        return _map_response(
            database_ready=True,
            lookup_source='online',
            total_events=total_events,
            points=points,
        )
    except ProgrammingError as error:
        logger.warning('Request map table is not ready', error=error)
        return _map_response(database_ready=False, lookup_source='none', total_events=0, points=[])
    except Exception as error:
        logger.warning('Request map lookup failed', error=error)
        return _map_response(database_ready=False, lookup_source='none', total_events=0, points=[])
