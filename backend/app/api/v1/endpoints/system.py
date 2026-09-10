from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_active_seller, get_db
from app.models.listing import Listing
from app.schemas.system import PendingRawPhotosItem, PendingRawPhotosOut
from app.services.raw_photo_standby_service import PENDING_RAW_PHOTOS

router = APIRouter(prefix="/system", tags=["system"])


@router.get("/pending-raw-photos", response_model=PendingRawPhotosOut)
async def get_pending_raw_photos(
    active_seller=Depends(get_active_seller),
    db: AsyncSession = Depends(get_db),
):
    """Quantos anuncios do seller ativo esperam foto bruta no bucket, e quais.

    Mesmo papel que o antigo `/system/image-engine` tinha para a confirmacao
    de motor: visibilidade do que esta parado esperando algo externo.
    """
    rows = (
        await db.execute(
            select(Listing)
            .where(Listing.seller_id == active_seller.id, Listing.status == PENDING_RAW_PHOTOS)
            .order_by(Listing.updated_at.asc())
        )
    ).scalars().all()
    return PendingRawPhotosOut(
        count=len(rows),
        listings=[
            PendingRawPhotosItem(
                id=l.id,
                sku_external_id=l.sku_external_id,
                created_via=l.created_via,
                waiting_since=l.updated_at,
                error_message=l.error_message,
            )
            for l in rows
        ],
    )
