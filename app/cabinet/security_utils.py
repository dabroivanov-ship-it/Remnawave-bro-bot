"""Shared security helpers for the cabinet API."""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.crud.rbac import UserRoleCRUD
from app.database.models import Ticket, TicketMessage, User
from app.services.permission_service import PermissionService, _is_legacy_admin


_SENSITIVE_BODY_KEYS = frozenset(
    {
        'password',
        'current_password',
        'new_password',
        'old_password',
        'confirm_password',
        'token',
        'refresh_token',
        'access_token',
        'secret',
        'secret_key',
        'api_key',
        'authorization',
    }
)

_MEDIA_FILE_ID_RE = re.compile(r'^[A-Za-z0-9_-]{10,256}$')


def redact_request_body(body: Any) -> Any:
    """Remove sensitive fields before persisting request bodies in audit logs."""
    if isinstance(body, dict):
        redacted: dict[str, Any] = {}
        for key, value in body.items():
            key_lower = str(key).lower()
            if key_lower in _SENSITIVE_BODY_KEYS:
                redacted[key] = '[REDACTED]'
            elif isinstance(value, dict):
                redacted[key] = redact_request_body(value)
            elif isinstance(value, list):
                redacted[key] = [
                    redact_request_body(item) if isinstance(item, dict) else item for item in value
                ]
            else:
                redacted[key] = value
        return redacted
    return body


def _file_id_in_media_items(media_items: Any, file_id: str) -> bool:
    if not isinstance(media_items, list):
        return False
    for item in media_items:
        if isinstance(item, dict) and item.get('file_id') == file_id:
            return True
        if isinstance(item, str) and item == file_id:
            return True
    return False


async def user_can_access_media_file(db: AsyncSession, user: User, file_id: str) -> bool:
    """True if user owns a ticket containing this Telegram file_id or may read all tickets."""
    if not file_id or not _MEDIA_FILE_ID_RE.fullmatch(file_id):
        return False

    if _is_legacy_admin(user):
        allowed, _reason = await PermissionService.check_permission(db, user, 'tickets:read')
        if allowed:
            return True

    permissions, _roles, _level = await UserRoleCRUD.get_user_permissions(db, user.id)
    if any(perm.startswith('tickets:') for perm in permissions):
        allowed, _reason = await PermissionService.check_permission(db, user, 'tickets:read')
        if allowed:
            return True

    stmt = (
        select(TicketMessage.id)
        .join(Ticket, TicketMessage.ticket_id == Ticket.id)
        .where(
            Ticket.user_id == user.id,
            TicketMessage.media_file_id == file_id,
        )
        .limit(1)
    )
    result = await db.execute(stmt)
    if result.scalar_one_or_none() is not None:
        return True

    # Fallback: scan messages with media_items JSON (JSONB contains is shape-sensitive)
    stmt2 = (
        select(TicketMessage.media_items)
        .join(Ticket, TicketMessage.ticket_id == Ticket.id)
        .where(Ticket.user_id == user.id, TicketMessage.has_media.is_(True))
        .limit(200)
    )
    rows = await db.execute(stmt2)
    for (media_items,) in rows.all():
        if _file_id_in_media_items(media_items, file_id):
            return True

    return False


def validate_cabinet_jwt_configuration() -> None:
    """Fail fast when cabinet is enabled without a dedicated JWT secret."""
    if not settings.is_cabinet_enabled():
        return
    if settings.DEBUG:
        return
    if settings.CABINET_JWT_SECRET:
        return
    raise RuntimeError(
        'CABINET_JWT_SECRET must be set when CABINET is enabled in production (DEBUG=false). '
        'Generate one with: python -c "import secrets; print(secrets.token_urlsafe(64))"'
    )
