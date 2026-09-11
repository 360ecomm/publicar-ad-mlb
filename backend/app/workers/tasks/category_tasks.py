import asyncio
from app.workers.celery_app import celery_app


async def _predict_category_async(listing_id: str, ean: str | None = None) -> dict:
    from app.database import worker_session
    from app.models.listing import Listing
    from app.services.category_service import CategoryService
    from sqlalchemy import select

    async with worker_session() as db:
        result = await db.execute(select(Listing).where(Listing.id == listing_id))
        listing = result.scalar_one()

        # O EAN vive em `products.ean` desde o cadastro, mas NINGUEM o passava:
        # `select_title` e os gatilhos batch despacham
        # `predict_category.delay(listing_id)` sem argumento, e o default era
        # None — entao o prefill de GTIN nunca acontecia e o atributo, que e
        # obrigatorio em varias categorias, nascia vazio. No SKU 37 isso passou
        # despercebido porque o GTIN foi preenchido a mao na Fase 5c.
        #
        # Fallback, nao sobrescrita: quem chamar informando o EAN continua
        # mandando o dele.
        if ean is None and listing.product_id:
            from app.models.product import Product

            product = (
                await db.execute(select(Product).where(Product.id == listing.product_id))
            ).scalar_one_or_none()
            if product is not None:
                ean = product.ean

        service = CategoryService(db)
        await service.predict_and_save(listing, ean=ean)
        await db.commit()

        # Batch: avança automaticamente para geração de imagens sem esperar
        # aprovação humana. Publicação em lote nunca acontece sozinha: é
        # sempre ação humana (trigger_publish / bulk_publish).
        if listing.created_via == "batch" and listing.status == "pending_description":
            from sqlalchemy import update as sa_update
            result = await db.execute(
                sa_update(Listing)
                .where(
                    Listing.id == listing_id,
                    Listing.status == "pending_description",
                    Listing.created_via == "batch",
                )
                .values(status="generating_images")
                .execution_options(synchronize_session=False)
            )
            await db.commit()
            if result.rowcount == 1:
                from app.workers.tasks.image_tasks import generate_images
                generate_images.delay(listing_id)

    return {"listing_id": listing_id, "category_id": listing.ml_category_id}


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


@celery_app.task(name="app.workers.tasks.category_tasks.predict_category", bind=True, max_retries=3)
def predict_category(self, listing_id: str, ean: str | None = None) -> dict:
    try:
        return asyncio.run(_predict_category_async(listing_id, ean=ean))
    except Exception as exc:
        if self.request.retries >= self.max_retries:
            asyncio.run(_mark_failed(listing_id))
            raise
        raise self.retry(exc=exc, countdown=2 ** self.request.retries * 5)
