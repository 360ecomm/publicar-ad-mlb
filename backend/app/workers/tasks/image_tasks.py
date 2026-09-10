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


async def _append_benefit_cards(
    db, listing, access_token: str, base_photo: bytes, source_sku: str, start_sort_order: int
) -> int:
    """Gera os 3 cards de texto a partir da 1a foto individual bem-sucedida.

    Devolve quantos cards subiram. Nunca levanta: qualquer falha vira log e
    zero cards — o anúncio não pode cair por causa de um card.
    """
    from sqlalchemy import select

    from app.models.listing_attribute import ListingAttribute
    from app.models.listing_image import ListingImage
    from app.services.image_benefit_card_service import render_benefit_card
    from app.services.image_card_copy_service import generate_card_copy
    from app.services.image_service import MLPictureService

    try:
        # Query própria: tocar `listing.attributes` (relacionamento lazy) aqui
        # dentro levantaria MissingGreenlet — ver CLAUDE.md.
        attributes = (
            await db.execute(
                select(ListingAttribute).where(ListingAttribute.listing_id == listing.id)
            )
        ).scalars().all()
        cards = await generate_card_copy(listing, attributes)
    except Exception as exc:
        # exc_info: este log e o UNICO sinal de que os cards pararam de sair —
        # o passo inteiro e engolido de proposito. Sem o traceback nao da pra
        # separar falha de query, de provider ou de parse.
        logger.warning(
            "benefit_cards listing_id=%s sku=%s result=failed reason=%s",
            listing.id,
            source_sku,
            exc,
            exc_info=True,
        )
        return 0

    ml_pic = MLPictureService()
    saved = 0
    for card in cards:
        try:
            card_bytes = render_benefit_card(base_photo, card.title, card.bullets)
            # Card nunca é capa, então fundo branco puro nunca é exigido dele.
            prepared, verdict = _prepare_image_for_upload(card_bytes, requires_white_bg=False)
            if prepared is None:
                logger.warning(
                    "benefit_cards listing_id=%s sku=%s kind=%s result=rejected reason=%s",
                    listing.id,
                    source_sku,
                    card.kind,
                    verdict.reason,
                )
                continue
            ml_picture_id = await ml_pic.upload(prepared, access_token)
            db.add(ListingImage(
                listing_id=listing.id,
                ml_picture_id=ml_picture_id,
                status="uploaded",
                sort_order=start_sort_order + saved,
                kind=card.kind,
                source_sku=source_sku,
            ))
            saved += 1
        except Exception as exc:
            # Um card que falha não derruba os outros nem as imagens já salvas.
            logger.warning(
                "benefit_cards listing_id=%s sku=%s kind=%s result=failed reason=%s",
                listing.id,
                source_sku,
                card.kind,
                exc,
                exc_info=True,
            )

    logger.info(
        "benefit_cards listing_id=%s sku=%s requested=%s saved=%s",
        listing.id,
        source_sku,
        len(cards),
        saved,
    )
    return saved


async def _try_i2i_generation(db, listing, seller, access_token: str) -> int | None:
    """Tenta o caminho image-to-image (fotos brutas reais do seller). Retorna
    None se o seller não tiver SellerImageConfig ou faltar alguma foto bruta
    — nesses casos o chamador deve cair no texto-imagem existente, inalterado."""
    from sqlalchemy import select
    from app.models.seller_image_config import SellerImageConfig
    from app.models.listing_image import ListingImage
    from app.models.product_image import ProductImage
    from app.services.seller_image_source_service import (
        RAW_PHOTOS_MIN,
        fetch_all_raw_photos,
        resolve_listing_skus,
    )
    from app.services.image_engines.openai_edit_engine import OpenAIEditEngine
    from app.services.image_service import MLPictureService

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

    # ROTEAMENTO: produto unico em categoria-FOLHA com perfil cadastrado vai
    # para o esquema de 5 posicoes. Categoria sem perfil segue o caminho
    # antigo, inalterado — e o que mantem a mudanca contida a perfumaria
    # enquanto as outras verticais nao forem testadas.
    #
    # Kits (`len(skus) > 1`) nunca entram aqui. Aquele ramo continua exatamente
    # como estava; hoje ele e inalcancavel porque `resolve_listing_skus` sempre
    # devolve 1 SKU, mas nao e este trabalho que muda isso.
    from app.services.image_position_profiles import profile_for_category

    profile = profile_for_category(listing.ml_category_id)
    if len(skus) == 1 and profile is not None:
        logger.info(
            "roteamento listing_id=%s categoria=%s perfil=%s caminho=cinco_posicoes",
            listing.id, listing.ml_category_id, profile.nome,
        )
        return await _gerar_cinco_posicoes(
            db, listing, access_token, profile, raw_photos_by_sku[skus[0]], skus[0]
        )

    # A clausula CRITICAL nao e enfeite. O prompt antigo pedia "same shape,
    # color, materials and proportions" e nao dizia nada sobre TEXTO — e o
    # motor tratou o rotulo como textura livre para redesenhar: num teste real
    # o frasco de 100ml saiu marcado "160ml | 3.50 fl.ex", com a marca escrita
    # "weoink" no lugar de "wepink". Volume e marca errados na vitrine sao
    # informacao falsa sobre o produto, nao imperfeicao estetica.
    #
    # Isto e MITIGACAO, nao garantia: o comportamento e do modelo, e continua
    # estocastico. O gate de revisao humana antes da aprovacao segue sendo a
    # protecao real. Ver a limitacao registrada no commit.
    _NO_TEXT_EDIT_RULE = (
        "CRITICAL: do not alter, redraw, translate, correct or re-render ANY "
        "text printed on the product or its packaging. Brand names, product "
        "names, volumes, measurement units, ingredient lists and any other "
        "lettering must be preserved exactly as they appear in the reference "
        "image, character for character. If any text is unreadable, keep it "
        "unreadable rather than inventing plausible text. Never change a "
        "number or a unit of measurement. "
    )

    treatment_prompt = (
        "Professional e-commerce product photo. Pure white background, "
        "studio lighting, product centered and isolated, no text overlay, no "
        "watermark, no people. Keep the exact product from the reference image "
        "— same shape, color, materials and proportions. Only the background, "
        "lighting and framing may change. "
        + _NO_TEXT_EDIT_RULE
    )

    engine = OpenAIEditEngine()
    ml_pic = MLPictureService()
    saved = 0
    # Fundo branco só é exigido na capa (sort_order 0) — as demais imagens
    # podem ter fundo contextual mesmo nas categorias com padronização rígida.
    requires_white_bg = await _resolve_requires_white_bg(listing)

    # Capa composta — só quando o anúncio tem mais de 1 SKU. Falha na
    # composição não afeta as imagens individuais: a capa é simplesmente
    # pulada, e a 1a imagem individual assume a posição de capa por ordem
    # natural do array `pictures` (sort_order=0).
    if len(skus) > 1:
        # `[:RAW_PHOTOS_MIN]` pelo MESMO motivo do laco das individuais mais
        # abaixo: `fetch_all_raw_photos` descobre TODAS as fotos brutas
        # disponiveis do SKU (podem ser 10), e cada foto extra entregue ao
        # motor de edicao e custo de IA por anuncio. O consumo continua preso
        # ao minimo obrigatorio; descobrir mais fotos nunca pode virar gasto
        # automatico. Hoje `resolve_listing_skus` sempre devolve 1 SKU, entao
        # este ramo esta dormente — mas era o unico ponto sem o corte, e num
        # anuncio de kit com 5 SKUs viraria um multiplicador silencioso.
        all_raw_photos = [
            photo for sku in skus for photo in raw_photos_by_sku[sku][:RAW_PHOTOS_MIN]
        ]
        cover_prompt = (
            "Professional e-commerce product photo showing all the items from "
            "the reference images together, composed in a single realistic scene. "
            "Pure white background, studio lighting, items clearly visible and "
            "proportionate to each other, no text overlay, no watermark, no people. "
            + _NO_TEXT_EDIT_RULE
        )
        try:
            cover_variants = await engine.edit(images=all_raw_photos, prompt=cover_prompt, n=1)
        except Exception:
            cover_variants = []

        for img_bytes in cover_variants:
            prepared, verdict = _prepare_image_for_upload(
                img_bytes, requires_white_bg=requires_white_bg and saved == 0
            )
            if prepared is None:
                db.add(ListingImage(
                    listing_id=listing.id,
                    status="validation_failed",
                    validation_error=verdict.reason,
                    sort_order=saved,
                    kind="cover_composed",
                    source_sku=None,
                ))
                continue
            ml_picture_id = await ml_pic.upload(prepared, access_token)
            db.add(ListingImage(
                listing_id=listing.id,
                ml_picture_id=ml_picture_id,
                status="uploaded",
                sort_order=saved,
                kind="cover_composed",
                source_sku=None,
            ))
            saved += 1

    # Capa determinística — só para 1 SKU, e antes do loop pago. Se a foto
    # bruta tiver fundo uniforme, a capa sai por recorte, sem custo de IA. Se
    # não der, `saved` continua 0 e tudo segue exatamente como antes.
    # Bytes da capa deterministica, quando ela sai e passa no QA. E a foto mais
    # confiavel da execucao: recorte do pixel original, sem IA no meio, entao o
    # texto impresso no produto (volume, unidade, marca) e o real.
    cover_prepared_bytes: bytes | None = None

    if len(skus) == 1:
        from app.services.image_deterministic_service import try_deterministic_cover

        only_sku = skus[0]
        cover_bytes = try_deterministic_cover(raw_photos_by_sku[only_sku][0])
        # Sinal binário de acerto/erro para medir a taxa real em produção sem
        # instrumentar o serviço nem persistir nada.
        logger.info(
            "deterministic_cover listing_id=%s seller_id=%s sku=%s result=%s",
            listing.id,
            listing.seller_id,
            only_sku,
            "hit" if cover_bytes is not None else "miss",
        )
        if cover_bytes is not None:
            prepared, verdict = _prepare_image_for_upload(
                cover_bytes, requires_white_bg=requires_white_bg
            )
            if prepared is None:
                db.add(ListingImage(
                    listing_id=listing.id,
                    status="validation_failed",
                    validation_error=verdict.reason,
                    sort_order=saved,
                    kind="cover_deterministic",
                    source_sku=only_sku,
                ))
            else:
                ml_picture_id = await ml_pic.upload(prepared, access_token)
                db.add(ListingImage(
                    listing_id=listing.id,
                    ml_picture_id=ml_picture_id,
                    status="uploaded",
                    sort_order=saved,
                    kind="cover_deterministic",
                    source_sku=only_sku,
                    # Bytes exatos que subiram para o ML — a futura variante de
                    # capa parte deles, nunca de uma re-derivacao. Re-derivar
                    # seria identico enquanto a foto bruta nao mudasse, mas o
                    # seller pode trocar a foto (aconteceu com 37-2.jpg), e ai
                    # a variante sairia de uma imagem diferente da publicada.
                    image_bytes=prepared,
                ))
                db.add(ProductImage(
                    seller_id=listing.seller_id,
                    sku=only_sku,
                    ml_picture_id=ml_picture_id,
                    source="deterministic",
                    is_approved=False,
                ))
                saved += 1
                cover_prepared_bytes = prepared

    # Imagens individuais — sempre, uma chamada de edição por foto bruta.
    first_individual_bytes: bytes | None = None
    for sku in skus:
        # LIMITE DELIBERADO nas 2 primeiras fotos. `fetch_raw_photos` passou a
        # descobrir ate 10 fotos por SKU, mas isso e insumo do esquema de 5
        # posicoes (piloto, ver docs/superpowers/specs/esquema-5-posicoes.md),
        # NAO deste loop.
        #
        # Sem o corte, um seller com 5 fotos geraria 10 individuais em vez de 4:
        # 2.5x o custo de IA, e 1 capa + 10 individuais + 3 cards = 14 imagens,
        # acima do teto de 12 do ML. Este loop e o pipeline de producao ja
        # testado e publicando — ele nao muda de comportamento.
        for raw_photo in raw_photos_by_sku[sku][:RAW_PHOTOS_MIN]:
            variants = await engine.edit(images=[raw_photo], prompt=treatment_prompt, n=2)
            for img_bytes in variants:
                prepared, verdict = _prepare_image_for_upload(
                    img_bytes, requires_white_bg=requires_white_bg and saved == 0
                )
                if prepared is None:
                    db.add(ListingImage(
                        listing_id=listing.id,
                        status="validation_failed",
                        validation_error=verdict.reason,
                        sort_order=saved,
                        kind="individual",
                        source_sku=sku,
                    ))
                    continue
                ml_picture_id = await ml_pic.upload(prepared, access_token)

                db.add(ListingImage(
                    listing_id=listing.id,
                    ml_picture_id=ml_picture_id,
                    status="uploaded",
                    sort_order=saved,
                    kind="individual",
                    source_sku=sku,
                ))
                db.add(ProductImage(
                    seller_id=listing.seller_id,
                    sku=sku,
                    ml_picture_id=ml_picture_id,
                    source="openai_edit",
                    is_approved=False,
                ))
                saved += 1
                if first_individual_bytes is None:
                    first_individual_bytes = prepared

    # Cards de texto — só para 1 SKU, e a base preferida é a CAPA
    # DETERMINÍSTICA, não a primeira individual.
    #
    # Por que: o motor i2i altera o texto impresso no rótulo de forma
    # estocástica. Um teste real com o SKU 37 saiu com a capa correta
    # ("100ml | 3.38 fl.oz") e as individuais mostrando "160ml | 3.50 fl.ex",
    # com a marca escrita "weoink". Os 3 cards herdaram o erro porque usavam a
    # primeira individual como base — multiplicando por 3 uma imagem que
    # ninguém tinha verificado.
    #
    # A capa determinística é recorte do pixel original, sem IA: o rótulo nela
    # é sempre fiel. Ancorar os cards nela troca 3 imagens de risco
    # probabilístico por 3 de risco zero. A individual continua como fallback
    # para quando a capa não sai (foto bruta com fundo texturizado).
    base_cards = cover_prepared_bytes or first_individual_bytes
    if len(skus) == 1 and base_cards is not None:
        logger.info(
            "benefit_cards_base listing_id=%s sku=%s origem=%s",
            listing.id,
            skus[0],
            "cover_deterministic" if cover_prepared_bytes else "individual",
        )
        saved += await _append_benefit_cards(
            db,
            listing,
            access_token,
            base_photo=base_cards,
            source_sku=skus[0],
            start_sort_order=saved,
        )

    return saved


async def _generate_images_async(listing_id: str) -> dict:
    from sqlalchemy import select
    from sqlalchemy.orm import defer

    from app.database import worker_session
    from app.models.listing import Listing
    from app.models.listing_image import CANDIDATE_KINDS, ListingImage
    from app.models.product_image import ProductImage
    from app.models.seller import Seller
    from app.services.ai.service import get_ai_provider
    from app.services.image_service import MLPictureService

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

        # Verifica se já existem imagens aprovadas para este SKU neste seller
        if sku:
            existing = (
                await db.execute(
                    select(ProductImage)
                    .where(
                        ProductImage.seller_id == listing.seller_id,
                        ProductImage.sku == sku,
                        ProductImage.is_approved == True,
                    )
                    .order_by(ProductImage.created_at.asc())
                )
            ).scalars().all()

            if existing:
                for i, pi in enumerate(existing):
                    db.add(ListingImage(
                        listing_id=listing.id,
                        ml_picture_id=pi.ml_picture_id,
                        status="uploaded",
                        approved=True,
                        sort_order=i,
                    ))
                if listing.created_via == "batch":
                    listing.status = "generating_description"
                    await db.commit()
                else:
                    listing.status = "pending_image_approval"
                    await db.commit()
                return {"listing_id": listing_id, "images_reused": len(existing)}

        # Sem imagens existentes — gera com IA
        from datetime import datetime, timezone
        from app.services.image_engines.base import ImageEngineUnavailableError
        from app.services.image_engines.openai_engine import check_openai_health
        from app.services.image_engines.service import get_engine_instance, get_engine_state

        seller = (
            await db.execute(select(Seller).where(Seller.id == listing.seller_id))
        ).scalar_one()
        access_token = await _fetch_upload_token(seller, db)

        i2i_saved = await _try_i2i_generation(db, listing, seller, access_token)
        if i2i_saved is not None:
            if i2i_saved == 0:
                raise RuntimeError("Nenhuma imagem válida foi gerada pelo motor 'openai_edit'")
            # Categoria com perfil de 5 posicoes NUNCA auto-aprova, nem em
            # batch: revisao humana antes de publicar e obrigatoria em todas as
            # 5 posicoes, sem excecao. Sem este guard o batch aprovaria as
            # posicoes 2-4 (que nao sao CANDIDATE_KINDS) e publicaria um
            # anuncio sem capa e sem ficha, porque essas duas SAO candidatas e
            # ficariam de fora.
            from app.services.image_position_profiles import profile_for_category

            usa_cinco_posicoes = profile_for_category(listing.ml_category_id) is not None

            if listing.created_via == "batch" and not usa_cinco_posicoes:
                # `kind NOT IN CANDIDATE_KINDS`: um candidato `cover_ai` /
                # `specs_ai` gerado sob demanda tambem esta em status
                # "uploaded" e seria varrido por esta aprovacao em massa numa
                # RE-execucao do pipeline — e imagem aprovada com
                # ml_picture_id vai direto para o payload de publicacao.
                images = (await db.execute(
                    select(ListingImage)
                    .options(defer(ListingImage.image_bytes))
                    .where(
                        ListingImage.listing_id == listing.id,
                        ListingImage.status == "uploaded",
                        ListingImage.kind.notin_(CANDIDATE_KINDS),
                    )
                )).scalars().all()
                for img in images:
                    # Guard redundante de proposito (a query ja filtra):
                    # aprovar um candidato o coloca no payload de publicacao,
                    # entao a regra vale tambem onde a escrita acontece.
                    if img.kind in CANDIDATE_KINDS:
                        continue
                    img.approved = True
                prod_imgs = (await db.execute(
                    select(ProductImage).where(
                        ProductImage.seller_id == listing.seller_id,
                        ProductImage.sku == sku,
                    )
                )).scalars().all()
                for pi in prod_imgs:
                    pi.is_approved = True
                listing.status = "generating_description"
                await db.commit()
            else:
                listing.status = "pending_image_approval"
                await db.commit()
            return {"listing_id": listing_id, "images_saved": i2i_saved, "source": "i2i"}

        engine_state = await get_engine_state(db)

        if engine_state.current_engine == "gemini" and await check_openai_health():
            engine_state.current_engine = "openai"
            engine_state.last_openai_error = None
            engine_state.last_switch_to_openai_at = datetime.now(timezone.utc)
            await db.commit()

        ai = get_ai_provider()
        prompt = await ai.generate_image_prompt(
            brand=listing.sku_brand,
            title=listing.selected_title or "",
            description=listing.sku_description,
        )

        engine = get_engine_instance(engine_state.current_engine)
        source_label = engine_state.current_engine

        try:
            raw_images = await engine.generate(prompt)
        except ImageEngineUnavailableError as exc:
            engine_state.last_openai_error = str(exc)[:500]
            await db.commit()
            listing.failed_step = listing.status
            listing.status = "pending_image_engine_confirmation"
            listing.error_message = str(exc)[:500]
            await db.commit()
            return {"listing_id": listing_id, "pending_image_engine_confirmation": True}

        ml_pic = MLPictureService()
        saved = 0
        requires_white_bg = await _resolve_requires_white_bg(listing)

        for img_bytes in raw_images:
            prepared, verdict = _prepare_image_for_upload(
                img_bytes, requires_white_bg=requires_white_bg and saved == 0
            )
            if prepared is None:
                db.add(ListingImage(
                    listing_id=listing.id,
                    status="validation_failed",
                    validation_error=verdict.reason,
                    sort_order=saved,
                ))
                continue

            ml_picture_id = await ml_pic.upload(prepared, access_token)

            db.add(ListingImage(
                listing_id=listing.id,
                ml_picture_id=ml_picture_id,
                status="uploaded",
                sort_order=saved,
            ))

            # Registra no índice SKU→imagem (não aprovada ainda)
            if sku:
                db.add(ProductImage(
                    seller_id=listing.seller_id,
                    sku=sku,
                    ml_picture_id=ml_picture_id,
                    source=source_label,
                    is_approved=False,
                ))

            saved += 1

        if saved == 0:
            raise RuntimeError(f"Nenhuma imagem válida foi gerada pelo motor '{source_label}'")

        if listing.created_via == "batch":
            # Auto-aprovar todas as imagens geradas e suas entradas no índice SKU→imagem.
            # Mesma exclusão de candidatos do ramo i2i acima, pelo mesmo motivo:
            # `cover_ai`/`specs_ai` também ficam em status "uploaded" e só podem
            # ser aprovados por ação humana explícita (`promote_cover`).
            images = (await db.execute(
                select(ListingImage)
                .options(defer(ListingImage.image_bytes))
                .where(
                    ListingImage.listing_id == listing.id,
                    ListingImage.status == "uploaded",
                    ListingImage.kind.notin_(CANDIDATE_KINDS),
                )
            )).scalars().all()
            for img in images:
                # Mesmo guard redundante do ramo i2i, pelo mesmo motivo.
                if img.kind in CANDIDATE_KINDS:
                    continue
                img.approved = True
            if sku:
                prod_imgs = (await db.execute(
                    select(ProductImage).where(
                        ProductImage.seller_id == listing.seller_id,
                        ProductImage.sku == sku,
                    )
                )).scalars().all()
                for pi in prod_imgs:
                    pi.is_approved = True
            listing.status = "generating_description"
            await db.commit()
        else:
            listing.status = "pending_image_approval"
            await db.commit()

    return {"listing_id": listing_id, "images_saved": saved}


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
        from app.services.image_engines.base import ImageRateLimitError
        countdown = (
            60 * (2 ** self.request.retries)   # 60s, 120s — muito mais longo para 429
            if isinstance(exc, ImageRateLimitError)
            else 2 ** self.request.retries * 5  # 5s, 10s — erros comuns
        )
        if self.request.retries >= self.max_retries:
            asyncio.run(_mark_failed(listing_id, str(exc)))
            raise
        raise self.retry(exc=exc, countdown=countdown)


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
    o anuncio — mesmo padrao ja usado em `_append_benefit_cards`. O retry
    existe porque a falha tipica do motor e transiente (timeout, 5xx), e
    perder uma posicao inteira por isso seria caro.
    """
    for tentativa in range(1, tentativas + 1):
        try:
            return await fabrica()
        except Exception as exc:
            logger.warning(
                "posicao_falhou listing_id=%s posicao=%s tentativa=%s/%s reason=%s",
                listing_id, descricao, tentativa, tentativas, exc,
                exc_info=(tentativa == tentativas),
            )
    return None


async def _campos_das_posicoes(db, listing):
    """Textos das posicoes 2, 3 e 5, todos de fontes ja existentes.

    Posicao 2 espelha a hierarquia do ROTULO FISICO: nome do produto em
    destaque, marca abaixo — no frasco, "wepink" e pequeno e "FATAL BLACK" e
    grande. Nao se inventa hierarquia nova quando a embalagem ja resolveu.
    """
    from sqlalchemy import select

    from app.models.listing_attribute import ListingAttribute
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
        "marca": (listing.sku_brand or "").strip() or None,
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

    preparado, veredito = _prepare_image_for_upload(
        gerado, requires_white_bg=requires_white_bg
    )
    if preparado is None:
        db.add(ListingImage(
            listing_id=listing.id, status="validation_failed",
            validation_error=veredito.reason, approved=False,
            sort_order=sort_order, kind=kind, source_sku=sku, image_bytes=gerado,
        ))
        logger.warning(
            "posicao_reprovada listing_id=%s kind=%s reason=%s",
            listing.id, kind, veredito.reason,
        )
        return False

    ml_picture_id = await MLPictureService().upload(preparado, access_token)
    db.add(ListingImage(
        listing_id=listing.id, ml_picture_id=ml_picture_id, status="uploaded",
        approved=False, sort_order=sort_order, kind=kind, source_sku=sku,
        image_bytes=preparado,
    ))
    return True


async def _gerar_cinco_posicoes(db, listing, access_token, profile, fotos, sku) -> int:
    """As 5 posicoes do esquema, cada uma independente. Devolve quantas subiram.

    Substitui, para categoria com perfil, o modelo antigo de "N variantes por
    foto": cada posicao 2-4 e UMA chamada de edicao que pode referenciar TODAS
    as fotos brutas do SKU, entao o corte `[:RAW_PHOTOS_MIN]` nao se aplica
    aqui (ele continua no caminho antigo, intocado).

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

        ml_picture_id = await MLPictureService().upload(base, access_token)
        db.add(ListingImage(
            listing_id=listing.id, ml_picture_id=ml_picture_id, status="uploaded",
            approved=False, sort_order=0, kind="cover_deterministic",
            source_sku=sku, image_bytes=base,
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
