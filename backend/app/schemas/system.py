from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel


class PendingListingItem(BaseModel):
    """Um anuncio parado em standby (foto bruta ausente, motor de IA fora)."""

    id: UUID
    sku_external_id: Optional[str]
    created_via: str
    waiting_since: datetime
    error_message: Optional[str]


class PendingListingsOut(BaseModel):
    count: int
    listings: list[PendingListingItem]


# Nomes anteriores, mantidos para quem ja os importa.
PendingRawPhotosItem = PendingListingItem
PendingRawPhotosOut = PendingListingsOut
