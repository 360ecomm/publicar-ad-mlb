"""Frente B: ficha tecnica renderizada por IA, gerada sob demanda.

Nada aqui roda automaticamente — o pipeline batch/manual continua produzindo
o `card_specs` pelo antigo renderizador Pillow (removido em 2026-09-10, sem custo de
IA) exatamente como hoje. Este servico so e acionado quando um humano chama
o endpoint dedicado; o resultado e um CANDIDATO para comparacao A/B com o
`card_specs` ja existente, nunca uma substituicao automatica.

A ficha tecnica IA parte SEMPRE dos bytes que ja subiram para o ML na capa
deterministica (bytes no R2 via `ListingImage.asset_key`, kind="cover_deterministic"),
nunca de uma foto bruta re-derivada nem do card Pillow ja renderizado —
mesma fonte e mesmo motivo da variante de capa (Frente A): a capa
deterministica nunca passou por IA, entao o rotulo do produto nela e fiel.

A copy (titulo + bullets) NAO e inventada aqui NEM por LLM: vem de
`build_specs_card`, que monta os bullets direto do `value_name` dos
atributos — o mesmo texto que o `card_specs` Pillow usa. O motor de IA entra
so para a composicao visual (foto + layout); o texto chega a ele pronto.

Antes esta funcao chamava `generate_card_copy` e dependia do LLM devolver um
angulo `card_specs` utilizavel. Isso somava um modo de falha ("a copy nao
veio, tente de novo") e, pior, punha texto estocastico numa ficha tecnica:
o tipo do perfume do SKU 37 saiu parafraseado numa execucao e correto em
outra. Ficha tecnica e dado, nao redacao.
"""
import logging
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.models.listing_attribute import ListingAttribute
from app.models.listing_image import (
    CANDIDATE_SORT_ORDER_FLOOR,
    COVER_SORT_ORDER,
    PROMOTABLE_SPECS_KINDS,
    SPECS_AI_KIND,
    ListingImage,
)
from app.services.cover_variant_service import _load_latest_deterministic_cover

logger = logging.getLogger(__name__)

SPECS_AI_SORT_ORDER = 91  # fora da faixa 0..N da galeria, ao lado de COVER_AI_SORT_ORDER=90


class SpecsVariantError(RuntimeError):
    """Nao ha capa deterministica com bytes salvos, ou os atributos nao formam uma ficha."""


def _build_specs_prompt(bullets: list[str]) -> str:
    """Prompt da ficha tecnica — MESMA linguagem visual das posicoes 2, 3 e 4.

    Cena atmosferica ocupando o quadro inteiro, amostrada da cor do produto,
    com o texto num cartao arredondado FLUTUANDO dentro dela. A versao
    anterior pedia "Lower ~40%: a clean panel", um painel colado na borda
    cobrindo a largura toda — o que destoava da posicao 3 e foi o que o
    Gabriel identificou como quebra de estilo. A causa era a redacao do
    prompt, nao o modelo.

    A secao LAYOUT antiga saiu junto, e nao so a clausula ALLOWED foi
    trocada: aquela secao descrevia exatamente o painel que a nova clausula
    proibe. Manter as duas deixaria o prompt pedindo e proibindo a mesma
    coisa — o defeito que reprovava a Frente A por construcao.

    SEM TITULO, por decisao de produto: a ficha sai so com os bullets. O
    prompt PROIBE cabecalho explicitamente, em vez de apenas omiti-lo —
    tirar a linha do titulo sem proibir convida o modelo a inventar um para
    preencher o topo do cartao.

    `build_specs_card` continua devolvendo `title`: o card Pillow
    (`card_specs`) e outro caminho e mantem o cabecalho dele.

    A clausula CRITICAL vem do piloto palavra por palavra: e a licao da Fase
    5c, quando o motor reescreveu "100ml" como "160ml" e "wepink" como
    "weoink" num anuncio real.
    """
    linhas = "\n".join(f'- "{b}"' for b in bullets)
    return (
        "Compose a premium square technical specification card (1200x1200)\n"
        "using the reference photo of this exact product as the visual source.\n\n"
        "ALLOWED: place the product against an elegant full-bleed backdrop\n"
        "sampled from the product's own dominant color, with soft paper-like\n"
        "texture and directional lighting — not flat white. Reserve an\n"
        "integrated rounded card, with visible margin from all four edges, for\n"
        "a short bulleted list — the card must float within the atmospheric\n"
        "scene, never a flat panel spanning the full width or touching the\n"
        "image edges.\n\n"
        "FORBIDDEN — the product must keep its identity: do not reshape, recolor\n"
        "or re-proportion the product body; do not add, remove or move any\n"
        "object; no props, hands, logos, watermarks, prices or badges.\n\n"
        "CRITICAL: do not alter, redraw, translate, correct or re-render ANY text\n"
        "printed on the product or its packaging. Brand names, product names, volumes\n"
        "and measurement units must be preserved exactly as they appear, character for\n"
        "character. Never change a number or a unit. If any text is unreadable, keep it\n"
        "unreadable rather than inventing plausible text.\n\n"
        "Text to render inside the card (Brazilian Portuguese, exactly as\n"
        "written, verbatim — do not translate, paraphrase, correct, reorder or\n"
        "invent additional specifications), one line per item, each preceded by\n"
        "a small illustrated line icon whose meaning matches that line. Do NOT\n"
        "render a heading, title or caption of any kind above or below the\n"
        "list:\n"
        f"{linhas}\n\n"
        "Typography: modern geometric sans-serif, calm and readable, generous\n"
        "margins inside the card. Nothing cropped at the edges."
    )


async def generate_specs_variant(db, listing, access_token: str) -> ListingImage:
    """Gera 1 candidato de ficha tecnica IA a partir dos bytes SALVOS da capa.

    Levanta `SpecsVariantError` antes de tocar no motor de IA se: (a) nao
    houver capa deterministica com bytes salvos, ou (b) os atributos do
    anuncio nao renderem `MIN_BULLETS` linhas de ficha — nos dois casos um
    request que nao pode ter sucesso nao deve chamar um motor pago.
    """
    from app.services.ai.cost_log import set_cost_context

    set_cost_context(listing_id=listing.id, sku=listing.sku_external_id)

    from app.services.image_card_copy_service import build_specs_card
    from app.services.image_engines.openai_edit_engine import OpenAIEditEngine
    from app.services.image_service import MLPictureService
    from app.workers.tasks.image_tasks import _prepare_image_for_upload

    # Mesma busca da Frente A, e pelo mesmo motivo: pode haver MAIS DE UMA
    # linha `cover_deterministic` (nada apaga ListingImage, e cada passada de
    # `_try_i2i_generation` insere uma, inclusive uma `validation_failed` sem
    # bytes). `scalar_one_or_none` estouraria `MultipleResultsFound` — 500
    # opaco no lugar do 409 deliberado. Ver o docstring do helper.
    from app.services.r2_asset_service import load_candidate_bytes, store_candidate_bytes

    cover = await _load_latest_deterministic_cover(db, listing)
    cover_bytes = await load_candidate_bytes(cover) if cover is not None else None

    if cover is None or cover_bytes is None:
        raise SpecsVariantError(
            "capa deterministica sem bytes salvos no R2 — anuncio gerado antes desta funcionalidade ou R2 indisponivel"
        )

    # Query propria (nao `listing.attributes`): relacionamento lazy levantaria
    # MissingGreenlet fora de uma sessao com contexto async ativo — ver CLAUDE.md.
    attributes = (
        await db.execute(
            select(ListingAttribute).where(ListingAttribute.listing_id == listing.id)
        )
    ).scalars().all()

    specs_copy = build_specs_card(attributes)
    if specs_copy is None:
        raise SpecsVariantError(
            "atributos insuficientes para montar a ficha tecnica deste anuncio"
        )

    prompt = _build_specs_prompt(specs_copy.bullets)

    engine = OpenAIEditEngine()
    variants = await engine.edit(images=[cover_bytes], prompt=prompt, n=1)
    generated_bytes = variants[0]

    # Ficha tecnica nunca e capa, entao fundo branco puro nunca e exigido dela
    # — mesma regra dos antigos cards Pillow.
    prepared, verdict = _prepare_image_for_upload(generated_bytes, requires_white_bg=False)

    if prepared is None:
        candidate = ListingImage(
            listing_id=listing.id,
            status="validation_failed",
            validation_error=verdict.reason,
            approved=False,
            sort_order=SPECS_AI_SORT_ORDER,
            kind=SPECS_AI_KIND,
            source_sku=cover.source_sku,
            # Mesma razao da Frente A: candidato reprovado continua revisavel.
            # Sem `ml_picture_id` — nada subiu para o ML.
            asset_key=await store_candidate_bytes(
                generated_bytes, db=db, seller_id=listing.seller_id, sku=cover.source_sku,
                kind=SPECS_AI_KIND,
            ),
        )
        db.add(candidate)
        await db.commit()
        logger.info("specs_variant listing_id=%s result=rejected", listing.id)
        return candidate

    ml_pic = MLPictureService()
    ml_picture_id = await ml_pic.upload(prepared, access_token)

    candidate = ListingImage(
        listing_id=listing.id,
        ml_picture_id=ml_picture_id,
        status="uploaded",
        approved=False,
        sort_order=SPECS_AI_SORT_ORDER,
        kind=SPECS_AI_KIND,
        source_sku=cover.source_sku,
        asset_key=await store_candidate_bytes(
            prepared, db=db, seller_id=listing.seller_id, sku=cover.source_sku,
            kind=SPECS_AI_KIND,
        ),
    )
    db.add(candidate)
    await db.commit()
    logger.info("specs_variant listing_id=%s result=uploaded", listing.id)
    return candidate


async def promote_specs(db, listing, image_id: UUID) -> None:
    """Troca qual imagem ocupa o slot de ficha tecnica na galeria (Frente B).

    Simetrico a `cover_variant_service.promote_cover`, com uma diferenca que
    NAO e detalhe de implementacao: a capa tem posicao fixa (0) e esta funcao
    LE a posicao em vez de impor uma. Ver `PROMOTABLE_SPECS_KINDS` no model
    para o porque — resumido: `card_specs` nasce em `start_sort_order + saved`,
    entao seu numero depende de quantas individuais o anuncio gerou, e cravar
    um valor mudaria a numeracao da galeria de todo anuncio ja publicado.

    O alvo precisa ser deste anuncio (senao 404) e ter `kind` em
    `PROMOTABLE_SPECS_KINDS` (senao 422). Promover uma CAPA por aqui tambem da
    422: capa tem endpoint proprio, e aceita-la aqui a mandaria para o meio da
    galeria deixando o slot 0 vago.

    O alvo assume o `sort_order` da ficha que hoje esta na galeria e
    `approved=True`. Toda outra linha de kind de ficha que esteja na galeria
    volta a ser candidata (`approved=False, sort_order=SPECS_AI_SORT_ORDER`) —
    nunca apagada, para o operador poder promover de volta depois.

    Sem ficha na galeria, o alvo entra DEPOIS da ultima imagem publicada, nunca
    por cima de uma foto ja aprovada. Com a galeria vazia, entra logo apos a
    posicao reservada a capa: mandar a ficha para o 0 violaria
    `PROMOTABLE_COVER_KINDS`.

    Rebaixa a LISTA inteira, e nao "a ficha atual" via `scalar_one_or_none`,
    pelo mesmo motivo de `promote_cover`: duas promocoes concorrentes podem
    deixar duas linhas empatadas no mesmo slot, e `publish_service` ordena por
    `sort_order` sem desempate — a ficha publicada viraria sorteio. Rebaixar
    tudo faz a proxima promocao se autocurar em vez de estourar
    `MultipleResultsFound`.

    Idempotente: promover quem ja esta no slot, sem duplicatas a rebaixar, nao
    escreve no banco.

    Herda a mesma LIMITACAO CONHECIDA de `promote_cover`: `with_for_update()`
    so serializa disputas pelas MESMAS linhas, entao duas promocoes simultaneas
    de alvos diferentes ainda podem empatar. Janela de milissegundos, exige
    acao humana concorrente no mesmo anuncio, e o estado se autocura.
    """
    target = (
        await db.execute(
            select(ListingImage)
            .where(
                ListingImage.id == image_id,
                ListingImage.listing_id == listing.id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()

    if target is None:
        raise HTTPException(status_code=404, detail="Imagem nao encontrada neste anuncio")

    if target.kind not in PROMOTABLE_SPECS_KINDS:
        raise HTTPException(
            status_code=422,
            detail="Somente o card de ficha tecnica ou a variante gerada por IA podem ocupar o slot de ficha",
        )

    ocupantes = (
        await db.execute(
            select(ListingImage)
            .where(
                ListingImage.listing_id == listing.id,
                ListingImage.kind.in_(PROMOTABLE_SPECS_KINDS),
                ListingImage.sort_order < CANDIDATE_SORT_ORDER_FLOOR,
                ListingImage.id != target.id,
            )
            .order_by(ListingImage.sort_order, ListingImage.id)
            .with_for_update()
        )
    ).scalars().all()

    # Guard redundante de proposito, como em `promote_cover`: a query ja filtra,
    # mas rebaixar despublica uma imagem, entao a regra e conferida onde a
    # escrita acontece, e nao so onde as linhas sao lidas.
    rebaixaveis = [o for o in ocupantes if o.kind in PROMOTABLE_SPECS_KINDS]

    if rebaixaveis:
        slot = min(o.sort_order for o in rebaixaveis)
    elif not target.is_candidate:
        # Ja esta na galeria e nao ha outra ficha: fica onde esta.
        slot = target.sort_order
    else:
        ultimo = (
            await db.execute(
                select(func.max(ListingImage.sort_order)).where(
                    ListingImage.listing_id == listing.id,
                    ListingImage.approved.is_(True),
                    ListingImage.sort_order < CANDIDATE_SORT_ORDER_FLOOR,
                )
            )
        ).scalars().all()
        maior = ultimo[0] if ultimo else None
        slot = COVER_SORT_ORDER + 1 if maior is None else maior + 1

    changed = False
    rebaixados = []

    for ocupante in rebaixaveis:
        ocupante.approved = False
        ocupante.sort_order = SPECS_AI_SORT_ORDER
        rebaixados.append(ocupante.id)
        changed = True

    if target.sort_order != slot or not target.approved:
        target.approved = True
        target.sort_order = slot
        changed = True

    if not changed:
        return

    try:
        await db.commit()
    except IntegrityError:
        # Indice unico parcial `uq_listing_images_specs_slot`: outra promocao
        # aprovou uma ficha na galeria entre a nossa leitura e o commit.
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Outra promoção de ficha técnica já está em andamento neste anúncio; tente de novo.",
        )
    logger.info(
        "specs_promote listing_id=%s promoted_id=%s slot=%s demoted_ids=%s",
        listing.id,
        target.id,
        slot,
        rebaixados,
    )
