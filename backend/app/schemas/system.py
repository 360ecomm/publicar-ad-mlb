from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel


class PendingRawPhotosItem(BaseModel):
    id: UUID
    sku_external_id: Optional[str]
    created_via: str
    waiting_since: datetime
    error_message: Optional[str]


class PendingRawPhotosOut(BaseModel):
    count: int
    listings: list[PendingRawPhotosItem]
