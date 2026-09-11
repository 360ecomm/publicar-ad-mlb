import asyncio

from app.workers.celery_app import celery_app


async def _publish_listing_async(listing_id: str) -> dict:
    from sqlalchemy import select

    from app.database import worker_session
    from app.models.listing import Listing
    from app.models.listing_attribute import ListingAttribute
    from app.models.listing_description import ListingDescription
    from app.models.listing_image import ListingImage
    from app.models.seller import Seller
    from app.services.publish_service import PublishService, get_valid_access_token

    async with worker_session() as db:
        listing = (
            await db.execute(select(Listing).where(Listing.id == listing_id))
        ).scalar_one()

        # Guard de idempotência: retry do Celery ou despacho duplicado de
        # publish_listing pode reinvocar esta task depois que o listing já
        # saiu de 'publishing' (publicado ou marcado failed) — o guard ignora
        # para não publicar de novo nem sobrescrever o resultado.
        if listing.status != "publishing":
            return {"listing_id": listing_id, "skipped": True}

        seller = (
            await db.execute(select(Seller).where(Seller.id == listing.seller_id))
        ).scalar_one()

        attributes = (
            await db.execute(
                select(ListingAttribute).where(ListingAttribute.listing_id == listing.id)
            )
        ).scalars().all()

        images = (
            await db.execute(
                select(ListingImage)
                .where(ListingImage.listing_id == listing.id, ListingImage.approved == True)
                .order_by(ListingImage.sort_order)
            )
        ).scalars().all()

        desc_row = (
            await db.execute(
                select(ListingDescription).where(ListingDescription.listing_id == listing.id)
            )
        ).scalar_one_or_none()

        description_html = desc_row.description_html if desc_row else None

        access_token = await get_valid_access_token(seller, db)

        mlb_id = await PublishService(db).publish(
            listing=listing,
            attributes=list(attributes),
            images=list(images),
            description_html=description_html,
            access_token=access_token,
        )

        listing.mlb_id = mlb_id
        listing.status = "published_paused"
        await db.commit()

        # A entrega das fotos no bucket proprio do seller (RF7, `r2_write_service`)
        # saiu em 2026-09-10 sem nunca ter sido usada — ver
        # docs/superpowers/specs/2026-09-10-entrega-ao-seller-bucket-proprio-pausada.md

    return {"listing_id": listing_id, "mlb_id": mlb_id}


@celery_app.task(name="app.workers.tasks.publish_tasks.publish_listing", bind=True, max_retries=2)
def publish_listing(self, listing_id: str) -> dict:
    from app.services.publish_service import MLValidationError
    try:
        return asyncio.run(_publish_listing_async(listing_id))
    except MLValidationError as exc:
        # Erro de validação ML: não retenta, registra na listagem
        asyncio.run(_set_failed(listing_id, str(exc)))
        raise
    except Exception as exc:
        if self.request.retries >= self.max_retries:
            asyncio.run(_set_failed(listing_id, str(exc)))
            raise
        raise self.retry(exc=exc, countdown=2 ** self.request.retries * 10)


async def _set_failed(listing_id: str, error_message: str) -> None:
    from app.database import worker_session
    from app.models.listing import Listing
    from sqlalchemy import select

    async with worker_session() as db:
        listing = (await db.execute(select(Listing).where(Listing.id == listing_id))).scalar_one_or_none()
        if listing and listing.status != "failed":
            listing.failed_step = listing.status  # capture column for UI routing
            listing.status = "failed"
            listing.error_message = error_message[:2000]
            await db.commit()
