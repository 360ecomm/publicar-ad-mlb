from uuid import UUID
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.core.dependencies import get_db, get_current_user, get_active_seller
from app.models.listing import Listing
from app.models.listing_description import ListingDescription
from app.models.seller import Seller
from app.models.listing_title import ListingTitle
from app.models.listing_attribute import ListingAttribute
from app.models.listing_job import ListingJob
from app.models.listing_image import ListingImage
from app.schemas.listing import (
    AttributesEditResponse,
    ImageApproveRequest,
    ImageOut,
    ListingCreate,
    ListingDetail,
    ListingPage,
    ListingStatusCounts,
    ListingSummary,
    RawPhotoGroup,
    RawPhotosOut,
)
from app.schemas.attribute import AttributesSubmitRequest
from app.services.listing_service import ListingService
from app.services.publish_service import PublishService
from app.services.seller_image_config_service import SellerImageConfigService

router = APIRouter(prefix="/listings", tags=["listings"])


async def _load_detail(db: AsyncSession, listing: Listing) -> ListingDetail:
    from app.schemas.listing import TitleOption, AttributeOut, JobOut

    titles = (await db.execute(
        select(ListingTitle).where(ListingTitle.listing_id == listing.id)
        .order_by(ListingTitle.ai_score.desc().nullslast())
    )).scalars().all()

    attributes = (await db.execute(
        select(ListingAttribute).where(ListingAttribute.listing_id == listing.id)
        .order_by(ListingAttribute.is_required.desc(), ListingAttribute.attribute_name)
    )).scalars().all()

    images = (await db.execute(
        select(ListingImage)
        .where(ListingImage.listing_id == listing.id)
        .order_by(ListingImage.sort_order)
    )).scalars().all()

    jobs = (await db.execute(
        select(ListingJob).where(ListingJob.listing_id == listing.id)
        .order_by(ListingJob.created_at.desc())
    )).scalars().all()

    desc_row = (await db.execute(
        select(ListingDescription).where(ListingDescription.listing_id == listing.id)
    )).scalar_one_or_none()

    return ListingDetail(
        id=listing.id,
        sku_external_id=listing.sku_external_id,
        sku_brand=listing.sku_brand,
        selected_title=listing.selected_title,
        status=listing.status,
        created_via=listing.created_via,
        mlb_id=listing.mlb_id,
        created_at=listing.created_at,
        updated_at=listing.updated_at,
        sku_description=listing.sku_description,
        price=listing.price,
        stock_quantity=listing.stock_quantity,
        condition=listing.condition,
        listing_type_id=listing.listing_type_id,
        ml_category_id=listing.ml_category_id,
        approved_image_count=listing.approved_image_count,
        error_message=listing.error_message,
        description_html=desc_row.description_html if desc_row else None,
        titles=[TitleOption.model_validate(t) for t in titles],
        attributes=[AttributeOut.model_validate(a) for a in attributes],
        images=[ImageOut.model_validate(i) for i in images],
        jobs=[JobOut.model_validate(j) for j in jobs],
    )


@router.post("", response_model=ListingSummary, status_code=201)
async def create_listing(
    data: ListingCreate,
    current_user=Depends(get_current_user),
    active_seller=Depends(get_active_seller),
    db: AsyncSession = Depends(get_db),
):
    return await ListingService(db).create(data, current_user, active_seller)


@router.get("", response_model=ListingPage)
async def list_listings(
    status: Optional[list[str]] = Query(None),
    search: Optional[str] = Query(None, max_length=200),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    active_seller=Depends(get_active_seller),
    db: AsyncSession = Depends(get_db),
):
    """`status` aceita repeticao (`?status=a&status=b`): cada agrupamento da
    fila junta 3 ou 4 status. Um so (`?status=a`) continua igual a hoje — o
    quadro atual depende disso. `search` casa por SKU, titulo, descricao de
    origem (`sku_description`, o unico texto que existe antes do titulo),
    marca e MLB."""
    return await ListingService(db).list_listings(
        active_seller.id, status, page, page_size, search=search
    )


@router.get("/status-counts", response_model=ListingStatusCounts)
async def listing_status_counts(
    active_seller=Depends(get_active_seller),
    db: AsyncSession = Depends(get_db),
):
    """Barra de resumo da fila: contagem por status do seller ativo, em uma
    consulta agregada. Fica ANTES de `/{listing_id}` de proposito — FastAPI
    casa rotas na ordem de declaracao, e declarada depois, "status-counts"
    cairia no parametro `{listing_id}`, falharia no parse de UUID e devolveria
    422 em vez do resumo."""
    return await ListingService(db).count_by_status(active_seller.id)


@router.get("/{listing_id}", response_model=ListingDetail)
async def get_listing(
    listing_id: UUID,
    active_seller=Depends(get_active_seller),
    db: AsyncSession = Depends(get_db),
):
    listing = await ListingService(db).get_or_404(listing_id, active_seller.id)
    return await _load_detail(db, listing)


@router.get("/{listing_id}/raw-photos", response_model=RawPhotosOut)
async def listing_raw_photos(
    listing_id: UUID,
    active_seller=Depends(get_active_seller),
    db: AsyncSession = Depends(get_db),
):
    """URLs das fotos brutas do anuncio, para o botao "ver original" da tela
    de revisao (sob demanda: a maioria das revisoes nao precisa do original).
    Devolve URL, nunca bytes — o bucket e' publico e o navegador busca direto;
    o servidor so resolve o que existe, porque a sondagem `{sku}-1`, `-2`...
    x extensoes feita do navegador seria N x 3 requisicoes e barreira de CORS.

    Pior caso por SKU: `RAW_PHOTOS_MAX` x `len(RAW_PHOTO_EXTENSIONS)`
    requisicoes ao bucket (ver `discover_raw_photo_urls`, que documenta os
    limites da descoberta por sondagem e a alternativa via API S3 do R2).
    Seller sem `raw_base_url` ou SKU sem foto: 200 com lista vazia, nao erro.
    Anuncio de outro seller: 404, como nos demais endpoints."""
    from app.services.seller_image_source_service import (
        discover_raw_photo_urls,
        resolve_listing_skus,
    )

    listing = await ListingService(db).get_or_404(listing_id, active_seller.id)
    config = await SellerImageConfigService(db, active_seller.id).get()
    if config is None or not config.raw_base_url:
        return RawPhotosOut(configured=False, groups=[])
    groups = [
        RawPhotoGroup(sku=sku, urls=await discover_raw_photo_urls(config.raw_base_url, sku))
        for sku in await resolve_listing_skus(listing)
    ]
    return RawPhotosOut(configured=True, groups=groups)


@router.delete("/{listing_id}", status_code=204)
async def delete_listing(
    listing_id: UUID,
    active_seller=Depends(get_active_seller),
    db: AsyncSession = Depends(get_db),
):
    await ListingService(db).delete(listing_id, active_seller.id)


@router.post("/{listing_id}/pipeline/start", response_model=ListingSummary)
async def start_pipeline(
    listing_id: UUID,
    active_seller=Depends(get_active_seller),
    db: AsyncSession = Depends(get_db),
):
    svc = ListingService(db)
    listing = await svc.get_or_404(listing_id, active_seller.id)
    await svc.start_pipeline(listing)
    return await svc.summary_after_commit(listing)


@router.post("/{listing_id}/pipeline/retry", response_model=ListingSummary)
async def retry_pipeline(
    listing_id: UUID,
    active_seller=Depends(get_active_seller),
    db: AsyncSession = Depends(get_db),
):
    svc = ListingService(db)
    listing = await svc.get_or_404(listing_id, active_seller.id)
    await svc.retry_pipeline(listing)
    return await svc.summary_after_commit(listing)


@router.post("/{listing_id}/titles/{title_id}/select", response_model=ListingSummary)
async def select_title(
    listing_id: UUID,
    title_id: UUID,
    active_seller=Depends(get_active_seller),
    db: AsyncSession = Depends(get_db),
):
    svc = ListingService(db)
    listing = await svc.get_or_404(listing_id, active_seller.id)
    await svc.select_title(listing, title_id)
    return await svc.summary_after_commit(listing)


@router.put("/{listing_id}/attributes", response_model=ListingSummary)
async def submit_attributes(
    listing_id: UUID,
    body: AttributesSubmitRequest,
    active_seller=Depends(get_active_seller),
    db: AsyncSession = Depends(get_db),
):
    svc = ListingService(db)
    listing = await svc.get_or_404(listing_id, active_seller.id)
    await svc.submit_attributes(listing, [a.model_dump() for a in body.attributes])
    return await svc.summary_after_commit(listing)


@router.patch("/{listing_id}/attributes", response_model=AttributesEditResponse)
async def edit_attributes(
    listing_id: UUID,
    body: AttributesSubmitRequest,
    active_seller=Depends(get_active_seller),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Corrige atributo já gravado, SEM avançar a etapa do anúncio.

    O `PUT` da mesma rota é o passo de preenchimento (tolera obrigatório
    vazio e decide o status seguinte); este é o de correção, e recusa
    obrigatório vazio. 409 fora dos status editáveis ou com regeneração de
    imagem em andamento; 422 para valor fora da enumeração, obrigatório
    esvaziado ou edição que não muda nada. Nenhuma task é enfileirada.
    Ver `ListingService.edit_attributes`.
    """
    svc = ListingService(db)
    listing = await svc.get_or_404(listing_id, active_seller.id)
    stale, duplicados = await svc.edit_attributes(
        listing, [a.model_dump() for a in body.attributes], user_id=current_user.id
    )
    return AttributesEditResponse(
        listing=await svc.summary_after_commit(listing),
        stale_positions=stale,
        duplicated_fields=duplicados,
    )


@router.post("/{listing_id}/pipeline/generate_images", response_model=ListingSummary)
async def generate_images(
    listing_id: UUID,
    active_seller=Depends(get_active_seller),
    db: AsyncSession = Depends(get_db),
):
    svc = ListingService(db)
    listing = await svc.get_or_404(listing_id, active_seller.id)
    await svc.trigger_image_generation(listing)
    return await svc.summary_after_commit(listing)


@router.post("/{listing_id}/pipeline/resume_raw_photos", response_model=ListingSummary)
async def resume_raw_photos(
    listing_id: UUID,
    active_seller=Depends(get_active_seller),
    db: AsyncSession = Depends(get_db),
):
    """Retomada manual de `pending_raw_photos`: sonda o bucket agora, sem
    esperar o proximo ciclo do beat. 409 se as fotos ainda nao estao la."""
    svc = ListingService(db)
    listing = await svc.get_or_404(listing_id, active_seller.id)
    await svc.resume_raw_photos(listing)
    return await svc.summary_after_commit(listing)


@router.post("/{listing_id}/pipeline/resume_ai_engine", response_model=ListingSummary)
async def resume_ai_engine(
    listing_id: UUID,
    active_seller=Depends(get_active_seller),
    db: AsyncSession = Depends(get_db),
):
    """Retomada manual de `pending_ai_engine` (credito OpenAI recarregado):
    redispara a geracao agora, sem esperar o proximo ciclo do beat."""
    svc = ListingService(db)
    listing = await svc.get_or_404(listing_id, active_seller.id)
    await svc.resume_ai_engine(listing)
    return await svc.summary_after_commit(listing)


@router.post("/{listing_id}/images/approve", response_model=ListingSummary)
async def approve_images(
    listing_id: UUID,
    body: ImageApproveRequest,
    active_seller=Depends(get_active_seller),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    svc = ListingService(db)
    listing = await svc.get_or_404(listing_id, active_seller.id)
    await svc.approve_images(listing, body.approved_ids, body.review_seconds, user_id=current_user.id)
    return await svc.summary_after_commit(listing)


@router.post(
    "/{listing_id}/images/positions/{posicao}/regenerate",
    response_model=ImageOut,
    status_code=202,
)
async def regenerate_image_position(
    listing_id: UUID,
    posicao: int,
    active_seller=Depends(get_active_seller),
    db: AsyncSession = Depends(get_db),
):
    """Regenera UMA posição (0..4, = `sort_order`) do esquema de 5 posições.

    Devolve 202 com o placeholder (`status="generating"`); a imagem chega
    pela task e aparece em `GET /listings/{id}` com `status="uploaded"`,
    `validation_failed` (QA) ou `generation_failed` (motor). 409 fora de
    `pending_image_approval`/`ready_to_publish`, em posição já
    aprovada quando o anúncio está em `pending_image_approval`, ou com
    regeneração já em andamento. A partir de `ready_to_publish` a posição é
    desaprovada e o anúncio volta para `pending_image_approval`.
    422 fora de 0..4. Ver `ListingService.regenerate_position`.
    """
    svc = ListingService(db)
    listing = await svc.get_or_404(listing_id, active_seller.id)
    placeholder = await svc.regenerate_position(listing, posicao)
    return ImageOut.model_validate(placeholder)


@router.post("/{listing_id}/pipeline/publish", response_model=ListingSummary)
async def publish_listing(
    listing_id: UUID,
    active_seller=Depends(get_active_seller),
    db: AsyncSession = Depends(get_db),
):
    svc = ListingService(db)
    listing = await svc.get_or_404(listing_id, active_seller.id)
    await svc.trigger_publish(listing)
    return await svc.summary_after_commit(listing)


@router.post("/{listing_id}/activate")
async def activate_listing(
    listing_id: UUID,
    active_seller=Depends(get_active_seller),
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Listing).where(
            Listing.id == listing_id,
            Listing.seller_id == active_seller.id,
        )
    )
    listing = result.scalar_one_or_none()
    if not listing:
        raise HTTPException(status_code=404, detail="Anúncio não encontrado")
    if listing.status != "published_paused":
        raise HTTPException(status_code=422, detail="Anúncio não está pausado")

    seller_result = await db.execute(select(Seller).where(Seller.id == active_seller.id))
    seller = seller_result.scalar_one()

    await PublishService(db).activate_listing(listing, seller)
    return {"status": "published"}


@router.post("/{listing_id}/images/cover-ai-variant", response_model=ImageOut, status_code=201)
async def generate_cover_ai_variant(
    listing_id: UUID,
    active_seller=Depends(get_active_seller),
    db: AsyncSession = Depends(get_db),
):
    """Gera sob demanda a variante ambientada da capa (Frente A).

    Nasce como candidato não aprovado (`approved=False`) — não muda o anúncio
    automaticamente. Um humano revisa e decide se promove (`promote_cover`,
    também Frente A).
    """
    from app.services.cover_variant_service import CoverVariantError, generate_cover_variant
    from app.services.image_engines.base import ImageEngineUnavailableError
    from app.services.publish_service import get_valid_access_token

    svc = ListingService(db)
    listing = await svc.get_or_404(listing_id, active_seller.id)
    access_token = await get_valid_access_token(active_seller, db)
    try:
        candidate = await generate_cover_variant(db, listing, access_token)
    except CoverVariantError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ImageEngineUnavailableError as exc:
        # 502, não 500: quem falhou foi um provedor externo (OpenAI) que este
        # endpoint expõe como gateway — mesmo status usado em publish_service.py
        # para falhas da API do ML. Condição transiente e retryable: o
        # operador clicou num botão pago e precisa saber que pode tentar de
        # novo, não que o endpoint está quebrado. Nenhuma ListingImage chega a
        # ser gravada neste caminho — a falha acontece antes de qualquer
        # db.add() no serviço.
        raise HTTPException(
            status_code=502,
            detail=f"Motor de imagem indisponível no momento — tente novamente em instantes. ({exc})",
        )
    return ImageOut.model_validate(candidate)


@router.post("/{listing_id}/images/{image_id}/promote-cover", response_model=ListingSummary)
async def promote_cover(
    listing_id: UUID,
    image_id: UUID,
    active_seller=Depends(get_active_seller),
    db: AsyncSession = Depends(get_db),
):
    """Decide qual imagem ocupa a capa do anúncio (Frente A).

    A imagem escolhida — capa determinística ou variante IA — assume
    `sort_order=0` e `approved=True`. **Só linhas de kind de capa** que
    estejam em `sort_order=0` são rebaixadas a candidatas (`approved=False`,
    `sort_order=90`); nenhuma é apagada. Fotos `individual` e cards não são
    tocados nem quando estão em 0, porque rebaixar despublicaria uma foto que
    o operador já aprovou — e `approve_images` reserva o 0 a kinds de capa
    justamente para que essa restrição não deixe duas imagens empatadas.

    Alvo de outro kind → 422. Alvo de outro anúncio → 404. Nada aqui roda
    automaticamente: só troca de lugar quando um humano chama este endpoint.

    Limitação conhecida (aceita no piloto): duas promoções **de alvos
    diferentes** no mesmo anúncio, simultâneas, podem terminar com as duas em
    `sort_order=0`. Ver `cover_variant_service.promote_cover`.

    409 enquanto houver regeneração de posição em andamento no anúncio: esta
    promoção rebaixaria o placeholder `generating` da posição 0 para 90.
    """
    from app.services.cover_variant_service import promote_cover as _promote_cover

    svc = ListingService(db)
    listing = await svc.get_or_404(listing_id, active_seller.id)
    await svc.recusar_se_regeneracao_em_andamento(listing)
    await _promote_cover(db, listing, image_id)
    return await svc.summary_after_commit(listing)


@router.post("/{listing_id}/images/{image_id}/promote-specs", response_model=ListingSummary)
async def promote_specs(
    listing_id: UUID,
    image_id: UUID,
    active_seller=Depends(get_active_seller),
    db: AsyncSession = Depends(get_db),
):
    """Decide qual imagem ocupa o slot de ficha técnica (Frente B).

    Simétrico a `promote_cover`, com uma diferença: a capa tem posição fixa
    (0) e a ficha técnica **não tem** — `promote_specs` lê o `sort_order` da
    ficha que já está na galeria e o alvo assume esse lugar. Ver
    `PROMOTABLE_SPECS_KINDS` no model para o porquê.

    Alvo de outro kind → 422 (inclusive capa, que tem endpoint próprio).
    Alvo de outro anúncio → 404. A ficha rebaixada vira candidata, nunca é
    apagada. Nada aqui roda automaticamente.

    409 enquanto houver regeneração de posição em andamento no anúncio:
    promover aprova uma linha por baixo de uma posição que ainda está sendo
    refeita.
    """
    from app.services.specs_variant_service import promote_specs as _promote_specs

    svc = ListingService(db)
    listing = await svc.get_or_404(listing_id, active_seller.id)
    await svc.recusar_se_regeneracao_em_andamento(listing)
    await _promote_specs(db, listing, image_id)
    return await svc.summary_after_commit(listing)


@router.post("/{listing_id}/images/specs-ai-variant", response_model=ImageOut, status_code=201)
async def generate_specs_ai_variant(
    listing_id: UUID,
    active_seller=Depends(get_active_seller),
    db: AsyncSession = Depends(get_db),
):
    """Gera sob demanda a ficha tecnica renderizada por IA (Frente B).

    Candidato para comparação A/B com o `card_specs` (Pillow) já produzido
    pelo pipeline — nasce não aprovado (`approved=False`) e nunca substitui o
    `card_specs` automaticamente. Um humano compara os dois e decide.
    """
    from app.services.image_engines.base import ImageEngineUnavailableError
    from app.services.publish_service import get_valid_access_token
    from app.services.specs_variant_service import SpecsVariantError, generate_specs_variant

    svc = ListingService(db)
    listing = await svc.get_or_404(listing_id, active_seller.id)
    access_token = await get_valid_access_token(active_seller, db)
    try:
        candidate = await generate_specs_variant(db, listing, access_token)
    except SpecsVariantError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ImageEngineUnavailableError as exc:
        # 502, não 500: mesma semântica do endpoint de variante de capa — o
        # provedor externo (OpenAI) falhou, não este serviço. Nenhuma
        # ListingImage chega a ser gravada neste caminho.
        raise HTTPException(
            status_code=502,
            detail=f"Motor de imagem indisponível no momento — tente novamente em instantes. ({exc})",
        )
    return ImageOut.model_validate(candidate)
