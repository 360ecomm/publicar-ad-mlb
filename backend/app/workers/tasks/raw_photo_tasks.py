"""Tarefa periodica do beat: retoma listings em `pending_raw_photos`.

A cada 15 minutos (ver `beat_schedule` em `celery_app.py`) percorre os
listings em espera e chama `try_resume_raw_photos` para cada um: quem ja
tem as fotos no bucket volta para `generating_images` e reentra na chain;
quem nao tem continua esperando. Nunca levanta por causa de um listing:
falha de um vira log e nao derruba a varredura dos outros.
"""
import asyncio
import logging

from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


async def _check_pending_raw_photos_async() -> dict:
    from sqlalchemy import select

    from app.database import worker_session
    from app.models.listing import Listing
    from app.services import raw_photo_standby_service as standby

    checked = resumed = 0
    async with worker_session() as db:
        pendentes = (
            await db.execute(
                select(Listing).where(Listing.status == standby.PENDING_RAW_PHOTOS)
            )
        ).scalars().all()
        for listing in pendentes:
            checked += 1
            try:
                if await standby.try_resume_raw_photos(db, listing):
                    resumed += 1
            except Exception:
                logger.exception(
                    "raw_photos_standby listing_id=%s result=erro_na_verificacao", listing.id
                )
    logger.info("raw_photos_standby varredura checked=%s resumed=%s", checked, resumed)
    return {"checked": checked, "resumed": resumed}


@celery_app.task(name="app.workers.tasks.raw_photo_tasks.check_pending_raw_photos")
def check_pending_raw_photos() -> dict:
    return asyncio.run(_check_pending_raw_photos_async())
