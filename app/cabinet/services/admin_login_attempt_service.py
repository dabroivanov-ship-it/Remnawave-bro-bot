"""Record and query cabinet login attempts for admin identities."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import structlog
from fastapi import Request
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.crud.rbac import UserRoleCRUD
from app.database.models import AdminLoginAttempt, User

from ..ip_utils import get_client_ip


if TYPE_CHECKING:
    pass

logger = structlog.get_logger(__name__)

AUTH_METHODS = frozenset(
    {
        'email',
        'telegram',
        'telegram_widget',
        'telegram_oidc',
        'oauth',
        'deeplink',
        'auto_login',
    }
)

BRUTE_FORCE_WINDOW = timedelta(minutes=15)
BRUTE_FORCE_THRESHOLD = 5
PASSWORD_FAILURE_REASONS = frozenset(
    {
        'invalid_credentials',
        'invalid_password',
        'password_not_configured',
    }
)


def identity_targets_legacy_admin(*, telegram_id: int | None = None, email: str | None = None) -> bool:
    return settings.is_admin(telegram_id=telegram_id, email=email)


async def user_has_admin_access(db: AsyncSession, user: User) -> bool:
    if identity_targets_legacy_admin(telegram_id=user.telegram_id, email=user.email):
        return True
    _permissions, _role_names, max_level = await UserRoleCRUD.get_user_permissions(db, user.id)
    return max_level > 0


async def should_log_admin_login_attempt(
    db: AsyncSession,
    *,
    user: User | None = None,
    email: str | None = None,
    telegram_id: int | None = None,
) -> bool:
    if identity_targets_legacy_admin(telegram_id=telegram_id, email=email):
        return True
    if user is not None:
        return await user_has_admin_access(db, user)
    return False


async def _detect_brute_force(
    db: AsyncSession,
    *,
    ip_address: str | None,
    failure_reason: str | None,
    status: str,
) -> bool:
    if status != 'failed' or not ip_address:
        return False
    if failure_reason not in PASSWORD_FAILURE_REASONS:
        return False

    since = datetime.now(UTC) - BRUTE_FORCE_WINDOW
    count_result = await db.execute(
        select(func.count(AdminLoginAttempt.id)).where(
            and_(
                AdminLoginAttempt.ip_address == ip_address,
                AdminLoginAttempt.status == 'failed',
                AdminLoginAttempt.failure_reason.in_(PASSWORD_FAILURE_REASONS),
                AdminLoginAttempt.created_at >= since,
            )
        )
    )
    prior_failures = count_result.scalar() or 0
    return prior_failures >= BRUTE_FORCE_THRESHOLD - 1


async def record_admin_login_attempt(
    db: AsyncSession,
    *,
    request: Request | None,
    auth_method: str,
    status: str,
    user: User | None = None,
    email: str | None = None,
    telegram_id: int | None = None,
    failure_reason: str | None = None,
    commit: bool = False,
) -> None:
    """Persist an admin-related login attempt. Never raises — logging must not break auth."""
    try:
        if auth_method not in AUTH_METHODS:
            auth_method = 'email'

        if not await should_log_admin_login_attempt(
            db,
            user=user,
            email=email,
            telegram_id=telegram_id,
        ):
            return

        ip_address = get_client_ip(request) if request else None
        user_agent = request.headers.get('user-agent') if request else None
        is_brute_force = await _detect_brute_force(
            db,
            ip_address=ip_address,
            failure_reason=failure_reason,
            status=status,
        )

        entry = AdminLoginAttempt(
            user_id=user.id if user else None,
            email=(email or (user.email if user else None)),
            telegram_id=telegram_id if telegram_id is not None else user.telegram_id if user else None,
            auth_method=auth_method,
            status=status,
            failure_reason=failure_reason,
            ip_address=ip_address,
            user_agent=user_agent,
            is_legacy_admin=identity_targets_legacy_admin(
                telegram_id=telegram_id if telegram_id is not None else (user.telegram_id if user else None),
                email=email or (user.email if user else None),
            ),
            is_brute_force=is_brute_force,
        )
        db.add(entry)
        await db.flush()
        if commit:
            await db.commit()
    except Exception:
        logger.warning('Failed to record admin login attempt', exc_info=True)
