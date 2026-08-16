"""Public routes for marketing guide pages."""

import structlog
from fastapi import APIRouter, Depends, HTTPException, Path, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.crud.guides import (
    build_public_hub,
    get_guide_page_by_slug,
)
from app.database.models import GuideCategory, GuidePage

from ..dependencies import get_cabinet_db
from ..schemas.guides import (
    GuideCategoryPublic,
    GuideHubPublicResponse,
    GuidePageLink,
    GuidePagePublicResponse,
)


logger = structlog.get_logger(__name__)

router = APIRouter(prefix='/guides', tags=['Cabinet Guides'])


def _assemble_hub(hub, categories: list[GuideCategory], pages: list[GuidePage]) -> GuideHubPublicResponse:
    pages_by_category: dict[int, list[GuidePage]] = {}
    for page in pages:
        if page.category_id is None:
            continue
        pages_by_category.setdefault(page.category_id, []).append(page)

    public_categories: list[GuideCategoryPublic] = []
    for category in categories:
        category_pages = pages_by_category.get(category.id, [])
        featured = next((p for p in category_pages if p.is_featured), None)
        regular_pages = [p for p in category_pages if not p.is_featured]

        public_categories.append(
            GuideCategoryPublic(
                id=category.id,
                slug=category.slug,
                title=category.title or {},
                description=category.description or {},
                icon=category.icon,
                footer_html=category.footer_html,
                featured_page=(
                    GuidePageLink(
                        slug=featured.slug,
                        link_label=featured.link_label or {},
                        is_featured=True,
                    )
                    if featured
                    else None
                ),
                pages=[
                    GuidePageLink(slug=page.slug, link_label=page.link_label or {}, is_featured=False)
                    for page in regular_pages
                ],
            )
        )

    return GuideHubPublicResponse(
        kicker=hub.kicker or {},
        title=hub.title or {},
        title_highlight=hub.title_highlight or {},
        description=hub.description or {},
        meta_title=hub.meta_title or {},
        meta_description=hub.meta_description or {},
        categories=public_categories,
    )


@router.get('/hub', response_model=GuideHubPublicResponse)
async def get_guides_hub(
    db: AsyncSession = Depends(get_cabinet_db),
) -> GuideHubPublicResponse:
    try:
        hub, categories, pages = await build_public_hub(db)
        if not categories:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Guides hub is not configured')
        return _assemble_hub(hub, categories, pages)
    except HTTPException:
        raise
    except Exception:
        logger.exception('Failed to load guides hub')
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail='Failed to load guides hub',
        )


@router.get('/pages/{slug}', response_model=GuidePagePublicResponse)
async def get_guide_page_public(
    slug: str = Path(..., max_length=200, pattern=r'^[a-z0-9\-]+$'),
    db: AsyncSession = Depends(get_cabinet_db),
) -> GuidePagePublicResponse:
    page = await get_guide_page_by_slug(db, slug)
    if not page or not page.is_active:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Guide page not found')

    category_slug = page.category.slug if page.category else None
    return GuidePagePublicResponse(
        id=page.id,
        slug=page.slug,
        title=page.title or {},
        content=page.content or {},
        meta_title=page.meta_title or {},
        meta_description=page.meta_description or {},
        category_slug=category_slug,
    )
