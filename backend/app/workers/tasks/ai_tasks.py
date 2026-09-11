import asyncio
from app.workers.celery_app import celery_app


async def _generate_title_async(
    listing_id: str,
    batch_mode: bool = False,
    ean: str | None = None,
    seo_context: str | None = None,
) -> dict:
    from app.database import worker_session
    from app.models.listing import Listing
    from app.models.listing_title import ListingTitle
    from app.models.product import Product
    from app.services.ai.service import get_ai_provider
    from app.services.seller_title_config_service import SellerTitleConfigService
    from sqlalchemy import select

    async with worker_session() as db:
        result = await db.execute(select(Listing).where(Listing.id == listing_id))
        listing = result.scalar_one()

        from app.services.ai.cost_log import set_cost_context
        set_cost_context(listing_id=listing.id, sku=listing.sku_external_id)

        # Fetch product for structured fields and product_group
        product: Product | None = None
        if listing.product_id:
            p_result = await db.execute(select(Product).where(Product.id == listing.product_id))
            product = p_result.scalar_one_or_none()

        # Resolve seller title config
        svc = SellerTitleConfigService(db, listing.seller_id)
        title_config = await svc.resolve(product.product_group if product else None)

        provider = get_ai_provider()
        titles = await provider.generate_titles(
            sku_description=listing.sku_description,
            sku_brand=listing.sku_brand,
            condition=listing.condition,
            ean=ean,
            seo_context=seo_context,
            batch_mode=batch_mode,
            title_config=title_config,
            sku_model=listing.sku_model,
            technical_reference=product.technical_reference if product else None,
            vehicle_application=product.vehicle_application if product else None,
            color=product.color if product else None,
            size=product.size if product else None,
            capacity=product.capacity if product else None,
            material=product.material if product else None,
            gender=product.gender if product else None,
        )

        for t in titles:
            db.add(ListingTitle(
                listing_id=listing.id,
                title_text=t["title"],
                ai_score=t.get("score"),
                selected=batch_mode,
            ))

        if batch_mode:
            listing.selected_title = titles[0]["title"]
            listing.status = "predicting_category"
        else:
            listing.status = "pending_title_approval"

        await db.commit()

        if batch_mode:
            from app.workers.tasks.category_tasks import predict_category
            predict_category.delay(listing_id, ean=ean)

    return {"listing_id": listing_id, "titles_generated": len(titles), "batch_mode": batch_mode}


async def _generate_description_async(listing_id: str) -> dict:
    from app.database import worker_session
    from app.models.listing import Listing
    from app.models.listing_attribute import ListingAttribute
    from app.models.listing_description import ListingDescription
    from app.services.ai.service import get_ai_provider
    from sqlalchemy import select

    async with worker_session() as db:
        result = await db.execute(select(Listing).where(Listing.id == listing_id))
        listing = result.scalar_one()

        # Guard de idempotência: retry do Celery ou despacho duplicado de
        # generate_description.delay pode reinvocar esta task depois que o
        # listing já saiu de 'generating_description' — o guard ignora para
        # não sobrescrever o estado real.
        if listing.status != "generating_description":
            return {"listing_id": listing_id, "skipped": True}

        from app.services.ai.cost_log import set_cost_context
        set_cost_context(listing_id=listing.id, sku=listing.sku_external_id)

        attrs_result = await db.execute(
            select(ListingAttribute).where(ListingAttribute.listing_id == listing.id)
        )
        attributes = [
            {"attribute_name": a.attribute_name, "value_name": a.value_name}
            for a in attrs_result.scalars().all()
            if a.value_name
        ]

        provider = get_ai_provider()
        description_html = await provider.generate_description({
            "selected_title": listing.selected_title,
            "sku_brand": listing.sku_brand,
            "sku_description": listing.sku_description,
            "condition": listing.condition,
            "attributes": attributes,
        })

        existing = await db.execute(
            select(ListingDescription).where(ListingDescription.listing_id == listing.id)
        )
        desc = existing.scalar_one_or_none()
        if desc:
            desc.description_html = description_html
        else:
            db.add(ListingDescription(listing_id=listing.id, description_html=description_html))

        listing.status = "ready_to_publish"
        await db.commit()

    return {"listing_id": listing_id}


async def _mark_failed(listing_id: str) -> None:
    from app.database import worker_session
    from app.models.listing import Listing
    from sqlalchemy import select

    async with worker_session() as db:
        listing = (await db.execute(select(Listing).where(Listing.id == listing_id))).scalar_one_or_none()
        if listing and listing.status != "failed":
            listing.failed_step = listing.status  # capture column for UI routing
            listing.status = "failed"
            await db.commit()


@celery_app.task(name="app.workers.tasks.ai_tasks.generate_title", bind=True, max_retries=3)
def generate_title(
    self,
    listing_id: str,
    batch_mode: bool = False,
    ean: str | None = None,
    seo_context: str | None = None,
) -> dict:
    try:
        return asyncio.run(_generate_title_async(listing_id, batch_mode=batch_mode, ean=ean, seo_context=seo_context))
    except Exception as exc:
        if self.request.retries >= self.max_retries:
            asyncio.run(_mark_failed(listing_id))
            raise
        raise self.retry(exc=exc, countdown=2 ** self.request.retries * 5)


@celery_app.task(name="app.workers.tasks.ai_tasks.generate_description", bind=True, max_retries=3)
def generate_description(self, listing_id: str) -> dict:
    try:
        return asyncio.run(_generate_description_async(listing_id))
    except Exception as exc:
        if self.request.retries >= self.max_retries:
            asyncio.run(_mark_failed(listing_id))
            raise
        raise self.retry(exc=exc, countdown=2 ** self.request.retries * 5)
