"""Standby por motor de IA indisponivel: `pending_ai_engine`.

Credito OpenAI esgotado, chave invalida (401/403), 5xx persistente ou
timeout: `OpenAIEditEngine` levanta `ImageEngineUnavailableError`, a geracao
das 5 posicoes aborta inteira (ver `_tentar` em image_tasks) e o worker poe
o listing aqui, com a mensagem real do motor, em vez de `failed` generico.

Nao ha pre-checagem possivel: a API da OpenAI nao expoe saldo, e listar
modelos responde 200 mesmo sem credito. Tentar E' a checagem — por isso a
retomada (beat a cada 15 min, ou endpoint manual) simplesmente redispara a
geracao; se o motor continuar fora, o worker volta a por o listing aqui,
com no maximo 2 chamadas que falham rapido por ciclo.

Mesma mecanica de `raw_photo_standby_service`: UPDATE atomico + reentrada
pelo ponto de sempre (`dispatch_image_generation`).
"""
import logging

from sqlalchemy import update as sa_update

from app.models.listing import Listing
from app.services.raw_photo_standby_service import dispatch_image_generation

logger = logging.getLogger(__name__)

PENDING_AI_ENGINE = "pending_ai_engine"


def engine_error_message(exc: Exception) -> str:
    return (
        "Motor de imagem (OpenAI) indisponível — crédito esgotado, chave inválida ou "
        f"instabilidade: {str(exc)[:300]}. O sistema tenta de novo a cada 15 minutos; "
        "ou use 'Tentar agora' depois de recarregar o crédito."
    )


async def try_resume_ai_engine(db, listing: Listing) -> bool:
    """Redispara a geracao. Devolve True se despachou (venceu a corrida)."""
    result = await db.execute(
        sa_update(Listing)
        .where(Listing.id == listing.id, Listing.status == PENDING_AI_ENGINE)
        .values(status="generating_images", error_message=None)
        .execution_options(synchronize_session=False)
    )
    await db.commit()
    if result.rowcount != 1:
        logger.info("ai_engine_standby listing_id=%s result=perdeu_a_corrida", listing.id)
        return False

    listing.status = "generating_images"
    listing.error_message = None
    dispatch_image_generation(listing)
    logger.info("ai_engine_standby listing_id=%s sku=%s result=retomado",
                listing.id, listing.sku_external_id)
    return True
