"""Standby por falta de foto bruta: `pending_raw_photos`.

Quando `_try_i2i_generation` nao encontra `{sku}-1.jpg` e `{sku}-2.jpg` no
bucket do seller, o listing NAO cai em nenhum fallback de geracao: entra em
`pending_raw_photos` e espera. Antes existia um caminho texto-imagem (prompt
do LLM + motor gerando do zero) que em lote auto-aprovava e publicava um
anuncio com imagem inventada — removido em 2026-09-10.

Duas retomadas, uma so logica:
- automatica: `raw_photo_tasks.check_pending_raw_photos`, no beat a cada 15
  min, chama `try_resume_raw_photos` para cada listing em espera;
- manual: `ListingService.resume_raw_photos`, sob demanda, mesma funcao.

A sondagem do bucket e' a que ja existe (`fetch_raw_photos`), e a reentrada
no pipeline e' pelo mesmo ponto de sempre: `generate_images`, em chain
completa quando o anuncio e' de lote.
"""
import logging

from celery import chain as celery_chain
from sqlalchemy import select, update as sa_update

from app.models.listing import Listing
from app.models.seller_image_config import SellerImageConfig
from app.services.seller_image_source_service import RAW_PHOTOS_MIN, fetch_raw_photos

logger = logging.getLogger(__name__)

PENDING_RAW_PHOTOS = "pending_raw_photos"


def missing_photos_message(sku: str) -> str:
    obrigatorias = ", ".join(f"{sku}-{n}.jpg" for n in range(1, RAW_PHOTOS_MIN + 1))
    return (
        f"Fotos brutas do SKU {sku} não encontradas no bucket do seller "
        f"(obrigatórias: {obrigatorias}). O sistema verifica de novo a cada 15 minutos; "
        "ou use 'Verificar fotos agora' depois de subir os arquivos."
    )


async def raw_photos_available(db, listing: Listing) -> bool:
    """True se o seller tem bucket configurado e o SKU tem o minimo de fotos."""
    config = (
        await db.execute(
            select(SellerImageConfig).where(SellerImageConfig.seller_id == listing.seller_id)
        )
    ).scalar_one_or_none()
    if config is None or not listing.sku_external_id:
        return False
    photos = await fetch_raw_photos(config.raw_base_url, listing.sku_external_id)
    return photos is not None


def dispatch_image_generation(listing: Listing) -> None:
    """Reentra no pipeline pelo ponto de sempre.

    Lote precisa da chain completa (imagens → descricao → publicacao) para o
    pipeline seguir sozinho, igual ao dispatch original do `category_tasks`.
    Manual pausa em cada etapa por design: um `.delay()` avulso basta.
    """
    from app.workers.tasks.image_tasks import generate_images

    if listing.created_via == "batch":
        from app.workers.tasks.ai_tasks import generate_description
        from app.workers.tasks.publish_tasks import publish_listing

        celery_chain(
            generate_images.si(str(listing.id)),
            generate_description.si(str(listing.id)),
            publish_listing.si(str(listing.id)),
        ).delay()
    else:
        generate_images.delay(str(listing.id))


async def try_resume_raw_photos(db, listing: Listing) -> bool:
    """Retoma se as fotos ja existem. Devolve True se despachou.

    UPDATE atomico `pending_raw_photos → generating_images`: beat e endpoint
    manual podem colidir, e so quem mudou a linha despacha — mesmo padrao do
    dispatch em lote do `category_tasks`.
    """
    if not await raw_photos_available(db, listing):
        logger.info("raw_photos_standby listing_id=%s sku=%s result=ainda_sem_fotos",
                    listing.id, listing.sku_external_id)
        return False

    result = await db.execute(
        sa_update(Listing)
        .where(Listing.id == listing.id, Listing.status == PENDING_RAW_PHOTOS)
        .values(status="generating_images", error_message=None)
        .execution_options(synchronize_session=False)
    )
    await db.commit()
    if result.rowcount != 1:
        logger.info("raw_photos_standby listing_id=%s result=perdeu_a_corrida", listing.id)
        return False

    listing.status = "generating_images"
    listing.error_message = None
    dispatch_image_generation(listing)
    logger.info("raw_photos_standby listing_id=%s sku=%s result=retomado",
                listing.id, listing.sku_external_id)
    return True
