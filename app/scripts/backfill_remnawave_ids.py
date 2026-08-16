"""Backfill Remnawave numeric ids after panel upgrade to 3.x.

Resolves panel users by shortUuid / username and writes remnawave_id
onto local users and subscriptions.

Usage (inside bot container / venv):

    python -m app.scripts.backfill_remnawave_ids
    python -m app.scripts.backfill_remnawave_ids --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import sys

import structlog
from sqlalchemy import or_, select
from sqlalchemy.orm import selectinload


logger = structlog.get_logger(__name__)


async def backfill_remnawave_ids(*, dry_run: bool = False) -> dict[str, int]:
    from app.config import settings
    from app.database.database import AsyncSessionLocal
    from app.database.models import Subscription, User
    from app.external.remnawave_api import apply_panel_identity, resolve_panel_user
    from app.services.remnawave_service import RemnaWaveService

    stats = {
        'subscriptions_checked': 0,
        'subscriptions_updated': 0,
        'users_updated': 0,
        'orphans': 0,
        'errors': 0,
    }

    service = RemnaWaveService()
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Subscription)
            .options(selectinload(Subscription.user))
            .where(
                or_(
                    Subscription.remnawave_short_uuid.isnot(None),
                    Subscription.remnawave_uuid.isnot(None),
                    Subscription.remnawave_id.isnot(None),
                )
            )
            .order_by(Subscription.id.asc())
        )
        subscriptions = list(result.scalars().all())

        # Avoid unique remnawave_id collisions across users during backfill.
        seen_user_panel_ids: set[int] = set()

        async with service.get_api_client() as api:
            for subscription in subscriptions:
                stats['subscriptions_checked'] += 1
                user = subscription.user
                try:
                    username = None
                    if user is not None:
                        username = settings.format_remnawave_username(
                            full_name=user.full_name or '',
                            username=user.username,
                            telegram_id=user.telegram_id,
                            email=user.email,
                            user_id=user.id,
                        )
                        if settings.is_multi_tariff_enabled() and subscription.remnawave_short_id:
                            username = f'{username}_{subscription.remnawave_short_id}'

                    panel_user = await resolve_panel_user(
                        api,
                        remnawave_id=subscription.remnawave_id
                        or getattr(user, 'remnawave_id', None)
                        or subscription.remnawave_uuid,
                        short_uuid=subscription.remnawave_short_uuid,
                        username=username,
                    )
                    if not panel_user:
                        stats['orphans'] += 1
                        logger.warning(
                            'No panel user for subscription',
                            subscription_id=subscription.id,
                            user_id=getattr(user, 'id', None),
                            short_uuid=subscription.remnawave_short_uuid,
                            remnawave_uuid=subscription.remnawave_uuid,
                        )
                        continue

                    changed = subscription.remnawave_id != panel_user.id or (
                        panel_user.short_uuid
                        and subscription.remnawave_short_uuid != panel_user.short_uuid
                    )
                    # In multi-tariff mode identity lives on the subscription only.
                    update_user = user is not None and not settings.is_multi_tariff_enabled()
                    if update_user and getattr(user, 'remnawave_id', None) == panel_user.id:
                        update_user = False
                    if (
                        update_user
                        and panel_user.id in seen_user_panel_ids
                        and getattr(user, 'remnawave_id', None) != panel_user.id
                    ):
                        update_user = False
                        logger.warning(
                            'Skip user remnawave_id collision',
                            user_id=user.id,
                            panel_id=panel_user.id,
                            subscription_id=subscription.id,
                        )
                    user_changed = bool(
                        update_user and getattr(user, 'remnawave_id', None) != panel_user.id
                    )

                    if not changed and not user_changed:
                        continue

                    if dry_run:
                        logger.info(
                            'DRY-RUN would update',
                            subscription_id=subscription.id,
                            panel_id=panel_user.id,
                            short_uuid=panel_user.short_uuid,
                        )
                    else:
                        apply_panel_identity(
                            user=user if update_user else None,
                            subscription=subscription,
                            panel_user=panel_user,
                        )
                        if update_user:
                            seen_user_panel_ids.add(panel_user.id)

                    stats['subscriptions_updated'] += 1
                    if user_changed:
                        stats['users_updated'] += 1
                except Exception as exc:
                    stats['errors'] += 1
                    logger.error(
                        'Backfill failed for subscription',
                        subscription_id=subscription.id,
                        error=exc,
                        exc_info=True,
                    )

            # Users without remnawave_id yet (legacy uuid / username lookup)
            users_result = await db.execute(
                select(User).where(
                    or_(User.remnawave_uuid.isnot(None), User.remnawave_id.isnot(None)),
                )
            )
            for user in users_result.scalars().all():
                if getattr(user, 'remnawave_id', None):
                    seen_user_panel_ids.add(user.remnawave_id)
                    continue
                try:
                    username = settings.format_remnawave_username(
                        full_name=user.full_name or '',
                        username=user.username,
                        telegram_id=user.telegram_id,
                        email=user.email,
                        user_id=user.id,
                    )
                    panel_user = await resolve_panel_user(
                        api,
                        remnawave_id=user.remnawave_id or user.remnawave_uuid,
                        username=username,
                    )
                    if not panel_user:
                        stats['orphans'] += 1
                        continue
                    if panel_user.id in seen_user_panel_ids:
                        stats['orphans'] += 1
                        logger.warning(
                            'Skip user remnawave_id collision',
                            user_id=user.id,
                            panel_id=panel_user.id,
                        )
                        continue
                    if dry_run:
                        logger.info('DRY-RUN would update user', user_id=user.id, panel_id=panel_user.id)
                    else:
                        apply_panel_identity(user=user, panel_user=panel_user)
                    seen_user_panel_ids.add(panel_user.id)
                    stats['users_updated'] += 1
                except Exception as exc:
                    stats['errors'] += 1
                    logger.error('Backfill failed for user', user_id=user.id, error=exc, exc_info=True)

        if not dry_run:
            await db.commit()

    logger.info('Remnawave id backfill finished', **stats)
    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Backfill Remnawave numeric ids')
    parser.add_argument('--dry-run', action='store_true', help='Do not write to DB')
    args = parser.parse_args(argv)
    stats = asyncio.run(backfill_remnawave_ids(dry_run=args.dry_run))
    print(stats)
    return 0 if stats.get('errors', 0) == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
