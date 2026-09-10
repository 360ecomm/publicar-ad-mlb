import uuid as _uuid
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models.seller_image_config import SellerImageConfig
from app.schemas.seller_image_config import SellerImageConfigUpsert


class SellerImageConfigService:
    def __init__(self, db: AsyncSession, seller_id: _uuid.UUID) -> None:
        self.db = db
        self.seller_id = seller_id

    async def get(self) -> Optional[SellerImageConfig]:
        result = await self.db.execute(
            select(SellerImageConfig).where(SellerImageConfig.seller_id == self.seller_id)
        )
        return result.scalar_one_or_none()

    async def upsert(self, payload: SellerImageConfigUpsert) -> SellerImageConfig:
        cfg = await self.get()
        if cfg is None:
            cfg = SellerImageConfig(seller_id=self.seller_id, raw_base_url=payload.raw_base_url)
            self.db.add(cfg)
        else:
            cfg.raw_base_url = payload.raw_base_url

        await self.db.commit()
        await self.db.refresh(cfg)
        return cfg
