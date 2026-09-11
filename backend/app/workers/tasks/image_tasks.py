import asyncio
import logging

from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


async def _fetch_upload_token(seller, db) -> str:
    from app.services.publish_service import get_valid_access_token
    return await get_valid_access_token(seller, db)


def _prepare_image_for_upload(image_bytes: bytes, requires_white_bg: bool):
    """Padroniza para 1200x1200 e roda o QA do ML antes do upload.

    Devolve `(bytes_prontos, veredito)`. Se o veredito reprovar, os bytes vêm
    None e o chamador registra a linha em listing_images sem subir nada.
    """
    from app.services.image_postprocess_service import normalize_to_square
    from app.services.image_service import ImageValidationResult, validate_image

    normalized = normalize_to_square(image_bytes)
    if normalized is None:
        return None, ImageValidationResult(
            is_valid=False, errors=["bytes não são uma imagem válida"]
        )

    verdict = validate_image(normalized, category_requires_white_bg=requires_white_bg)
    if not verdict.is_valid:
        return None, verdict
    return normalized, verdict


async def _resolve_requires_white_bg(listing) -> bool:
    """Se a categoria-raiz do anúncio exige fundo branco puro na capa."""
    from app.services.category_service import category_requires_white_background
    return await category_requires_white_background(listing.ml_category_id)


async def _try_i2i_generation(db, listing, seller, access_token: str) -> int | None:
    """Gera as imagens a partir das fotos brutas reais do seller.

    Devolve None se o seller nao tiver SellerImageConfig ou faltar foto bruta
    obrigatoria — o chamador poe o listing em `pending_raw_photos`. Do
    contrario, roteia para o esquema de 5 posicoes com o perfil da categoria
    (`profile_for_category` nunca devolve None: categoria sem perfil proprio
    usa `PERFIL_PADRAO`).

    Aqui existiu, ate 2026-09-10, um segundo caminho — individuais por foto
    (2 variantes cada), capa composta para kit, capa deterministica
    persistida e 3 cards Pillow — que era o destino de toda categoria sem
    perfil e que em LOTE auto-aprovava e publicava. Removido por completo, nao
    deixado dormente. O ramo de kit (`len(skus) > 1`) foi junto: era
    inalcancavel, `resolve_listing_skus` sempre devolve 1 SKU.
    """
    from sqlalchemy import select

    from app.models.seller_image_config import SellerImageConfig
    from app.services.image_position_profiles import profile_for_category
    from app.services.seller_image_source_service import (
        fetch_all_raw_photos,
        resolve_listing_skus,
    )

    config = (
        await db.execute(
            select(SellerImageConfig).where(SellerImageConfig.seller_id == listing.seller_id)
        )
    ).scalar_one_or_none()
    if config is None:
        return None

    skus = await resolve_listing_skus(listing)
    if not skus:
        return None

    raw_photos_by_sku = await fetch_all_raw_photos(config.raw_base_url, skus)
    if raw_photos_by_sku is None:
        return None

    if len(skus) != 1:
        raise RuntimeError(
            f"anuncio com {len(skus)} SKUs nao e suportado pelo esquema de 5 posicoes"
        )

    profile = profile_for_category(listing.ml_category_id)
    logger.info(
        "roteamento listing_id=%s categoria=%s perfil=%s caminho=cinco_posicoes",
        listing.id, listing.ml_category_id, profile.nome,
    )
    return await _gerar_cinco_posicoes(
        db, listing, access_token, profile, raw_photos_by_sku[skus[0]], skus[0]
    )


async def _generate_images_async(listing_id: str) -> dict:
    from sqlalchemy import select

    from app.database import worker_session
    from app.models.listing import Listing
    from app.models.seller import Seller

    async with worker_session() as db:
        listing = (
            await db.execute(select(Listing).where(Listing.id == listing_id))
        ).scalar_one()

        # Guard de idempotência: se o status já avançou (retry ou dispatch duplo), abortar.
        if listing.status != "generating_images":
            return {"listing_id": listing_id, "skipped": True}

        # Toda chamada de IA desta task (posicoes, card copy, prompt de imagem,
        # texto-imagem) carrega o listing/SKU na linha `ai_cost`.
        from app.services.ai.cost_log import set_cost_context
        set_cost_context(listing_id=listing.id, sku=listing.sku_external_id)

        sku = listing.sku_external_id or ""

        # Aqui existia um atalho: se o (seller, sku) ja tivesse `ProductImage`
        # aprovada de outro anuncio, os `ml_picture_id` eram copiados para
        # este listing, marcados `approved=True`, e o lote pulava direto para
        # `generating_description` — sem gerar nada, sem o esquema de 5
        # posicoes e sem o guard de revisao humana. Removido em 2026-09-10:
        # todo listing SEMPRE gera as suas imagens. O indice SKU→imagem
        # (`ProductImage`) nao e mais lido nem escrito desde entao; a tabela
        # fica so como registro historico dos SKUs 37/38.
        seller = (
            await db.execute(select(Seller).where(Seller.id == listing.seller_id))
        ).scalar_one()
        access_token = await _fetch_upload_token(seller, db)

        from app.services.image_engines.base import ImageEngineUnavailableError

        try:
            i2i_saved = await _try_i2i_generation(db, listing, seller, access_token)
        except ImageEngineUnavailableError as exc:
            # Motor de IA fora (credito OpenAI esgotado, chave invalida, 5xx
            # persistente, timeout): STANDBY dedicado, nao `failed` generico.
            # O rollback descarta as posicoes que esta tentativa ja tinha
            # gravado na sessao — a retomada regenera as 5 do zero, em vez de
            # deixar galeria parcial ou duplicar posicoes. Depois do rollback o
            # objeto expira; recarrega antes de escrever.
            from app.services.ai_engine_standby_service import (
                PENDING_AI_ENGINE,
                engine_error_message,
            )

            await db.rollback()
            listing = (
                await db.execute(select(Listing).where(Listing.id == listing_id))
            ).scalar_one()
            listing.status = PENDING_AI_ENGINE
            listing.error_message = engine_error_message(exc)
            await db.commit()
            logger.warning(
                "ai_engine_standby listing_id=%s sku=%s result=motor_indisponivel reason=%s",
                listing.id, sku, exc,
            )
            return {"listing_id": listing_id, "pending_ai_engine": True}

        if i2i_saved is not None:
            if i2i_saved == 0:
                raise RuntimeError("Nenhuma imagem válida foi gerada pelo motor 'openai_edit'")
            # Revisao humana SEMPRE, em qualquer categoria e tambem em lote:
            # todas as posicoes nascem approved=False e o anuncio para aqui.
            # A auto-aprovacao em lote que existia para o caminho antigo
            # (individuais + cards) saiu com ele em 2026-09-10.
            listing.status = "pending_image_approval"
            await db.commit()
            return {"listing_id": listing_id, "images_saved": i2i_saved, "source": "i2i"}

        # Sem foto bruta no bucket: STANDBY, nunca fallback. Aqui existia o
        # caminho texto-imagem (prompt do LLM + motor gerando do zero), que em
        # lote auto-aprovava e publicava anuncio com imagem inventada.
        # Removido em 2026-09-10 junto com o subsistema de "motor de imagem"
        # (ImageEngineState, pending_image_engine_confirmation, endpoint de
        # confirmacao), que so existia para servir esse caminho.
        #
        # O status nao e' `generating_description` nem `publishing`: nada e'
        # enfileirado depois deste ponto, nem em lote. O proximo passo e' a
        # retomada (beat a cada 15 min, ou endpoint manual), que reentra por aqui.
        from app.services.raw_photo_standby_service import (
            PENDING_RAW_PHOTOS,
            missing_photos_message,
        )

        listing.status = PENDING_RAW_PHOTOS
        listing.error_message = missing_photos_message(sku or "?")
        await db.commit()
        logger.warning(
            "raw_photos_standby listing_id=%s sku=%s result=aguardando_fotos",
            listing.id, sku,
        )
        return {"listing_id": listing_id, "pending_raw_photos": True}


async def _mark_failed(listing_id: str, error: str) -> None:
    import logging
    logger = logging.getLogger(__name__)
    try:
        from app.database import worker_session
        from app.models.listing import Listing
        from sqlalchemy import select
        async with worker_session() as db:
            listing = (
                await db.execute(select(Listing).where(Listing.id == listing_id))
            ).scalar_one_or_none()
            if listing and listing.status != "failed":
                listing.failed_step = listing.status  # capture column for UI routing
                listing.status = "failed"
                listing.error_message = error[:500]
                await db.commit()
    except Exception as mark_exc:
        logger.error(
            "Could not mark listing %s as failed (original error: %s): %s",
            listing_id,
            error,
            mark_exc,
        )


@celery_app.task(name="app.workers.tasks.image_tasks.generate_images", bind=True, max_retries=2)
def generate_images(self, listing_id: str) -> dict:
    try:
        return asyncio.run(_generate_images_async(listing_id))
    except Exception as exc:
        if self.request.retries >= self.max_retries:
            asyncio.run(_mark_failed(listing_id, str(exc)))
            raise
        raise self.retry(exc=exc, countdown=2 ** self.request.retries * 5)  # 5s, 10s


@celery_app.task(name="app.workers.tasks.image_tasks.upload_images_to_ml", bind=True, max_retries=3)
def upload_images_to_ml(self, listing_id: str) -> dict:
    raise NotImplementedError("Use generate_images task")


# ---------------------------------------------------------------------------
# Esquema de 5 posicoes — padrao para anuncio de PRODUTO UNICO em categoria
# com perfil cadastrado. Ver docs/superpowers/specs/esquema-5-posicoes.md.
# ---------------------------------------------------------------------------

POSITION_KIND_PRESENTATION = "presentation"
POSITION_KIND_BENEFITS = "benefits_ai"
POSITION_KIND_DETAIL = "detail_ai"

_TENTATIVAS_POR_POSICAO = 2


async def _tentar(descricao: str, listing_id, fabrica, tentativas: int = _TENTATIVAS_POR_POSICAO):
    """Roda `fabrica()` ate `tentativas` vezes; devolve None se todas falharem.

    Cada posicao e independente: uma que falha nao pode derrubar as outras nem
    o anuncio — mesmo padrao dos antigos cards Pillow. O retry
    existe porque a falha tipica do motor e transiente (timeout, 5xx), e
    perder uma posicao inteira por isso seria caro.
    """
    from app.services.image_engines.base import ImageEngineUnavailableError

    for tentativa in range(1, tentativas + 1):
        try:
            return await fabrica()
        except Exception as exc:
            logger.warning(
                "posicao_falhou listing_id=%s posicao=%s tentativa=%s/%s reason=%s",
                listing_id, descricao, tentativa, tentativas, exc,
                exc_info=(tentativa == tentativas),
            )
            # Motor indisponivel (credito esgotado, 401/403, 5xx persistente,
            # timeout) na ULTIMA tentativa: nao e' falha desta posicao, e' do
            # motor — as outras vao falhar igual. Sobe para abortar a geracao
            # inteira e por o listing em `pending_ai_engine`. Antes, isto era
            # engolido: 10 chamadas pagas-que-falham por tentativa, e se o
            # credito acabasse no meio o anuncio seguia com galeria parcial.
            if tentativa == tentativas and isinstance(exc, ImageEngineUnavailableError):
                raise
    return None


async def _campos_das_posicoes(db, listing):
    """Textos das posicoes 2, 3 e 5, todos de fontes ja existentes.

    Posicao 2 espelha a hierarquia do ROTULO FISICO: nome do produto em
    destaque, marca abaixo — no frasco, "wepink" e pequeno e "FATAL BLACK" e
    grande. Nao se inventa hierarquia nova quando a embalagem ja resolveu.
    """
    from sqlalchemy import select

    from app.models.listing_attribute import ListingAttribute
    from app.services.brand_field import real_brand
    from app.services.image_card_copy_service import build_specs_card, generate_card_copy

    atributos = (await db.execute(
        select(ListingAttribute).where(ListingAttribute.listing_id == listing.id)
    )).scalars().all()

    volume = next(
        (a.value_name for a in atributos if a.attribute_id == "UNIT_VOLUME" and a.value_name),
        None,
    )
    cards = await generate_card_copy(listing, atributos)
    beneficios = next((c for c in cards if c.kind == "card_benefits"), None)
    ficha = build_specs_card(atributos)

    return {
        "nome": (listing.sku_model or listing.sku_description or "").strip(),
        # `real_brand`: placeholder ("Sem marca") vira None e a linha 2 da
        # apresentacao e' OMITIDA — no SKU 45 ela saiu impressa na imagem.
        "marca": real_brand(listing.sku_brand),
        "volume": volume,
        "beneficios": beneficios,
        "ficha": ficha,
    }


async def _salvar_posicao(db, listing, sku, kind, sort_order, gerado, access_token,
                          requires_white_bg: bool):
    """QA + upload + linha nao aprovada. Devolve True se subiu.

    `approved=False` SEMPRE: revisao humana antes de publicar e obrigatoria em
    todas as 5 posicoes, sem excecao. Reprovada no QA, guarda os bytes do que
    a IA produziu — um candidato existe para alguem julgar.
    """
    from app.models.listing_image import ListingImage
    from app.services.image_service import MLPictureService
    from app.services.r2_asset_service import store_candidate_bytes

    preparado, veredito = _prepare_image_for_upload(
        gerado, requires_white_bg=requires_white_bg
    )
    if preparado is None:
        # Bytes crus do que a IA produziu vao ao R2 mesmo reprovados: um
        # candidato existe para alguem julgar. NUNCA no banco.
        asset_key = await store_candidate_bytes(
            gerado, db=db, seller_id=listing.seller_id, sku=sku, kind=kind
        )
        db.add(ListingImage(
            listing_id=listing.id, status="validation_failed",
            validation_error=veredito.reason, approved=False,
            sort_order=sort_order, kind=kind, source_sku=sku, asset_key=asset_key,
        ))
        logger.warning(
            "posicao_reprovada listing_id=%s kind=%s reason=%s",
            listing.id, kind, veredito.reason,
        )
        return False

    ml_picture_id = await MLPictureService().upload(preparado, access_token)
    # Write-back no R2 no MESMO momento do upload ao ML: os bytes exatos que
    # foram para o CDN, para variantes por IA partirem do arquivo publicado.
    asset_key = await store_candidate_bytes(
        preparado, db=db, seller_id=listing.seller_id, sku=sku, kind=kind
    )
    db.add(ListingImage(
        listing_id=listing.id, ml_picture_id=ml_picture_id, status="uploaded",
        approved=False, sort_order=sort_order, kind=kind, source_sku=sku,
        asset_key=asset_key,
    ))
    return True


async def _gerar_cinco_posicoes(db, listing, access_token, profile, fotos, sku) -> int:
    """As 5 posicoes do esquema, cada uma independente. Devolve quantas subiram.

    E o unico caminho de imagens, em toda categoria (perfil proprio so em
    MLB6284; as demais usam `PERFIL_PADRAO`). Substituiu o modelo antigo de
    "N variantes por foto": cada posicao 2-4 e UMA chamada de edicao que pode
    referenciar TODAS as fotos brutas do SKU, entao nao ha corte
    `[:RAW_PHOTOS_MIN]` aqui.

    A capa DETERMINISTICA e calculada mas NAO vira linha visivel: serve de
    base para as posicoes 1 e 5 e so e persistida se a posicao 1 por IA
    falhar por completo — ai ela assume a capa como fallback, em vez de o
    anuncio ficar sem imagem nenhuma na posicao mais importante.
    """
    from app.services.cover_variant_service import _pick_prompt
    from app.services.image_deterministic_service import try_deterministic_cover
    from app.services.image_engines.openai_edit_engine import OpenAIEditEngine
    from app.services.image_position_prompts import (
        build_benefits_prompt,
        build_detail_prompt,
        build_presentation_prompt,
    )
    from app.services.image_position_profiles import detail_caption_for
    from app.services.seller_image_source_service import pick_detail_source
    from app.services.specs_variant_service import _build_specs_prompt

    engine = OpenAIEditEngine()
    canvas = profile.canvas
    campos = await _campos_das_posicoes(db, listing)
    salvas = 0

    # Base deterministica: recorte do pixel original, sem IA — o rotulo nela e
    # sempre fiel, e e por isso que as posicoes 1 e 5 partem dela.
    cover_bytes = try_deterministic_cover(fotos[0])
    base, _ = (
        _prepare_image_for_upload(cover_bytes, requires_white_bg=True)
        if cover_bytes is not None else (None, None)
    )
    logger.info(
        "cinco_posicoes listing_id=%s sku=%s capa_deterministica=%s",
        listing.id, sku, "hit" if base is not None else "miss",
    )
    base_ia = base if base is not None else fotos[0]

    # Posicao 1 — capa por IA, sempre branca (ver `_pick_prompt`).
    async def _pos1():
        return (await engine.edit(images=[base_ia], prompt=_pick_prompt(), n=1, size=canvas))[0]

    gerado = await _tentar("1-capa", listing.id, _pos1)
    if gerado is not None and await _salvar_posicao(
        db, listing, sku, "cover_ai", 0, gerado, access_token, requires_white_bg=True
    ):
        salvas += 1
    elif base is not None:
        # Fallback interno: a capa deterministica so aparece quando a IA falha.
        from app.models.listing_image import ListingImage
        from app.services.image_service import MLPictureService
        from app.services.r2_asset_service import store_candidate_bytes

        ml_picture_id = await MLPictureService().upload(base, access_token)
        asset_key = await store_candidate_bytes(
            base, db=db, seller_id=listing.seller_id, sku=sku, kind="cover_deterministic"
        )
        db.add(ListingImage(
            listing_id=listing.id, ml_picture_id=ml_picture_id, status="uploaded",
            approved=False, sort_order=0, kind="cover_deterministic",
            source_sku=sku, asset_key=asset_key,
        ))
        salvas += 1
        logger.warning("cinco_posicoes listing_id=%s posicao=1 usou_fallback_deterministico", listing.id)

    # Posicao 2 — apresentacao. Unica que recebe TODAS as fotos brutas.
    if campos["nome"]:
        prompt2 = build_presentation_prompt(campos["nome"], campos["marca"], campos["volume"])

        async def _pos2():
            return (await engine.edit(images=fotos, prompt=prompt2, n=1, size=canvas))[0]

        gerado = await _tentar("2-apresentacao", listing.id, _pos2)
        if gerado is not None and await _salvar_posicao(
            db, listing, sku, POSITION_KIND_PRESENTATION, 1, gerado, access_token,
            requires_white_bg=False,
        ):
            salvas += 1

    # Posicao 3 — beneficios. Copy do LLM, a mesma ja usada no card Pillow.
    beneficios = campos["beneficios"]
    if beneficios is not None:
        prompt3 = build_benefits_prompt(beneficios.title, beneficios.bullets)

        async def _pos3():
            return (await engine.edit(images=[fotos[0]], prompt=prompt3, n=1, size=canvas))[0]

        gerado = await _tentar("3-beneficios", listing.id, _pos3)
        if gerado is not None and await _salvar_posicao(
            db, listing, sku, POSITION_KIND_BENEFITS, 2, gerado, access_token,
            requires_white_bg=False,
        ):
            salvas += 1

    # Posicao 4 — detalhe. `pick_detail_source` escolhe a 3a foto se existir.
    foto_detalhe, veio_de_extra = pick_detail_source(fotos)
    legenda = detail_caption_for(profile, sku)
    prompt4 = build_detail_prompt(legenda)
    logger.info(
        "cinco_posicoes listing_id=%s posicao=4 fonte=%s legenda=%r",
        listing.id, "extra" if veio_de_extra else "reuso_do_minimo", legenda,
    )

    async def _pos4():
        return (await engine.edit(images=[foto_detalhe], prompt=prompt4, n=1, size=canvas))[0]

    gerado = await _tentar("4-detalhe", listing.id, _pos4)
    if gerado is not None and await _salvar_posicao(
        db, listing, sku, POSITION_KIND_DETAIL, 3, gerado, access_token,
        requires_white_bg=False,
    ):
        salvas += 1

    # Posicao 5 — ficha tecnica. Bullets ancorados no value_name real.
    ficha = campos["ficha"]
    if ficha is not None:
        prompt5 = _build_specs_prompt(ficha.bullets)

        async def _pos5():
            return (await engine.edit(images=[base_ia], prompt=prompt5, n=1, size=canvas))[0]

        gerado = await _tentar("5-ficha", listing.id, _pos5)
        if gerado is not None and await _salvar_posicao(
            db, listing, sku, "specs_ai", 4, gerado, access_token, requires_white_bg=False
        ):
            salvas += 1

    await db.commit()
    logger.info("cinco_posicoes listing_id=%s sku=%s salvas=%s", listing.id, sku, salvas)
    return salvas
