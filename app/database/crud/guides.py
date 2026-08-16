"""CRUD operations for marketing guide pages (/guides hub)."""

from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database.models import GuideCategory, GuideHubSettings, GuidePage


logger = structlog.get_logger(__name__)

_HUB_SETTINGS_ID = 1

_CATEGORY_UPDATE_FIELDS = frozenset(
    {'slug', 'title', 'description', 'icon', 'footer_html', 'sort_order', 'is_active'}
)
_PAGE_UPDATE_FIELDS = frozenset(
    {
        'slug',
        'category_id',
        'title',
        'link_label',
        'content',
        'meta_title',
        'meta_description',
        'is_featured',
        'sort_order',
        'is_active',
    }
)
_HUB_UPDATE_FIELDS = frozenset(
    {'kicker', 'title', 'title_highlight', 'description', 'meta_title', 'meta_description'}
)


async def get_or_create_hub_settings(db: AsyncSession) -> GuideHubSettings:
    result = await db.execute(select(GuideHubSettings).where(GuideHubSettings.id == _HUB_SETTINGS_ID))
    settings = result.scalar_one_or_none()
    if settings:
        return settings

    settings = GuideHubSettings(id=_HUB_SETTINGS_ID)
    db.add(settings)
    await db.commit()
    await db.refresh(settings)
    return settings


async def update_hub_settings(db: AsyncSession, **fields: Any) -> GuideHubSettings:
    settings = await get_or_create_hub_settings(db)
    for key, value in fields.items():
        if key in _HUB_UPDATE_FIELDS and value is not None:
            setattr(settings, key, value)
    settings.updated_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(settings)
    return settings


async def get_all_categories(db: AsyncSession, *, include_inactive: bool = False) -> list[GuideCategory]:
    stmt = select(GuideCategory).order_by(GuideCategory.sort_order.asc(), GuideCategory.id.asc())
    if not include_inactive:
        stmt = stmt.where(GuideCategory.is_active.is_(True))
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_category_by_id(db: AsyncSession, category_id: int) -> GuideCategory | None:
    result = await db.execute(select(GuideCategory).where(GuideCategory.id == category_id))
    return result.scalar_one_or_none()


async def create_category(db: AsyncSession, **fields: Any) -> GuideCategory:
    category = GuideCategory(**fields)
    db.add(category)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise
    await db.refresh(category)
    return category


async def update_category(db: AsyncSession, category: GuideCategory, **fields: Any) -> GuideCategory:
    for key, value in fields.items():
        if key in _CATEGORY_UPDATE_FIELDS:
            setattr(category, key, value)
    category.updated_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(category)
    return category


async def delete_category(db: AsyncSession, category: GuideCategory) -> None:
    await db.delete(category)
    await db.commit()


async def get_all_guide_pages(
    db: AsyncSession,
    *,
    include_inactive: bool = False,
    category_id: int | None = None,
) -> list[GuidePage]:
    stmt = select(GuidePage).order_by(GuidePage.sort_order.asc(), GuidePage.id.asc())
    if not include_inactive:
        stmt = stmt.where(GuidePage.is_active.is_(True))
    if category_id is not None:
        stmt = stmt.where(GuidePage.category_id == category_id)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_guide_page_by_id(db: AsyncSession, page_id: int) -> GuidePage | None:
    result = await db.execute(select(GuidePage).where(GuidePage.id == page_id))
    return result.scalar_one_or_none()


async def get_guide_page_by_slug(db: AsyncSession, slug: str) -> GuidePage | None:
    result = await db.execute(
        select(GuidePage).options(selectinload(GuidePage.category)).where(GuidePage.slug == slug)
    )
    return result.scalar_one_or_none()


async def create_guide_page(db: AsyncSession, **fields: Any) -> GuidePage:
    page = GuidePage(**fields)
    db.add(page)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise
    await db.refresh(page)
    logger.info('Created guide page', page_id=page.id, slug=page.slug)
    return page


async def update_guide_page(db: AsyncSession, page: GuidePage, **fields: Any) -> GuidePage:
    for key, value in fields.items():
        if key in _PAGE_UPDATE_FIELDS:
            setattr(page, key, value)
    page.updated_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(page)
    return page


async def delete_guide_page(db: AsyncSession, page: GuidePage) -> None:
    await db.delete(page)
    await db.commit()


async def reorder_guide_pages(db: AsyncSession, items: list[dict[str, int]]) -> None:
    for item in items:
        await db.execute(
            update(GuidePage)
            .where(GuidePage.id == item['id'])
            .values(sort_order=item['sort_order'], updated_at=datetime.now(UTC))
        )
    await db.commit()


async def reorder_guide_categories(db: AsyncSession, items: list[dict[str, int]]) -> None:
    for item in items:
        await db.execute(
            update(GuideCategory)
            .where(GuideCategory.id == item['id'])
            .values(sort_order=item['sort_order'], updated_at=datetime.now(UTC))
        )
    await db.commit()


async def build_public_hub(db: AsyncSession) -> tuple[GuideHubSettings, list[GuideCategory], list[GuidePage]]:
    hub = await get_or_create_hub_settings(db)
    categories = await get_all_categories(db, include_inactive=False)
    pages = await get_all_guide_pages(db, include_inactive=False)
    return hub, categories, pages


async def clear_featured_in_category(db: AsyncSession, category_id: int, *, exclude_page_id: int | None = None) -> None:
    stmt = update(GuidePage).where(GuidePage.category_id == category_id, GuidePage.is_featured.is_(True))
    if exclude_page_id is not None:
        stmt = stmt.where(GuidePage.id != exclude_page_id)
    await db.execute(stmt.values(is_featured=False, updated_at=datetime.now(UTC)))
    await db.commit()
