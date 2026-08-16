from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class RouterTariffInput(BaseModel):
    id: int | None = None
    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    price_kopeks: int = Field(..., ge=0)
    display_order: int = 0
    is_active: bool = True


class RouterTariffResponse(BaseModel):
    id: int
    name: str
    description: str | None = None
    price_kopeks: int
    display_order: int
    is_active: bool

    model_config = {'from_attributes': True}


class RouterProductListItem(BaseModel):
    id: int
    name: str
    description: str | None = None
    image_url: str | None = None
    display_order: int
    is_active: bool
    tariffs_count: int = 0
    min_price_kopeks: int | None = None

    model_config = {'from_attributes': True}


class RouterProductListResponse(BaseModel):
    items: list[RouterProductListItem]
    total: int


class RouterProductDetailResponse(BaseModel):
    id: int
    name: str
    description: str | None = None
    image_url: str | None = None
    display_order: int
    is_active: bool
    tariffs: list[RouterTariffResponse]
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = {'from_attributes': True}


class RouterProductCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    image_url: str | None = Field(None, max_length=500)
    display_order: int = 0
    is_active: bool = True
    tariffs: list[RouterTariffInput] = Field(default_factory=list)


class RouterProductUpdateRequest(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = None
    image_url: str | None = Field(None, max_length=500)
    display_order: int | None = None
    is_active: bool | None = None
    tariffs: list[RouterTariffInput] | None = None


class RouterProductOrderRequest(BaseModel):
    order: list[int]


class RouterProductToggleResponse(BaseModel):
    id: int
    is_active: bool


class RouterShopTariff(BaseModel):
    id: int
    name: str
    description: str | None = None
    price_kopeks: int


class RouterShopProduct(BaseModel):
    id: int
    name: str
    description: str | None = None
    image_url: str | None = None
    tariffs: list[RouterShopTariff]


class RouterShopListResponse(BaseModel):
    items: list[RouterShopProduct]


class RouterOrderCreateRequest(BaseModel):
    router_product_id: int
    router_product_tariff_id: int
    delivery_provider: Literal['cdek', 'yandex_delivery']
    recipient_name: str = Field(..., min_length=2, max_length=255)
    recipient_phone: str = Field(..., min_length=5, max_length=50)
    recipient_address: str = Field(..., min_length=5, max_length=2000)
    recipient_comment: str | None = Field(None, max_length=1000)


class RouterOrderResponse(BaseModel):
    id: int
    product_name: str
    tariff_name: str
    amount_kopeks: int
    status: str
    delivery_provider: str
    recipient_name: str
    recipient_phone: str
    recipient_address: str
    recipient_comment: str | None = None
    created_at: datetime | None = None

    model_config = {'from_attributes': True}
