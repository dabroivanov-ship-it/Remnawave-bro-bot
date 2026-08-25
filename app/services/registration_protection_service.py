"""Re-registration protection: detect accounts sharing IP + software and alert a chat."""

from __future__ import annotations

import html

import structlog
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.models import User
from app.utils.timezone import format_local_datetime


logger = structlog.get_logger(__name__)

_MAX_MATCHES = 10


def _user_identifier(user: User) -> str:
    """Human-readable identifier for a user (telegram id / email / internal id)."""
    telegram_id = getattr(user, 'telegram_id', None)
    if telegram_id:
        username = getattr(user, 'username', None)
        if username:
            return f'@{html.escape(username)} (<code>{telegram_id}</code>)'
        return f'<code>{telegram_id}</code>'
    email = getattr(user, 'email', None)
    if email:
        return f'📧 {html.escape(email)}'
    return f'User#{getattr(user, "id", "?")}'


async def find_duplicate_registrations(
    db: AsyncSession,
    *,
    user: User,
    ip: str | None,
    user_agent: str | None,
) -> list[User]:
    """Return other accounts that registered with the same IP AND software (user-agent)."""
    if not ip or not user_agent:
        return []

    result = await db.execute(
        select(User)
        .where(
            and_(
                User.id != user.id,
                User.registration_ip == ip,
                User.registration_user_agent == user_agent,
            )
        )
        .order_by(User.created_at.desc())
        .limit(_MAX_MATCHES)
    )
    return list(result.scalars().all())


async def find_possible_duplicate_registrations(
    db: AsyncSession,
    *,
    user: User,
    limit: int = 50,
) -> list[tuple[User, str]]:
    """Find possible re-registrations for admin profile.

    Returns (matched_user, match_by) where match_by is:
    - ``ip_and_software`` — same registration IP and user-agent
    - ``ip_only`` — same registration IP only
    """
    ip = getattr(user, 'registration_ip', None)
    user_agent = getattr(user, 'registration_user_agent', None)
    if not ip:
        return []

    matches: list[tuple[User, str]] = []
    seen_ids: set[int] = set()

    if user_agent:
        exact = await db.execute(
            select(User)
            .where(
                and_(
                    User.id != user.id,
                    User.registration_ip == ip,
                    User.registration_user_agent == user_agent,
                )
            )
            .order_by(User.created_at.desc())
            .limit(limit)
        )
        for match in exact.scalars().all():
            seen_ids.add(match.id)
            matches.append((match, 'ip_and_software'))

    remaining = max(limit - len(matches), 0)
    if remaining:
        conditions = [
            User.id != user.id,
            User.registration_ip == ip,
        ]
        if seen_ids:
            conditions.append(User.id.notin_(seen_ids))

        ip_only = await db.execute(
            select(User).where(and_(*conditions)).order_by(User.created_at.desc()).limit(remaining)
        )
        for match in ip_only.scalars().all():
            matches.append((match, 'ip_only'))

    return matches


def _build_alert_text(user: User, ip: str | None, user_agent: str | None, matches: list[User]) -> str:
    ua_display = html.escape((user_agent or '')[:300]) or '—'
    ip_display = html.escape(ip or '—')

    lines = [
        '🛡 <b>Возможная повторная регистрация</b>',
        '',
        f'🆕 <b>Новый аккаунт:</b> {_user_identifier(user)}',
        f'🌐 <b>IP:</b> <code>{ip_display}</code>',
        f'💻 <b>Софт:</b> <code>{ua_display}</code>',
        '',
        f'⚠️ <b>Совпадения по IP + устройству ({len(matches)}):</b>',
    ]

    for match in matches:
        created = format_local_datetime(match.created_at, '%d.%m.%Y %H:%M') if match.created_at else '—'
        lines.append(f'• {_user_identifier(match)} — {created}')

    return '\n'.join(lines)


async def check_and_notify_duplicate_registration(
    db: AsyncSession,
    *,
    user: User,
    ip: str | None,
    user_agent: str | None,
) -> None:
    """Best-effort duplicate-registration check + chat notification. Never raises."""
    try:
        if not settings.is_registration_protection_enabled():
            return

        chat_id = settings.get_registration_protection_chat_id()
        if not chat_id:
            return

        matches = await find_duplicate_registrations(db, user=user, ip=ip, user_agent=user_agent)
        if not matches:
            return

        text = _build_alert_text(user, ip, user_agent, matches)
        topic_id = settings.REGISTRATION_PROTECTION_TOPIC_ID

        from app.bot_factory import create_bot

        send_kwargs: dict = {'chat_id': chat_id, 'text': text, 'disable_web_page_preview': True}
        if topic_id:
            send_kwargs['message_thread_id'] = topic_id

        async with create_bot() as bot:
            await bot.send_message(**send_kwargs)

        logger.info(
            'Re-registration alert sent',
            user_id=user.id,
            ip=ip,
            matches=len(matches),
        )
    except Exception:
        logger.warning('Failed to run re-registration protection check', exc_info=True)
