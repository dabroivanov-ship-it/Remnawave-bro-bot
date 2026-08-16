"""Pydantic schemas for marketing guide pages (/guides hub)."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class GuidePageLink(BaseModel):
    slug: str
    link_label: dict[str, str]
    is_featured: bool = False


class GuideCategoryPublic(BaseModel):
    id: int
    slug: str
    title: dict[str, str]
    description: dict[str, str]
    icon: str
    footer_html: dict[str, str] | None = None
    featured_page: GuidePageLink | None = None
    pages: list[GuidePageLink] = Field(default_factory=list)


class GuideHubPublicResponse(BaseModel):
    kicker: dict[str, str]
    title: dict[str, str]
    title_highlight: dict[str, str]
    description: dict[str, str]
    meta_title: dict[str, str]
    meta_description: dict[str, str]
    categories: list[GuideCategoryPublic] = Field(default_factory=list)


class GuidePagePublicResponse(BaseModel):
    id: int
    slug: str
    title: dict[str, str]
    content: dict[str, str]
    meta_title: dict[str, str]
    meta_description: dict[str, str]
    category_slug: str | None = None

    model_config = ConfigDict(from_attributes=True)


class GuideHubSettingsResponse(BaseModel):
    kicker: dict[str, str]
    title: dict[str, str]
    title_highlight: dict[str, str]
    description: dict[str, str]
    meta_title: dict[str, str]
    meta_description: dict[str, str]
    updated_at: datetime | None = None


class GuideHubSettingsUpdateRequest(BaseModel):
    kicker: dict[str, str] | None = None
    title: dict[str, str] | None = None
    title_highlight: dict[str, str] | None = None
    description: dict[str, str] | None = None
    meta_title: dict[str, str] | None = None
    meta_description: dict[str, str] | None = None


class GuideCategoryResponse(BaseModel):
    id: int
    slug: str
    title: dict[str, str]
    description: dict[str, str]
    icon: str
    footer_html: dict[str, str] | None = None
    sort_order: int
    is_active: bool
    created_at: datetime
    updated_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class GuideCategoryCreateRequest(BaseModel):
    slug: str = Field(min_length=1, max_length=100, pattern=r'^[a-z0-9\-]+$')
    title: dict[str, str] = Field(default_factory=dict)
    description: dict[str, str] = Field(default_factory=dict)
    icon: str = Field(default='openwrt', max_length=50)
    footer_html: dict[str, str] | None = None
    sort_order: int = 0
    is_active: bool = True


class GuideCategoryUpdateRequest(BaseModel):
    slug: str | None = Field(None, min_length=1, max_length=100, pattern=r'^[a-z0-9\-]+$')
    title: dict[str, str] | None = None
    description: dict[str, str] | None = None
    icon: str | None = Field(None, max_length=50)
    footer_html: dict[str, str] | None = None
    sort_order: int | None = None
    is_active: bool | None = None


class GuidePageListItem(BaseModel):
    id: int
    slug: str
    category_id: int | None
    title: dict[str, str]
    link_label: dict[str, str]
    is_featured: bool
    sort_order: int
    is_active: bool
    updated_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class GuidePageResponse(BaseModel):
    id: int
    slug: str
    category_id: int | None
    title: dict[str, str]
    link_label: dict[str, str]
    content: dict[str, str]
    meta_title: dict[str, str]
    meta_description: dict[str, str]
    is_featured: bool
    sort_order: int
    is_active: bool
    created_at: datetime
    updated_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class GuidePageCreateRequest(BaseModel):
    slug: str = Field(min_length=1, max_length=200, pattern=r'^[a-z0-9\-]+$')
    category_id: int | None = None
    title: dict[str, str] = Field(default_factory=dict)
    link_label: dict[str, str] = Field(default_factory=dict)
    content: dict[str, str] = Field(default_factory=dict)
    meta_title: dict[str, str] = Field(default_factory=dict)
    meta_description: dict[str, str] = Field(default_factory=dict)
    is_featured: bool = False
    sort_order: int = 0
    is_active: bool = True


class GuidePageUpdateRequest(BaseModel):
    slug: str | None = Field(None, min_length=1, max_length=200, pattern=r'^[a-z0-9\-]+$')
    category_id: int | None = None
    title: dict[str, str] | None = None
    link_label: dict[str, str] | None = None
    content: dict[str, str] | None = None
    meta_title: dict[str, str] | None = None
    meta_description: dict[str, str] | None = None
    is_featured: bool | None = None
    sort_order: int | None = None
    is_active: bool | None = None


class GuideReorderItem(BaseModel):
    id: int
    sort_order: int = Field(ge=0)


class GuideReorderRequest(BaseModel):
    items: list[GuideReorderItem] = Field(..., min_length=1)
