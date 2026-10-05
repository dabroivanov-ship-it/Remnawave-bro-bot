"""GeoLite2 map of where users connected or refreshed a subscription."""

import os
from datetime import UTC, datetime, timedelta

import structlog
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.models import RemnawaveRequestEvent, User

from ..dependencies import get_cabinet_db, require_permission


logger = structlog.get_logger(__name__)

router = APIRouter(prefix='/admin/remnawave', tags=['Cabinet Admin RemnaWave'])


class RequestMapPoint(BaseModel):
    latitude: float
    longitude: float
    country: str | None = None
    city: str | None = None
    event_type: str
    count: int


class RequestMapResponse(BaseModel):
    database_ready: bool
    total_events: int
    points: list[RequestMapPoint]


def _database_path() -> str:
    return (settings.GEOLITE2_DB_PATH or '').strip()


def _lookup_grouped(rows: list[tuple[str, str, int]]) -> list[RequestMapPoint]:
    path = _database_path()
    if not path or not os.path.isfile(path):
        return []
    import geoip2.database
    import geoip2.errors

    grouped: dict[tuple[float, float, str], RequestMapPoint] = {}
    with geoip2.database.Reader(path) as reader:
        for ip_address, event_type, count in rows:
            try:
                city = reader.city(ip_address)
            except geoip2.errors.AddressNotFoundError:
                continue
            except Exception as error:
                logger.warning('GeoLite lookup failed', error=error)
                continue
            if city.location.latitude is None or city.location.longitude is None:
                continue
            latitude = round(city.location.latitude, 2)
            longitude = round(city.location.longitude, 2)
            key = (latitude, longitude, event_type)
            current = grouped.get(key)
            if current is None:
                grouped[key] = RequestMapPoint(
                    latitude=latitude,
                    longitude=longitude,
                    country=city.country.name,
                    city=city.city.name,
                    event_type=event_type,
                    count=count,
                )
            else:
                current.count += count
    return list(grouped.values())


@router.get('/request-map', response_model=RequestMapResponse)
async def get_request_map(
    days: int = Query(default=30, ge=1, le=365),
    _admin: User = Depends(require_permission('remnawave:read')),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Points for the request map. Missing GeoLite2 file returns an empty map, not an error."""
    since = datetime.now(UTC) - timedelta(days=days)
    ready = bool(_database_path() and os.path.isfile(_database_path()))
    try:
        total_result = await db.execute(
            select(func.count(RemnawaveRequestEvent.id)).where(RemnawaveRequestEvent.occurred_at >= since)
        )
        total_events = total_result.scalar() or 0
        if not ready or total_events == 0:
            return RequestMapResponse(database_ready=ready, total_events=total_events, points=[])

        grouped = await db.execute(
            select(
                RemnawaveRequestEvent.ip_address,
                RemnawaveRequestEvent.event_type,
                func.count(RemnawaveRequestEvent.id),
            )
            .where(RemnawaveRequestEvent.occurred_at >= since)
            .group_by(RemnawaveRequestEvent.ip_address, RemnawaveRequestEvent.event_type)
            .limit(5000)
        )
        points = _lookup_grouped([(row[0], row[1], row[2]) for row in grouped])
    except ProgrammingError as error:
        logger.warning('Request map table is not ready', error=error)
        return RequestMapResponse(database_ready=ready, total_events=0, points=[])
    except Exception as error:
        logger.warning('Request map lookup failed', error=error)
        return RequestMapResponse(database_ready=False, total_events=0, points=[])
    return RequestMapResponse(database_ready=True, total_events=total_events, points=points)
