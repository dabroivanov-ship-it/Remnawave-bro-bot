"""Public endpoint for reporting cabinet frontend errors."""

from __future__ import annotations

import json

import structlog
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.crud.rbac import CabinetErrorLogCRUD
from app.database.models import User
from app.utils.cache import RateLimitCache

from ..dependencies import get_cabinet_db, get_optional_cabinet_user
from ..ip_utils import get_client_ip


logger = structlog.get_logger(__name__)

router = APIRouter(prefix='/client-errors', tags=['Cabinet Client Errors'])

ALLOWED_SOURCES = frozenset({'cabinet', 'admin'})
ALLOWED_LEVELS = frozenset({'error', 'warning'})


class ClientErrorReport(BaseModel):
    source: str = Field(default='cabinet', max_length=20)
    level: str = Field(default='error', max_length=20)
    message: str = Field(min_length=1, max_length=4000)
    stack_trace: str | None = Field(default=None, max_length=8000)
    page_url: str | None = Field(default=None, max_length=2000)
    component: str | None = Field(default=None, max_length=255)
    context: dict | None = None


class ClientErrorReportResponse(BaseModel):
    ok: bool = True


@router.post('', response_model=ClientErrorReportResponse)
async def report_client_error(
    payload: ClientErrorReport,
    request: Request,
    db: AsyncSession = Depends(get_cabinet_db),
    user: User | None = Depends(get_optional_cabinet_user),
):
    """Accept client-side error reports (cabinet or admin panel)."""
    client_ip = get_client_ip(request)
    if await RateLimitCache.is_ip_rate_limited(
        client_ip, 'client_errors', limit=30, window=60, fail_closed=True
    ):
        return ClientErrorReportResponse()

    source = payload.source if payload.source in ALLOWED_SOURCES else 'cabinet'
    if source == 'admin' and user is None:
        source = 'cabinet'
    level = payload.level if payload.level in ALLOWED_LEVELS else 'error'

    context_json = None
    if payload.context:
        try:
            context_json = json.dumps(payload.context, ensure_ascii=False)[:8000]
        except (TypeError, ValueError):
            context_json = None

    try:
        await CabinetErrorLogCRUD.create(
            db,
            source=source,
            level=level,
            message=payload.message,
            user_id=user.id if user else None,
            stack_trace=payload.stack_trace,
            page_url=payload.page_url,
            component=payload.component,
            ip_address=get_client_ip(request),
            user_agent=request.headers.get('user-agent'),
            context_json=context_json,
            commit=True,
        )
    except Exception:
        logger.warning('Failed to store client error report', exc_info=True)

    return ClientErrorReportResponse()
