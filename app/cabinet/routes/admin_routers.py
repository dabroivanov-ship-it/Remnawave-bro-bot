"""Admin routes for managing VPN router products."""

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.crud.router_product import (
    count_router_products,
    create_router_product,
    delete_router_product,
    get_all_router_products,
    get_router_product_by_id,
    reorder_router_products,
    toggle_router_product,
    update_router_product,
)
from app.database.models import RouterProduct, User

from ..dependencies import get_cabinet_db, require_permission
from ..schemas.routers import (
    RouterProductCreateRequest,
    RouterProductDetailResponse,
    RouterProductListItem,
    RouterProductListResponse,
    RouterProductOrderRequest,
    RouterProductToggleResponse,
    RouterProductUpdateRequest,
    RouterTariffResponse,
)


router = APIRouter(prefix='/admin/routers', tags=['Cabinet Admin Routers'])


def _serialize_list_item(product: RouterProduct) -> RouterProductListItem:
    active_tariffs = [t for t in product.tariffs if t.is_active]
    min_price = min((t.price_kopeks for t in active_tariffs), default=None)
    return RouterProductListItem(
        id=product.id,
        name=product.name,
        description=product.description,
        image_url=product.image_url,
        display_order=product.display_order,
        is_active=product.is_active,
        tariffs_count=len(product.tariffs),
        min_price_kopeks=min_price,
    )


def _serialize_detail(product: RouterProduct) -> RouterProductDetailResponse:
    return RouterProductDetailResponse(
        id=product.id,
        name=product.name,
        description=product.description,
        image_url=product.image_url,
        display_order=product.display_order,
        is_active=product.is_active,
        tariffs=[RouterTariffResponse.model_validate(t) for t in product.tariffs],
        created_at=product.created_at,
        updated_at=product.updated_at,
    )


def _tariffs_to_dicts(tariffs: list) -> list[dict]:
    return [t.model_dump() for t in tariffs]


@router.get('', response_model=RouterProductListResponse)
async def list_router_products(
    include_inactive: bool = True,
    admin: User = Depends(require_permission('routers:read')),
    db: AsyncSession = Depends(get_cabinet_db),
) -> RouterProductListResponse:
    products = await get_all_router_products(db, include_inactive=include_inactive)
    total = await count_router_products(db, include_inactive=include_inactive)
    return RouterProductListResponse(
        items=[_serialize_list_item(p) for p in products],
        total=total,
    )


@router.put('/order', status_code=status.HTTP_204_NO_CONTENT)
async def reorder_router_products_endpoint(
    payload: RouterProductOrderRequest,
    admin: User = Depends(require_permission('routers:edit')),
    db: AsyncSession = Depends(get_cabinet_db),
) -> Response:
    await reorder_router_products(db, payload.order)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get('/{product_id}', response_model=RouterProductDetailResponse)
async def get_router_product(
    product_id: int,
    admin: User = Depends(require_permission('routers:read')),
    db: AsyncSession = Depends(get_cabinet_db),
) -> RouterProductDetailResponse:
    product = await get_router_product_by_id(db, product_id)
    if not product:
        raise HTTPException(status.HTTP_404_NOT_FOUND, 'Router product not found')
    return _serialize_detail(product)


@router.post('', response_model=RouterProductDetailResponse, status_code=status.HTTP_201_CREATED)
async def create_router_product_endpoint(
    payload: RouterProductCreateRequest,
    admin: User = Depends(require_permission('routers:create')),
    db: AsyncSession = Depends(get_cabinet_db),
) -> RouterProductDetailResponse:
    product = await create_router_product(
        db,
        name=payload.name,
        description=payload.description,
        image_url=payload.image_url,
        display_order=payload.display_order,
        is_active=payload.is_active,
        tariffs=_tariffs_to_dicts(payload.tariffs),
    )
    await db.commit()
    product = await get_router_product_by_id(db, product.id)
    return _serialize_detail(product)


@router.put('/{product_id}', response_model=RouterProductDetailResponse)
async def update_router_product_endpoint(
    product_id: int,
    payload: RouterProductUpdateRequest,
    admin: User = Depends(require_permission('routers:edit')),
    db: AsyncSession = Depends(get_cabinet_db),
) -> RouterProductDetailResponse:
    product = await get_router_product_by_id(db, product_id)
    if not product:
        raise HTTPException(status.HTTP_404_NOT_FOUND, 'Router product not found')

    update_data = payload.model_dump(exclude_unset=True)
    tariffs = update_data.pop('tariffs', None)
    if tariffs is not None:
        tariffs = _tariffs_to_dicts(payload.tariffs or [])

    product = await update_router_product(db, product, tariffs=tariffs, **update_data)
    await db.commit()
    product = await get_router_product_by_id(db, product.id)
    return _serialize_detail(product)


@router.delete('/{product_id}', status_code=status.HTTP_204_NO_CONTENT)
async def delete_router_product_endpoint(
    product_id: int,
    admin: User = Depends(require_permission('routers:delete')),
    db: AsyncSession = Depends(get_cabinet_db),
) -> Response:
    product = await get_router_product_by_id(db, product_id)
    if not product:
        raise HTTPException(status.HTTP_404_NOT_FOUND, 'Router product not found')

    await delete_router_product(db, product)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post('/{product_id}/toggle', response_model=RouterProductToggleResponse)
async def toggle_router_product_endpoint(
    product_id: int,
    admin: User = Depends(require_permission('routers:edit')),
    db: AsyncSession = Depends(get_cabinet_db),
) -> RouterProductToggleResponse:
    product = await get_router_product_by_id(db, product_id)
    if not product:
        raise HTTPException(status.HTTP_404_NOT_FOUND, 'Router product not found')

    product = await toggle_router_product(db, product)
    await db.commit()
    return RouterProductToggleResponse(id=product.id, is_active=product.is_active)
