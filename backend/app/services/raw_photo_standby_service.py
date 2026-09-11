"""Standby por falta de foto bruta: `pending_raw_photos`.

Quando `_try_i2i_generation` nao encontra as `RAW_PHOTOS_MIN` fotos
obrigatorias (`{sku}-1` e `{sku}-2`, em qualquer das `RAW_PHOTO_EXTENSIONS`:
jpg, png ou webp) no bucket do seller, o listing NAO cai em nenhum fallback de geracao: entra em
`pending_raw_photos` e espera. Antes existia um caminho texto-imagem (prompt
do LLM + motor gerando do zero) que em lote auto-aprovava e publicava um
anuncio com imagem inventada — removido em 2026-09-10.

Duas retomadas, uma so logica:
- automatica: `raw_photo_tasks.check_pending_raw_photos`, no beat a cada 15
  min, chama `try_resume_raw_photos` para cada listing em espera;
- manual: `ListingService.resume_raw_photos`, sob demanda, mesma funcao.

A sondagem do bucket e' a que ja existe (`fetch_raw_photos`), e a reentrada
no pipeline e' pelo mesmo ponto de sempre: `generate_images.delay`, igual
para lote e manual — publicar continua sendo sempre acao humana
(`trigger_publish` / `bulk_publish`).
"""
import logging

from sqlalchemy import select, update as sa_update

from app.models.listing import Listing
from app.models.seller_image_config import SellerImageConfig
from app.services.seller_image_source_service import (
    RAW_PHOTO_EXTENSIONS,
    RAW_PHOTOS_MIN,
    fetch_raw_photos,
)

logger = logging.getLogger(__name__)

PENDING_RAW_PHOTOS = "pending_raw_photos"


def _enumerar(itens: list[str], conector: str) -> str:
    """'a, b e c' / 'a, b ou c' — lista legivel em portugues."""
    if len(itens) == 1:
        return itens[0]
    return ", ".join(itens[:-1]) + f" {conector} " + itens[-1]


def missing_photos_message(sku: str) -> str:
    # Derivada das constantes, nunca texto fixo: se RAW_PHOTOS_MIN ou
    # RAW_PHOTO_EXTENSIONS mudarem, a mensagem que o operador le acompanha.
    obrigatorias = _enumerar([f"{sku}-{n}" for n in range(1, RAW_PHOTOS_MIN + 1)], "e")
    formatos = _enumerar([f".{ext}" for ext in RAW_PHOTO_EXTENSIONS], "ou")
    return (
        f"Fotos brutas do SKU {sku} não encontradas no bucket do seller "
        f"(obrigatórias: {obrigatorias}, em {formatos}). O sistema verifica de novo a cada 15 minutos; "
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

    Lote e manual pausam em cada etapa por design, esperando aprovacao
    humana (imagens, depois publicacao): um `.delay()` avulso basta para os
    dois — nao ha mais chain nem despacho automatico de
    generate_description/publish_listing por este caminho.
    """
    from app.workers.tasks.image_tasks import generate_images

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
