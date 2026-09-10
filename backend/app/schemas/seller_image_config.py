from datetime import datetime
from typing import Optional
from uuid import UUID
from pydantic import BaseModel, field_validator


class SellerImageConfigUpsert(BaseModel):
    raw_base_url: str

    @field_validator("raw_base_url")
    @classmethod
    def base_url_not_empty(cls, v: str) -> str:
        v = v.strip().rstrip("/")
        if not v:
            raise ValueError("raw_base_url não pode ser vazio")
        return v


class SellerImageConfigOut(BaseModel):
    id: UUID
    seller_id: UUID
    raw_base_url: str
    created_at: datetime
    updated_at: datetime
