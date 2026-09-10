from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_active_seller, get_db
from app.models.listing import Listing
from app.schemas.system import PendingListingItem, PendingListingsOut
from app.services.ai_engine_standby_service import PENDING_AI_ENGINE
from app.services.raw_photo_standby_service import PENDING_RAW_PHOTOS

router = APIRouter(prefix="/system", tags=["system"])


async def _pendentes(db: AsyncSession, seller_id, status: str) -> PendingListingsOut:
    """Anuncios do seller ativo parados num status de espera, mais antigos primeiro."""
    rows = (
        await db.execute(
            select(Listing)
            .where(Listing.seller_id == seller_id, Listing.status == status)
            .order_by(Listing.updated_at.asc())
        )
    ).scalars().all()
    return PendingListingsOut(
        count=len(rows),
        listings=[
            PendingListingItem(
                id=l.id,
                sku_external_id=l.sku_external_id,
                created_via=l.created_via,
                waiting_since=l.updated_at,
                error_message=l.error_message,
            )
            for l in rows
        ],
    )


@router.get("/pending-raw-photos", response_model=PendingListingsOut)
async def get_pending_raw_photos(
    active_seller=Depends(get_active_seller),
    db: AsyncSession = Depends(get_db),
):
    """Quantos anuncios esperam foto bruta no bucket, e quais."""
    return await _pendentes(db, active_seller.id, PENDING_RAW_PHOTOS)


@router.get("/pending-ai-engine", response_model=PendingListingsOut)
async def get_pending_ai_engine(
    active_seller=Depends(get_active_seller),
    db: AsyncSession = Depends(get_db),
):
    """Quantos anuncios esperam o motor de IA voltar (credito OpenAI), e quais."""
    return await _pendentes(db, active_seller.id, PENDING_AI_ENGINE)
