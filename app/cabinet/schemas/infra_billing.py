from datetime import date, datetime

from pydantic import BaseModel, Field


class InfraProviderResponse(BaseModel):
    id: int
    name: str
    website: str | None = None
    login_url: str | None = None
    notes: str | None = None
    currency: str
    servers_count: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = {'from_attributes': True}


class InfraProviderListResponse(BaseModel):
    items: list[InfraProviderResponse]
    total: int


class InfraProviderCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    website: str | None = Field(None, max_length=500)
    login_url: str | None = Field(None, max_length=500)
    notes: str | None = None
    currency: str = Field(default='RUB', max_length=10)


class InfraProviderUpdateRequest(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    website: str | None = Field(None, max_length=500)
    login_url: str | None = Field(None, max_length=500)
    notes: str | None = None
    currency: str | None = Field(None, max_length=10)


class InfraServerResponse(BaseModel):
    id: int
    provider_id: int | None = None
    provider_name: str | None = None
    remnawave_node_uuid: str | None = None
    name: str
    address: str | None = None
    country_code: str | None = None
    billing_amount_kopeks: int | None = None
    billing_cycle_days: int
    next_due_date: date | None = None
    last_paid_at: datetime | None = None
    payment_status: str
    is_active: bool
    notes: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = {'from_attributes': True}


class InfraServerListResponse(BaseModel):
    items: list[InfraServerResponse]
    total: int


class InfraServerCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    provider_id: int | None = None
    remnawave_node_uuid: str | None = Field(None, max_length=64)
    address: str | None = Field(None, max_length=255)
    country_code: str | None = Field(None, max_length=10)
    billing_amount_kopeks: int | None = Field(None, ge=0)
    billing_cycle_days: int = Field(default=30, ge=1, le=365)
    next_due_date: date | None = None
    notes: str | None = None
    is_active: bool = True


class InfraServerUpdateRequest(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    provider_id: int | None = None
    remnawave_node_uuid: str | None = Field(None, max_length=64)
    address: str | None = Field(None, max_length=255)
    country_code: str | None = Field(None, max_length=10)
    billing_amount_kopeks: int | None = Field(None, ge=0)
    billing_cycle_days: int | None = Field(None, ge=1, le=365)
    next_due_date: date | None = None
    notes: str | None = None
    is_active: bool | None = None


class InfraMarkPaidRequest(BaseModel):
    amount_kopeks: int = Field(..., ge=1)
    notes: str | None = None
    extend_due_date: date | None = None


class InfraPaymentResponse(BaseModel):
    id: int
    server_id: int
    server_name: str | None = None
    provider_id: int | None = None
    provider_name: str | None = None
    amount_kopeks: int
    paid_at: datetime
    due_date: date | None = None
    source: str
    source_event: str | None = None
    notes: str | None = None
    created_by_user_id: int | None = None
    created_at: datetime | None = None

    model_config = {'from_attributes': True}


class InfraPaymentListResponse(BaseModel):
    items: list[InfraPaymentResponse]
    total: int


class InfraUpcomingServer(BaseModel):
    id: int
    name: str
    provider_name: str | None = None
    next_due_date: date | None = None
    payment_status: str
    billing_amount_kopeks: int | None = None


class InfraDashboardResponse(BaseModel):
    total_monthly_kopeks: int
    monthly_is_estimated: bool = False
    paid_this_month_kopeks: int = 0
    active_servers_count: int
    providers_count: int
    by_status: dict[str, int]
    overdue_count: int = 0
    overdue_amount_kopeks: int = 0
    overdue: list[InfraUpcomingServer] = []
    upcoming_count: int
    upcoming_amount_kopeks: int = 0
    upcoming: list[InfraUpcomingServer]


class InfraSyncResponse(BaseModel):
    synced: int
    created: int
    updated: int
    payments_imported: int = 0
    message: str
