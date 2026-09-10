"""Admin IP control — unique Remnawave IPs and subscription-sharing alerts."""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.models import User
from app.services.ip_control_service import ip_control_scheduler, ip_control_service
from app.services.system_settings_service import ReadOnlySettingError, bot_configuration_service

from ..dependencies import get_cabinet_db, require_permission


logger = structlog.get_logger(__name__)

router = APIRouter(prefix='/admin/ip-control', tags=['Admin IP Control'])

_EDITABLE_KEYS = {
    'enabled': 'IP_CONTROL_ENABLED',
    'threshold': 'IP_CONTROL_UNIQUE_IP_THRESHOLD',
    'window_hours': 'IP_CONTROL_WINDOW_HOURS',
    'interval_minutes': 'IP_CONTROL_INTERVAL_MINUTES',
    'cooldown_hours': 'IP_CONTROL_COOLDOWN_HOURS',
}


class IpControlSettingsUpdate(BaseModel):
    enabled: bool | None = None
    threshold: int | None = Field(None, ge=2, le=100)
    window_hours: int | None = Field(None, ge=1, le=168)
    interval_minutes: int | None = Field(None, ge=5, le=1440)
    cooldown_hours: int | None = Field(None, ge=1, le=168)


class IpControlOffenderResponse(BaseModel):
    panel_user_id: int
    unique_ip_count: int
    address_count: int = 0
    device_count: int
    ips: list[str]
    telegram_id: int | None = None
    username: str | None = None
    full_name: str | None = None
    email: str | None = None
    local_user_id: int | None = None
    device_limit: int | None = None


class IpControlStatusResponse(BaseModel):
    enabled: bool
    threshold: int
    window_hours: int
    interval_minutes: int
    cooldown_hours: int
    scheduler_running: bool
    remnawave_configured: bool
    topic_id: int | None = None
    scanned_at: str | None = None
    users_checked: int = 0
    offender_count: int = 0
    alerts_sent: int = 0
    offenders: list[IpControlOffenderResponse] = []


def _status_payload(scan: dict[str, Any] | None = None) -> IpControlStatusResponse:
    snapshot = ip_control_service.get_settings_snapshot()
    last = scan if isinstance(scan, dict) else None
    offenders_raw = last.get('offenders') if last else []
    offenders: list[IpControlOffenderResponse] = []
    if isinstance(offenders_raw, list):
        for item in offenders_raw:
            if not isinstance(item, dict):
                continue
            try:
                if not item.get('address_count'):
                    item = {
                        **item,
                        'address_count': len(item.get('ips') or []) or item.get('unique_ip_count') or 0,
                    }
                offenders.append(IpControlOffenderResponse.model_validate(item))
            except Exception:
                continue
    return IpControlStatusResponse(
        enabled=snapshot['enabled'],
        threshold=snapshot['threshold'],
        window_hours=snapshot['window_hours'],
        interval_minutes=snapshot['interval_minutes'],
        cooldown_hours=snapshot['cooldown_hours'],
        scheduler_running=ip_control_scheduler.is_running(),
        remnawave_configured=ip_control_service.remnawave_service.is_configured,
        topic_id=settings.SUSPICIOUS_NOTIFICATIONS_TOPIC_ID,
        scanned_at=last.get('scanned_at') if last else None,
        users_checked=int(last.get('users_checked') or 0) if last else 0,
        offender_count=int(last.get('offender_count') or 0) if last else 0,
        alerts_sent=int(last.get('alerts_sent') or 0) if last else 0,
        offenders=offenders,
    )


@router.get('/status', response_model=IpControlStatusResponse)
async def get_ip_control_status(
    _admin: User = Depends(require_permission('users:read')),
):
    last = await ip_control_service.get_last_scan()
    return _status_payload(last)


@router.post('/scan', response_model=IpControlStatusResponse)
async def run_ip_control_scan(
    _admin: User = Depends(require_permission('users:read')),
):
    if not ip_control_service.remnawave_service.is_configured:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, 'Remnawave is not configured')
    result = await ip_control_service.run_scan(ip_control_scheduler.bot, notify=True)
    return _status_payload(
        {
            'scanned_at': result.scanned_at,
            'users_checked': result.users_checked,
            'offender_count': result.offender_count,
            'alerts_sent': result.alerts_sent,
            'offenders': [
                {
                    'panel_user_id': item.panel_user_id,
                    'unique_ip_count': item.unique_ip_count,
                    'address_count': item.address_count,
                    'device_count': item.device_count,
                    'ips': item.ips,
                    'telegram_id': item.telegram_id,
                    'username': item.username,
                    'full_name': item.full_name,
                    'email': item.email,
                    'local_user_id': item.local_user_id,
                    'device_limit': item.device_limit,
                }
                for item in result.offenders
            ],
        }
    )


@router.patch('/settings', response_model=IpControlStatusResponse)
async def update_ip_control_settings(
    payload: IpControlSettingsUpdate,
    admin: User = Depends(require_permission('settings:edit')),
    db: AsyncSession = Depends(get_cabinet_db),
):
    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        last = await ip_control_service.get_last_scan()
        return _status_payload(last)

    for field_name, value in updates.items():
        key = _EDITABLE_KEYS[field_name]
        try:
            await bot_configuration_service.set_value(db, key, value)
        except KeyError as error:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f'Unknown setting {key}') from error
        except ReadOnlySettingError as error:
            raise HTTPException(status.HTTP_403_FORBIDDEN, str(error)) from error

    await db.commit()
    logger.info('Admin updated IP control settings', telegram_id=admin.telegram_id, updates=updates)
    last = await ip_control_service.get_last_scan()
    return _status_payload(last)
