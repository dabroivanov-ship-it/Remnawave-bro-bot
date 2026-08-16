"""Admin login attempt log — view cabinet auth events for admin identities."""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime

import structlog
from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.crud.rbac import AdminLoginAttemptCRUD
from app.database.models import User

from ..dependencies import get_cabinet_db, require_permission


logger = structlog.get_logger(__name__)

router = APIRouter(prefix='/admin/rbac/login-attempts', tags=['Admin RBAC Login Attempts'])


class AdminLoginAttemptEntry(BaseModel):
    id: int
    user_id: int | None = None
    email: str | None = None
    telegram_id: int | None = None
    auth_method: str
    status: str
    failure_reason: str | None = None
    ip_address: str | None = None
    user_agent: str | None = None
    is_legacy_admin: bool
    is_brute_force: bool = False
    created_at: datetime | None = None
    user_first_name: str | None = None
    user_email: str | None = None


class AdminLoginAttemptListResponse(BaseModel):
    items: list[AdminLoginAttemptEntry]
    total: int
    limit: int
    offset: int


_CSV_COLUMNS = [
    'id',
    'user_id',
    'email',
    'telegram_id',
    'auth_method',
    'status',
    'failure_reason',
    'ip_address',
    'is_brute_force',
    'is_legacy_admin',
    'created_at',
    'user_agent',
]


def _sanitize_csv_cell(value: str) -> str:
    if value and value[0] in ('=', '+', '-', '@', '\t', '\r'):
        return f"'{value}"
    return value


def _attempts_to_csv(attempts) -> str:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(_CSV_COLUMNS)
    for row in attempts:
        writer.writerow(
            [
                row.id,
                row.user_id or '',
                row.email or '',
                row.telegram_id or '',
                row.auth_method,
                row.status,
                row.failure_reason or '',
                row.ip_address or '',
                row.is_brute_force,
                row.is_legacy_admin,
                row.created_at.isoformat() if row.created_at else '',
                _sanitize_csv_cell((row.user_agent or '')[:200]),
            ]
        )
    return output.getvalue()


def _serialize(entry, user: User | None = None) -> AdminLoginAttemptEntry:
    return AdminLoginAttemptEntry(
        id=entry.id,
        user_id=entry.user_id,
        email=entry.email,
        telegram_id=entry.telegram_id,
        auth_method=entry.auth_method,
        status=entry.status,
        failure_reason=entry.failure_reason,
        ip_address=entry.ip_address,
        user_agent=entry.user_agent,
        is_legacy_admin=bool(entry.is_legacy_admin),
        is_brute_force=bool(entry.is_brute_force),
        created_at=entry.created_at,
        user_first_name=user.first_name if user else None,
        user_email=user.email if user else None,
    )


@router.get('', response_model=AdminLoginAttemptListResponse)
async def list_admin_login_attempts(
    admin: User = Depends(require_permission('audit_log:read')),
    db: AsyncSession = Depends(get_cabinet_db),
    user_id: int | None = Query(default=None),
    status: str | None = Query(default=None),
    auth_method: str | None = Query(default=None),
    email: str | None = Query(default=None),
    ip_address: str | None = Query(default=None),
    is_brute_force: bool | None = Query(default=None),
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    """List admin-related cabinet login attempts."""
    attempts, total = await AdminLoginAttemptCRUD.get_attempts(
        db,
        user_id=user_id,
        status=status,
        auth_method=auth_method,
        email=email,
        ip_address=ip_address,
        is_brute_force=is_brute_force,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
        offset=offset,
        load_user=True,
    )
    items = [_serialize(entry, entry.user) for entry in attempts]
    return AdminLoginAttemptListResponse(items=items, total=total, limit=limit, offset=offset)


@router.get('/export')
async def export_admin_login_attempts(
    admin: User = Depends(require_permission('audit_log:export')),
    db: AsyncSession = Depends(get_cabinet_db),
    user_id: int | None = Query(default=None),
    status: str | None = Query(default=None),
    auth_method: str | None = Query(default=None),
    email: str | None = Query(default=None),
    ip_address: str | None = Query(default=None),
    is_brute_force: bool | None = Query(default=None),
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
):
    """Export admin login attempts as CSV."""
    attempts, _total = await AdminLoginAttemptCRUD.get_attempts(
        db,
        user_id=user_id,
        status=status,
        auth_method=auth_method,
        email=email,
        ip_address=ip_address,
        is_brute_force=is_brute_force,
        date_from=date_from,
        date_to=date_to,
        limit=10_000,
        offset=0,
        load_user=False,
    )
    csv_content = _attempts_to_csv(attempts)
    timestamp = datetime.now(UTC).strftime('%Y%m%d_%H%M%S')
    filename = f'admin_login_attempts_{timestamp}.csv'
    logger.info('Admin exported login attempts', admin_id=admin.id, count=len(attempts))
    return StreamingResponse(
        iter([csv_content]),
        media_type='text/csv',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'},
    )
