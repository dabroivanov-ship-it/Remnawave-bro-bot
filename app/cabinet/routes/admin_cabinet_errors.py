"""Admin view of cabinet / panel client error logs."""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime

import structlog
from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.crud.rbac import CabinetErrorLogCRUD
from app.database.models import User

from ..dependencies import get_cabinet_db, require_permission


logger = structlog.get_logger(__name__)

router = APIRouter(prefix='/admin/rbac/client-errors', tags=['Admin Cabinet Errors'])


class CabinetErrorLogEntry(BaseModel):
    id: int
    user_id: int | None = None
    source: str
    level: str
    message: str
    stack_trace: str | None = None
    page_url: str | None = None
    component: str | None = None
    ip_address: str | None = None
    user_agent: str | None = None
    created_at: datetime | None = None
    user_first_name: str | None = None
    user_email: str | None = None


class CabinetErrorLogListResponse(BaseModel):
    items: list[CabinetErrorLogEntry]
    total: int
    limit: int
    offset: int


class CabinetErrorLogClearResponse(BaseModel):
    deleted: int


_CSV_COLUMNS = [
    'id',
    'user_id',
    'source',
    'level',
    'message',
    'page_url',
    'component',
    'ip_address',
    'created_at',
]


def _sanitize_csv_cell(value: str) -> str:
    if value and value[0] in ('=', '+', '-', '@', '\t', '\r'):
        return f"'{value}"
    return value


def _serialize(entry, user: User | None = None) -> CabinetErrorLogEntry:
    return CabinetErrorLogEntry(
        id=entry.id,
        user_id=entry.user_id,
        source=entry.source,
        level=entry.level,
        message=entry.message,
        stack_trace=entry.stack_trace,
        page_url=entry.page_url,
        component=entry.component,
        ip_address=entry.ip_address,
        user_agent=entry.user_agent,
        created_at=entry.created_at,
        user_first_name=user.first_name if user else None,
        user_email=user.email if user else None,
    )


@router.get('', response_model=CabinetErrorLogListResponse)
async def list_cabinet_errors(
    admin: User = Depends(require_permission('audit_log:read')),
    db: AsyncSession = Depends(get_cabinet_db),
    source: str | None = Query(default=None),
    level: str | None = Query(default=None),
    user_id: int | None = Query(default=None),
    search: str | None = Query(default=None),
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    logs, total = await CabinetErrorLogCRUD.get_logs(
        db,
        source=source,
        level=level,
        user_id=user_id,
        search=search,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
        offset=offset,
        load_user=True,
    )
    items = [_serialize(entry, entry.user) for entry in logs]
    return CabinetErrorLogListResponse(items=items, total=total, limit=limit, offset=offset)


@router.delete('', response_model=CabinetErrorLogClearResponse)
async def clear_cabinet_errors(
    admin: User = Depends(require_permission('audit_log:delete')),
    db: AsyncSession = Depends(get_cabinet_db),
    source: str | None = Query(default=None),
    level: str | None = Query(default=None),
    user_id: int | None = Query(default=None),
    search: str | None = Query(default=None),
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
):
    deleted = await CabinetErrorLogCRUD.delete_logs(
        db,
        source=source,
        level=level,
        user_id=user_id,
        search=search,
        date_from=date_from,
        date_to=date_to,
    )
    await db.commit()
    logger.info(
        'Admin cleared cabinet error logs',
        admin_id=admin.id,
        deleted=deleted,
        source=source,
        level=level,
        user_id=user_id,
    )
    return CabinetErrorLogClearResponse(deleted=deleted)


@router.get('/export')
async def export_cabinet_errors(
    admin: User = Depends(require_permission('audit_log:export')),
    db: AsyncSession = Depends(get_cabinet_db),
    source: str | None = Query(default=None),
    level: str | None = Query(default=None),
    user_id: int | None = Query(default=None),
    search: str | None = Query(default=None),
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
):
    logs, _total = await CabinetErrorLogCRUD.get_logs(
        db,
        source=source,
        level=level,
        user_id=user_id,
        search=search,
        date_from=date_from,
        date_to=date_to,
        limit=10_000,
        offset=0,
        load_user=False,
    )
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(_CSV_COLUMNS)
    for row in logs:
        writer.writerow(
            [
                row.id,
                row.user_id or '',
                row.source,
                row.level,
                _sanitize_csv_cell((row.message or '')[:500]),
                row.page_url or '',
                row.component or '',
                row.ip_address or '',
                row.created_at.isoformat() if row.created_at else '',
            ]
        )
    timestamp = datetime.now(UTC).strftime('%Y%m%d_%H%M%S')
    filename = f'cabinet_errors_{timestamp}.csv'
    logger.info('Admin exported cabinet errors', admin_id=admin.id, count=len(logs))
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type='text/csv',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'},
    )
