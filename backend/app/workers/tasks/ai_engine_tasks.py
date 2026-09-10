"""Tarefa periodica do beat: retoma listings em `pending_ai_engine`.

A cada 15 minutos redispara a geracao de quem parou por motor de IA
indisponivel. Sem pre-checagem (a OpenAI nao expoe saldo): se o motor
continuar fora, o worker devolve o listing ao standby com no maximo 2
chamadas que falham rapido. Falha de um listing vira log, nao derruba a
varredura.
"""
import asyncio
import logging

from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


async def _check_pending_ai_engine_async() -> dict:
    from sqlalchemy import select

    from app.database import worker_session
    from app.models.listing import Listing
    from app.services import ai_engine_standby_service as standby

    checked = resumed = 0
    async with worker_session() as db:
        pendentes = (
            await db.execute(select(Listing).where(Listing.status == standby.PENDING_AI_ENGINE))
        ).scalars().all()
        for listing in pendentes:
            checked += 1
            try:
                if await standby.try_resume_ai_engine(db, listing):
                    resumed += 1
            except Exception:
                logger.exception("ai_engine_standby listing_id=%s result=erro_na_retomada", listing.id)
    logger.info("ai_engine_standby varredura checked=%s resumed=%s", checked, resumed)
    return {"checked": checked, "resumed": resumed}


@celery_app.task(name="app.workers.tasks.ai_engine_tasks.check_pending_ai_engine")
def check_pending_ai_engine() -> dict:
    return asyncio.run(_check_pending_ai_engine_async())
