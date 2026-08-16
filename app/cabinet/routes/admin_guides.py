"""Admin routes for marketing guide pages."""

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.crud.guides import (
    clear_featured_in_category,
    create_category,
    create_guide_page,
    delete_category,
    delete_guide_page,
    get_all_categories,
    get_all_guide_pages,
    get_category_by_id,
    get_guide_page_by_id,
    get_or_create_hub_settings,
    reorder_guide_categories,
    reorder_guide_pages,
    update_category,
    update_guide_page,
    update_hub_settings,
)
from app.database.models import User

from ..dependencies import get_cabinet_db, require_permission
from ..schemas.guides import (
    GuideCategoryCreateRequest,
    GuideCategoryResponse,
    GuideCategoryUpdateRequest,
    GuideHubSettingsResponse,
    GuideHubSettingsUpdateRequest,
    GuidePageCreateRequest,
    GuidePageListItem,
    GuidePageResponse,
    GuidePageUpdateRequest,
    GuideReorderRequest,
)


logger = structlog.get_logger(__name__)

router = APIRouter(prefix='/admin/guides', tags=['Cabinet Admin Guides'])


@router.get('/hub-settings', response_model=GuideHubSettingsResponse)
async def get_hub_settings_admin(
    admin: User = Depends(require_permission('guides:read')),
    db: AsyncSession = Depends(get_cabinet_db),
) -> GuideHubSettingsResponse:
    settings = await get_or_create_hub_settings(db)
    return GuideHubSettingsResponse.model_validate(settings)


@router.put('/hub-settings', response_model=GuideHubSettingsResponse)
async def update_hub_settings_admin(
    request: GuideHubSettingsUpdateRequest,
    admin: User = Depends(require_permission('guides:edit')),
    db: AsyncSession = Depends(get_cabinet_db),
) -> GuideHubSettingsResponse:
    settings = await update_hub_settings(db, **request.model_dump(exclude_unset=True))
    return GuideHubSettingsResponse.model_validate(settings)


@router.get('/categories', response_model=list[GuideCategoryResponse])
async def list_categories_admin(
    admin: User = Depends(require_permission('guides:read')),
    db: AsyncSession = Depends(get_cabinet_db),
) -> list[GuideCategoryResponse]:
    categories = await get_all_categories(db, include_inactive=True)
    return [GuideCategoryResponse.model_validate(c) for c in categories]


@router.post('/categories', response_model=GuideCategoryResponse, status_code=status.HTTP_201_CREATED)
async def create_category_admin(
    request: GuideCategoryCreateRequest,
    admin: User = Depends(require_permission('guides:edit')),
    db: AsyncSession = Depends(get_cabinet_db),
) -> GuideCategoryResponse:
    try:
        category = await create_category(db, **request.model_dump())
        return GuideCategoryResponse.model_validate(category)
    except IntegrityError:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail='Category slug already exists')


@router.put('/categories/{category_id}', response_model=GuideCategoryResponse)
async def update_category_admin(
    category_id: int,
    request: GuideCategoryUpdateRequest,
    admin: User = Depends(require_permission('guides:edit')),
    db: AsyncSession = Depends(get_cabinet_db),
) -> GuideCategoryResponse:
    category = await get_category_by_id(db, category_id)
    if not category:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Category not found')
    try:
        updated = await update_category(db, category, **request.model_dump(exclude_unset=True))
        return GuideCategoryResponse.model_validate(updated)
    except IntegrityError:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail='Category slug already exists')


@router.delete('/categories/{category_id}', status_code=status.HTTP_204_NO_CONTENT)
async def delete_category_admin(
    category_id: int,
    admin: User = Depends(require_permission('guides:edit')),
    db: AsyncSession = Depends(get_cabinet_db),
) -> None:
    category = await get_category_by_id(db, category_id)
    if not category:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Category not found')
    await delete_category(db, category)


@router.post('/categories/reorder', status_code=status.HTTP_204_NO_CONTENT)
async def reorder_categories_admin(
    request: GuideReorderRequest,
    admin: User = Depends(require_permission('guides:edit')),
    db: AsyncSession = Depends(get_cabinet_db),
) -> None:
    await reorder_guide_categories(db, [item.model_dump() for item in request.items])


@router.get('/pages', response_model=list[GuidePageListItem])
async def list_pages_admin(
    admin: User = Depends(require_permission('guides:read')),
    db: AsyncSession = Depends(get_cabinet_db),
) -> list[GuidePageListItem]:
    pages = await get_all_guide_pages(db, include_inactive=True)
    return [GuidePageListItem.model_validate(p) for p in pages]


@router.get('/pages/{page_id}', response_model=GuidePageResponse)
async def get_page_admin(
    page_id: int,
    admin: User = Depends(require_permission('guides:read')),
    db: AsyncSession = Depends(get_cabinet_db),
) -> GuidePageResponse:
    page = await get_guide_page_by_id(db, page_id)
    if not page:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Guide page not found')
    return GuidePageResponse.model_validate(page)


@router.post('/pages', response_model=GuidePageResponse, status_code=status.HTTP_201_CREATED)
async def create_page_admin(
    request: GuidePageCreateRequest,
    admin: User = Depends(require_permission('guides:edit')),
    db: AsyncSession = Depends(get_cabinet_db),
) -> GuidePageResponse:
    try:
        if request.is_featured and request.category_id is not None:
            await clear_featured_in_category(db, request.category_id)
        page = await create_guide_page(db, **request.model_dump())
        return GuidePageResponse.model_validate(page)
    except IntegrityError:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail='Page slug already exists')


@router.put('/pages/{page_id}', response_model=GuidePageResponse)
async def update_page_admin(
    page_id: int,
    request: GuidePageUpdateRequest,
    admin: User = Depends(require_permission('guides:edit')),
    db: AsyncSession = Depends(get_cabinet_db),
) -> GuidePageResponse:
    page = await get_guide_page_by_id(db, page_id)
    if not page:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Guide page not found')

    payload = request.model_dump(exclude_unset=True)
    try:
        if payload.get('is_featured'):
            category_id = payload.get('category_id', page.category_id)
            if category_id is not None:
                await clear_featured_in_category(db, category_id, exclude_page_id=page.id)
        updated = await update_guide_page(db, page, **payload)
        return GuidePageResponse.model_validate(updated)
    except IntegrityError:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail='Page slug already exists')


@router.delete('/pages/{page_id}', status_code=status.HTTP_204_NO_CONTENT)
async def delete_page_admin(
    page_id: int,
    admin: User = Depends(require_permission('guides:edit')),
    db: AsyncSession = Depends(get_cabinet_db),
) -> None:
    page = await get_guide_page_by_id(db, page_id)
    if not page:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Guide page not found')
    await delete_guide_page(db, page)


@router.post('/pages/reorder', status_code=status.HTTP_204_NO_CONTENT)
async def reorder_pages_admin(
    request: GuideReorderRequest,
    admin: User = Depends(require_permission('guides:edit')),
    db: AsyncSession = Depends(get_cabinet_db),
) -> None:
    await reorder_guide_pages(db, [item.model_dump() for item in request.items])
