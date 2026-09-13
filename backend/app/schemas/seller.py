from datetime import datetime
from uuid import UUID
from pydantic import BaseModel


class SellerOut(BaseModel):
    id: UUID
    ml_user_id: int
    ml_nickname: str
    ml_site_id: str
    token_expires_at: datetime
    is_active: bool
    granted_at: datetime

    model_config = {"from_attributes": True}


class ListingStatusCount(BaseModel):
    status: str
    count: int


class SellerDashboardEntry(BaseModel):
    seller_id: UUID
    ml_nickname: str
    is_active: bool
    listings_by_status: dict[str, int]
    total_listings: int
    last_activity_at: datetime | None


class DashboardResponse(BaseModel):
    sellers: list[SellerDashboardEntry]
