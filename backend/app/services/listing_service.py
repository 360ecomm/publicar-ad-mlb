import logging
from uuid import UUID
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, func, or_
from sqlalchemy import update as sa_update, delete as sa_delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import aliased
from app.models.listing import (
    EDITABLE_ATTRIBUTE_STATUSES,
    LISTING_STATUSES,
    REGENERABLE_POSITION_STATUSES,
    Listing,
)
from app.models.listing_title import ListingTitle
from app.models.listing_attribute import ListingAttribute
from app.models.listing_description import ListingDescription
from app.models.listing_image import (
    CANDIDATE_SORT_ORDER_FLOOR,
    COVER_SORT_ORDER,
    GENERATING_STATUS,
    POSITION_KINDS,
    PROMOTABLE_COVER_KINDS,
    ListingImage,
)
from app.models.listing_review_event import (
    REVIEW_ACTION_ATTRIBUTES_EDITED,
    REVIEW_ACTION_IMAGES_APPROVED,
    REVIEW_MODE_BULK,
    REVIEW_MODE_INDIVIDUAL,
    ListingReviewEvent,
)
from app.models.listing_job import ListingJob
from app.models.user import User
from app.models.seller import Seller
from app.schemas.listing import ListingCreate, ListingPage, ListingStatusCounts, ListingSummary
from app.schemas.bulk import BulkItemResult, BulkResult
from app.services.attribute_impact import snapshot_attributes, stale_positions

logger = logging.getLogger(__name__)


def _mensagem_regeneracao_em_andamento(posicoes: list[int]) -> str:
    """Bloqueio das aprovacoes enquanto ha placeholder `generating`. Mensagem
    PROPRIA, nao "estado invalido": o operador precisa saber que e'
    temporario e qual posicao esta sendo refeita."""
    lista = ", ".join(str(p) for p in posicoes)
    return f"Regeneração em andamento na posição {lista}; aguarde a conclusão antes de aprovar."


# Atributos do ML que existem EM DUPLICATA no sistema: o atributo alimenta a
# ficha tecnica (posicao 4) e a coluna homonima do listing
# (`sku_model`/`sku_brand`, vinda do catalogo) alimenta a apresentacao
# (posicao 1). Corrigir um NAO corrige o outro, e nada no codigo os
# sincroniza. Enquanto a duplicacao existir, a tela precisa avisar.
DUPLICATED_CATALOG_ATTRIBUTE_IDS = frozenset({"BRAND", "MODEL"})


class ListingService:
    def __init__(self, db: AsyncSession, seller_id=None) -> None:
        self.db = db
        self.seller_id = seller_id

    async def create(self, data: ListingCreate, user: User, seller: Seller) -> Listing:
        listing = Listing(
            seller_id=seller.id,
            created_by=user.id,
            **data.model_dump(),
        )
        self.db.add(listing)
        await self.db.commit()
        await self.db.refresh(listing)
        return listing

    async def summary_after_commit(self, listing: Listing) -> ListingSummary:
        """Serializa o anuncio DEPOIS de uma acao commitada, com o valor certo.

        Unico caminho dos endpoints de acao que devolvem `ListingSummary`
        (start, retry, select_title, attributes, generate_images, resume_*,
        approve_images, publish, promote_*). O `refresh` custa uma consulta
        e existe por dois motivos: (1) `approved_image_count` e' uma
        column_property que o flush deixava expirada — hoje tem
        `expire_on_flush=False`, entao serializar nao estoura mais, mas o
        valor em memoria e' o do carregamento; (2) em acoes que MUDAM a
        contagem na propria requisicao (aprovar 5 imagens, promover uma
        capa), o operador tem de receber 5, nao o 0 de antes. Reler aqui, uma
        vez, evita repetir a releitura em 11 endpoints. Ver
        tests/test_serializacao_pos_commit.py.

        Atencao: o `refresh` DESCARTA qualquer alteracao ainda nao commitada
        no `listing` e devolve o que esta no banco — chamar so depois do
        commit da acao (hoje os tres pontos que escrevem `listing.status`
        depois do ultimo commit gravaram o mesmo valor por UPDATE antes, entao
        o resultado e' identico).
        """
        await self.db.refresh(listing)
        return ListingSummary.model_validate(listing)

    async def get_or_404(self, listing_id: UUID, seller_id: UUID) -> Listing:
        result = await self.db.execute(
            select(Listing).where(Listing.id == listing_id, Listing.seller_id == seller_id)
        )
        listing = result.scalar_one_or_none()
        if not listing:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Anúncio não encontrado")
        return listing

    async def list_listings(
        self,
        seller_id: UUID,
        filter_status: str | list[str] | None,
        page: int,
        page_size: int,
        search: str | None = None,
    ) -> ListingPage:
        query = select(Listing).where(Listing.seller_id == seller_id)

        # Aceita o parametro unico de hoje (str) e a lista da fila (cada
        # agrupamento junta 3 ou 4 status). Vazios ("" ou lista vazia) nao
        # filtram — mesmo comportamento do `if filter_status` antigo.
        if isinstance(filter_status, str):
            filter_status = [filter_status]
        statuses = [s for s in (filter_status or []) if s]
        if statuses:
            query = query.where(Listing.status.in_(statuses))

        # Mesmo padrao de `ProductService.list_products`: ilike + or_.
        # `sku_description` entra porque, antes de o titulo ser escolhido
        # (draft ate pending_title_approval), `selected_title` e' NULL — e e'
        # justamente com o anuncio parado esperando revisao que o operador o
        # procura. A descricao de origem e' o unico texto sempre preenchido.
        term = (search or "").strip()
        if term:
            like = f"%{term}%"
            query = query.where(
                or_(
                    Listing.sku_external_id.ilike(like),
                    Listing.selected_title.ilike(like),
                    Listing.sku_description.ilike(like),
                    Listing.sku_brand.ilike(like),
                    Listing.mlb_id.ilike(like),
                )
            )

        count_result = await self.db.execute(
            select(func.count()).select_from(query.subquery())
        )
        total = count_result.scalar_one()

        # Desempate por id: created_at e' timestamp da TRANSACAO
        # (server_default=func.now()), entao um lote de batch import cria
        # varios listings com o MESMO created_at. Sem desempate, a ordem
        # entre eles fica a criterio do plano do Postgres, e com
        # OFFSET/LIMIT isso faz um listing aparecer em duas paginas ou em
        # nenhuma. `id` (uuid4) nao tem ordem semantica, mas e' unico e
        # estavel — suficiente pra paginacao ser deterministica.
        query = query.order_by(Listing.created_at.desc(), Listing.id.desc())
        query = query.offset((page - 1) * page_size).limit(page_size)
        result = await self.db.execute(query)
        items = result.scalars().all()

        return ListingPage(
            items=[ListingSummary.model_validate(i) for i in items],
            total=total,
            page=page,
            page_size=page_size,
        )

    async def count_by_status(self, seller_id: UUID) -> ListingStatusCounts:
        """UMA consulta agregada (GROUP BY status) — nunca carrega linhas."""
        result = await self.db.execute(
            select(Listing.status, func.count().label("cnt"))
            .where(Listing.seller_id == seller_id)
            .group_by(Listing.status)
        )
        by_status = {s: 0 for s in LISTING_STATUSES}
        for row in result.all():
            by_status[row.status] = row.cnt
        return ListingStatusCounts(by_status=by_status, total=sum(by_status.values()))

    async def delete(self, listing_id: UUID, seller_id: UUID) -> None:
        listing = await self.get_or_404(listing_id, seller_id)
        if listing.status not in ("draft", "failed"):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Somente anúncios em rascunho ou com falha podem ser excluídos",
            )
        await self.db.delete(listing)
        await self.db.commit()

    async def start_pipeline(self, listing: Listing) -> None:
        if listing.status != "draft":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Pipeline não pode ser iniciado no status '{listing.status}'",
            )
        listing.status = "generating_title"
        await self.db.commit()
        from app.workers.tasks.ai_tasks import generate_title
        generate_title.delay(str(listing.id))

    async def retry_pipeline(self, listing: Listing) -> None:
        if listing.status != "failed":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Retry disponível apenas para anúncios com falha",
            )
        listing.error_message = None
        # Se a categoria já foi prevista, basta o seller corrigir os atributos;
        # não é necessário reger todo o pipeline desde o início.
        if listing.ml_category_id:
            listing.status = "pending_seller_attributes"
            await self.db.commit()
            return
        listing.status = "generating_title"
        await self.db.commit()
        from app.workers.tasks.ai_tasks import generate_title
        generate_title.delay(str(listing.id))

    async def select_title(self, listing: Listing, title_id: UUID) -> None:
        if listing.status != "pending_title_approval":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Seleção de título disponível apenas no status 'pending_title_approval'",
            )
        result = await self.db.execute(
            select(ListingTitle).where(
                ListingTitle.id == title_id, ListingTitle.listing_id == listing.id
            )
        )
        title = result.scalar_one_or_none()
        if not title:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Título não encontrado")

        await self.db.execute(
            update(ListingTitle)
            .where(ListingTitle.listing_id == listing.id)
            .values(selected=False)
        )
        title.selected = True
        listing.selected_title = title.title_text
        listing.status = "predicting_category"
        await self.db.commit()

        from app.workers.tasks.category_tasks import predict_category
        predict_category.delay(str(listing.id))

    @staticmethod
    def _validar_valor(attr: ListingAttribute, item: dict) -> tuple[str | None, str | None]:
        """Valida o valor submetido contra os `allowed_values` da categoria.

        Atributo de lista com valor fora da lista e recusado AQUI, com 422 e a
        lista do que e aceito. Antes, qualquer cliente da API podia gravar
        qualquer texto: o valor entrava no banco, sobrevivia a geracao de imagem
        e de descricao, e so era recusado la na frente pelo ML, com
        `Attribute [X] is not valid, item values [(null:Y)]` — mensagem obscura,
        no momento mais caro, depois de ja ter gastado IA.

        Rejeitar cedo troca isso por um erro claro antes de qualquer gasto.
        """
        value_id = item.get("value_id")
        value_name = item.get("value_name")
        allowed = attr.allowed_values or []

        # Sem lista de valores permitidos o atributo e texto livre (GTIN,
        # MODEL, dimensoes): nada a validar.
        if not allowed or value_name is None:
            return value_id, value_name

        opcoes = [v for v in allowed if isinstance(v, dict)]
        if not opcoes:
            return value_id, value_name

        for v in opcoes:
            nome = v.get("name")
            if value_id and v.get("id") == value_id:
                return v.get("id"), nome
            if nome and str(nome).lower() == str(value_name).lower():
                # Normaliza para o nome exato do ML e resolve o id de brinde:
                # cliente que manda so o nome nao precisa saber o id.
                return v.get("id"), nome

        # Nao casou. So e ERRO quando o atributo e uma ENUMERACAO fechada
        # (`value_type == "list"`). Em `string`, a lista do ML e de sugestoes:
        # ele aceita texto livre e resolve o id sozinho — recusar aqui barrava
        # a marca real do produto. Mesma regra de `category_service`, ver o
        # comentario la para a evidencia (BRAND/Wepink em MLB6284).
        if attr.attribute_type != "list":
            return value_id, value_name

        aceitos = [v.get("name") for v in opcoes]
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Valor {value_name!r} não é válido para o atributo "
                f"'{attr.attribute_name}' ({attr.attribute_id}) nesta categoria. "
                f"Valores aceitos: {', '.join(str(a) for a in aceitos[:15])}"
                + (f" (e mais {len(aceitos) - 15})" if len(aceitos) > 15 else "")
            ),
        )

    async def submit_attributes(self, listing: Listing, submitted: list[dict]) -> None:
        if listing.status != "pending_seller_attributes":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Atributos só podem ser enviados no status 'pending_seller_attributes'",
            )
        for item in submitted:
            result = await self.db.execute(
                select(ListingAttribute).where(
                    ListingAttribute.listing_id == listing.id,
                    ListingAttribute.attribute_id == item["attribute_id"],
                )
            )
            attr = result.scalar_one_or_none()
            if attr:
                value_id, value_name = self._validar_valor(attr, item)
                attr.value_id = value_id
                attr.value_name = value_name
                attr.source = "seller"

        # Se imagens aprovadas e descrição já existem (retry após erro de publicação),
        # pula direto para ready_to_publish sem regenerar tudo.
        approved_img = (await self.db.execute(
            select(ListingImage)
            .where(
                ListingImage.listing_id == listing.id,
                ListingImage.approved == True,
            )
        )).scalars().first()
        description = (await self.db.execute(
            select(ListingDescription).where(ListingDescription.listing_id == listing.id)
        )).scalar_one_or_none()

        new_status = "ready_to_publish" if (approved_img and description) else "pending_description"
        listing.status = new_status
        await self.db.commit()

        # Batch: avança automaticamente para geração de imagens sem esperar o
        # seller clicar. Publicação em lote nunca acontece sozinha: mesmo
        # quando o retry pula direto para 'ready_to_publish', o listing fica
        # parado até ação humana (trigger_publish / bulk_publish).
        if listing.created_via == "batch" and new_status == "pending_description":
            result = await self.db.execute(
                update(Listing)
                .where(
                    Listing.id == listing.id,
                    Listing.status == "pending_description",
                )
                .values(status="generating_images")
                .execution_options(synchronize_session=False)
            )
            await self.db.commit()
            if result.rowcount == 1:
                listing.status = "generating_images"
                from app.workers.tasks.image_tasks import generate_images
                generate_images.delay(str(listing.id))

    @staticmethod
    def _valor_limpo(item: dict) -> dict:
        """Campo apagado pelo operador vira APAGAR, nao string vazia.

        Sem isso, `_validar_valor` trataria `""` como um valor a procurar na
        enumeracao e devolveria um 422 incompreensivel ("Valor '' nao e'
        valido") quando a intencao era limpar o campo.
        """
        nome = item.get("value_name")
        if nome is None or not str(nome).strip():
            return {"attribute_id": item["attribute_id"], "value_id": None, "value_name": None}
        return item

    async def edit_attributes(
        self,
        listing: Listing,
        submitted: list[dict],
        *,
        user_id: UUID,
    ) -> tuple[list[int], list[str]]:
        """Corrige atributo JA gravado e para por ai.

        Nao toca `listing.status`, nao enfileira task, nao apaga imagem
        aprovada nem descricao. `submit_attributes` continua sendo o passo de
        PREENCHIMENTO — ele tolera obrigatorio vazio (e' assim que o anuncio
        entra em `pending_seller_attributes`) e decide a etapa seguinte. Este
        e' o de CORRECAO, e recusa obrigatorio vazio. Sao duas semanticas
        opostas no mesmo dado; por isso dois metodos, e nao um `if` por
        status dentro de um so.

        Tudo que recusa, recusa ANTES de qualquer escrita — mesmo principio
        de `approve_images`.

        Devolve `(stale_positions, duplicated_fields)`: as posicoes de imagem
        cujo texto impresso nao corresponde mais ao banco, e os atributos
        editados que existem em duplicata no catalogo. Avisa; nao regenera
        nada (opcao A do spec) — regenerar por conta propria gastaria chamada
        paga sem decisao humana.

        Spec: docs/superpowers/specs/2026-09-15-editar-atributos-antes-de-publicar.md
        """
        if listing.status not in EDITABLE_ATTRIBUTE_STATUSES:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Correção de atributos indisponível no status '{listing.status}'",
            )
        # Mesma trava das aprovacoes e das promocoes: o worker da regeneracao
        # vai reler os atributos para montar a posicao, entao editar agora
        # decide por sorteio qual versao do texto entra na imagem.
        await self.recusar_se_regeneracao_em_andamento(listing)

        atributos = (await self.db.execute(
            select(ListingAttribute).where(ListingAttribute.listing_id == listing.id)
        )).scalars().all()
        por_id = {a.attribute_id: a for a in atributos}

        antes = snapshot_attributes(atributos)

        # 1) Resolve TUDO antes de escrever: um 422 no terceiro item nao pode
        #    deixar os dois primeiros gravados.
        #
        #    Dedup por `attribute_id` ANTES de resolver: `submitted` com o
        #    MESMO atributo duas vezes nao e' duas edicoes, e' a intencao mais
        #    recente do operador sobrescrevendo a anterior. Sem isso o laco de
        #    escrita aplicava as duas (a ultima vencia de qualquer jeito) mas
        #    `approved_count` contava o mesmo atributo DUAS VEZES — um dado
        #    falso no evento de auditoria (ver docstring de
        #    `ListingReviewEvent.approved_count`, que define esse numero como
        #    "quantos atributos MUDARAM de valor de fato"). Mesmo principio de
        #    `approve_images` (`dict.fromkeys` nos ids); aqui a chave e' o
        #    `attribute_id` e o VALOR mais recente e' o que fica.
        ultimos: dict = {}
        for item in submitted:
            ultimos[item.get("attribute_id")] = item

        resolvidos = []
        for item in ultimos.values():
            attr = por_id.get(item.get("attribute_id"))
            if attr is None:
                continue  # atributo de outra categoria: ignorado, como no PUT
            value_id, value_name = self._validar_valor(attr, self._valor_limpo(item))
            resolvidos.append((attr, value_id, value_name))

        # 2) So o que MUDA de fato. A comparacao e' pelo `value_name` e nao
        #    tambem pelo `value_id` de proposito: em atributo de texto livre
        #    (`string`) o formulario reenvia o nome sem o id, e o id que o ML
        #    resolveu esta gravado. Comparar os dois marcaria isso como
        #    mudanca e APAGARIA um `value_id` valido.
        mudancas = [
            (attr, value_id, value_name)
            for attr, value_id, value_name in resolvidos
            if attr.value_name != value_name
        ]

        # 3) Obrigatorio nao pode ficar vazio depois da edicao.
        esvaziados = sorted(
            attr.attribute_name
            for attr, _vid, value_name in mudancas
            if attr.is_required and value_name is None
        )
        if esvaziados:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    "Atributo obrigatório não pode ficar vazio: "
                    + ", ".join(esvaziados)
                ),
            )

        # 4) Acao que nao faz nada nao e' acao (ver o docstring de
        #    `ListingReviewEvent.approved_count`).
        if not mudancas:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Nenhum atributo foi alterado",
            )

        # 5) Escreve. O antes/depois vive so no log: ainda nao sabemos que
        #    perguntas faremos a esse historico, e tabela agora seria apostar
        #    no formato antes de conhecer o uso.
        for attr, value_id, value_name in mudancas:
            anterior = attr.value_name
            attr.value_id = value_id
            attr.value_name = value_name
            attr.source = "seller"
            logger.info(
                "attribute_edit listing_id=%s sku=%s user_id=%s attribute_id=%s de=%r para=%r",
                listing.id, listing.sku_external_id, user_id,
                attr.attribute_id, anterior, value_name,
            )

        posicoes_existentes = (await self.db.execute(
            select(ListingImage.sort_order).where(
                ListingImage.listing_id == listing.id,
                ListingImage.sort_order.in_(tuple(POSITION_KINDS)),
            )
        )).scalars().all()
        stale = stale_positions(antes, snapshot_attributes(atributos), posicoes_existentes)

        self.db.add(ListingReviewEvent(
            listing_id=listing.id,
            user_id=user_id,
            action=REVIEW_ACTION_ATTRIBUTES_EDITED,
            mode=REVIEW_MODE_INDIVIDUAL,
            approved_count=len(mudancas),
            # Nao ha cronometro nesta tela, e estimar seria inventar dado.
            review_seconds=None,
        ))

        duplicados = sorted({
            attr.attribute_id
            for attr, _vid, _vn in mudancas
            if attr.attribute_id in DUPLICATED_CATALOG_ATTRIBUTE_IDS
        })
        logger.info(
            "attribute_edit listing_id=%s sku=%s user_id=%s alterados=%d stale_positions=%s",
            listing.id, listing.sku_external_id, user_id, len(mudancas), stale,
        )
        await self.db.commit()
        return stale, duplicados

    async def trigger_image_generation(self, listing: Listing) -> None:
        if listing.status != "pending_description":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Geração de imagens indisponível no status '{listing.status}'",
            )
        listing.status = "generating_images"
        await self.db.commit()
        from app.workers.tasks.image_tasks import generate_images
        generate_images.delay(str(listing.id))

    async def resume_raw_photos(self, listing: Listing) -> None:
        """Retomada MANUAL de `pending_raw_photos`, sem esperar o beat.

        Mesma logica da tarefa periodica (`try_resume_raw_photos`): sonda o
        bucket e, se as fotos minimas existem, reentra em `generate_images`.
        Se ainda faltam, 409 dizendo quais arquivos sao obrigatorios.
        """
        from app.services import raw_photo_standby_service as standby

        if listing.status != standby.PENDING_RAW_PHOTOS:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Retomada por fotos brutas indisponível no status '{listing.status}'",
            )
        if not await standby.try_resume_raw_photos(self.db, listing):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=standby.missing_photos_message(listing.sku_external_id or "?"),
            )

    async def resume_ai_engine(self, listing: Listing) -> None:
        """Retomada MANUAL de `pending_ai_engine` (credito recarregado, chave
        trocada), sem esperar o beat. Nao ha pre-checagem: redispara e o
        worker decide."""
        from app.services import ai_engine_standby_service as standby

        if listing.status != standby.PENDING_AI_ENGINE:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Retomada por motor de IA indisponível no status '{listing.status}'",
            )
        if not await standby.try_resume_ai_engine(self.db, listing):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Este anúncio acabou de ser retomado por outra ação; aguarde.",
            )

    async def regenerate_position(self, listing: Listing, posicao: int) -> ListingImage:
        """Regenera UMA posicao (0..4) do esquema de 5 posicoes.

        Insere um placeholder `ListingImage(status="generating")` e faz
        commit ANTES de enfileirar: o placeholder e' a trava contra duplo
        clique (indice unico parcial `uq_listing_images_generating_slot`) e o
        que a tela le como "gerando". Spec:
        docs/superpowers/specs/2026-09-12-regenerar-posicao.md.

        Aceita `pending_image_approval` e `ready_to_publish`
        (`REGENERABLE_POSITION_STATUSES`) e trata os dois de forma diferente:

          pending_image_approval  o anuncio NAO muda de status, e posicao
                                  aprovada e' recusada com 409 — imagem
                                  aprovada nao e' substituida por tras do
                                  operador.
          ready_to_publish        as 5 posicoes estao aprovadas ali, entao
                                  recusar posicao aprovada devolveria 409 em
                                  100% dos casos. Regenerar DESAPROVA a
                                  posicao pedida e devolve o anuncio a
                                  `pending_image_approval`. Nao e'
                                  substituicao por tras de ninguem: e' o
                                  clique do proprio operador, e a invariante
                                  "publica so imagem aprovada" fica intacta.
                                  O retorno a revisao tambem e' o que faz o
                                  guard de status do worker
                                  (`_regenerate_position_async`) aceitar a
                                  task.
        """
        if listing.status not in REGENERABLE_POSITION_STATUSES:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Regeneração de imagem disponível apenas nos status "
                    "'pending_image_approval' e 'ready_to_publish' "
                    f"(atual: '{listing.status}')"
                ),
            )
        if posicao not in POSITION_KINDS:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Posição inválida: informe um número de 0 a 4",
            )
        ocupantes = (
            await self.db.execute(
                select(ListingImage).where(
                    ListingImage.listing_id == listing.id,
                    ListingImage.sort_order == posicao,
                )
            )
        ).scalars().all()
        status_original = listing.status
        desaprovadas: list[ListingImage] = []
        if listing.status == "ready_to_publish":
            # Desaprova SO esta posicao e devolve o anuncio a revisao, na
            # mesma transacao do placeholder. `status="uploaded"` (e nao
            # "rejected") porque a linha antiga volta a ser exatamente o que
            # era: gerada e ainda nao julgada — se a regeneracao falhar, ela
            # continua sendo a candidata daquela posicao.
            #
            # A volta acontece MESMO quando a posicao ja nao estava aprovada:
            # um anuncio pode chegar a `ready_to_publish` com 4 de 5, e sem
            # a mudanca de status o worker (`image_tasks.py:708`) apagaria o
            # placeholder ao ver o anuncio fora de `pending_image_approval` e
            # nada mais aconteceria — um no-op silencioso: a tela mostra
            # "gerando", o placeholder some sozinho, nenhuma imagem nova
            # aparece e nenhum erro e mostrado.
            desaprovadas = [img for img in ocupantes if img.approved]
            for img in desaprovadas:
                img.approved = False
                img.status = "uploaded"
            listing.status = "pending_image_approval"
        elif any(img.approved for img in ocupantes):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"A posição {posicao} já está aprovada; imagem aprovada não é regenerada",
            )

        placeholder = ListingImage(
            listing_id=listing.id,
            status=GENERATING_STATUS,
            approved=False,
            sort_order=posicao,
            kind=POSITION_KINDS[posicao],
            source_sku=listing.sku_external_id,
        )
        self.db.add(placeholder)
        try:
            await self.db.commit()
        except IntegrityError:
            # `uq_listing_images_generating_slot`: ja existe placeholder
            # `generating` nesta posicao — outro clique venceu.
            await self.db.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Regeneração em andamento na posição {posicao}; aguarde.",
            )
        await self.db.refresh(placeholder)

        from app.workers.tasks.image_tasks import regenerate_position
        try:
            regenerate_position.delay(str(listing.id), str(placeholder.id))
        except Exception as exc:  # broker fora (Redis): a task NUNCA foi
            # enfileirada, entao nenhum worker vai rodar para apaga-la (isso
            # e' diferente do guard de status do worker em
            # `image_tasks.py:708`, que so age quando a task CHEGA a rodar).
            # Sem este delete, o placeholder seria uma trava eterna — bloqueia
            # as aprovacoes e as promocoes do anuncio, e o indice unico
            # parcial recusa qualquer nova tentativa nesta posicao.
            await self.db.delete(placeholder)
            if status_original == "ready_to_publish":
                # Compensacao por verdade: a mensagem de 503 abaixo afirma
                # que "o pedido nao foi registrado". Sem desfazer a
                # desaprovacao e a volta de status feitas acima, isso seria
                # mentira — o anuncio teria mesmo saido de `ready_to_publish`
                # e perdido uma aprovacao. Mesmo commit que apaga o
                # placeholder.
                for img in desaprovadas:
                    img.approved = True
                    img.status = "approved"
                listing.status = "ready_to_publish"
            try:
                await self.db.commit()
            except IntegrityError as integrity_exc:
                # Restaurar `approved=True` numa posicao de capa/ficha toca
                # `uq_listing_images_cover_slot`/`_specs_slot`. Na pratica o
                # slot esta livre (foi liberado nesta mesma requisicao), mas
                # se a compensacao estourar mesmo assim, o 503 nao pode virar
                # 500 por cima dele.
                await self.db.rollback()
                logger.error(
                    "regen_posicao listing_id=%s posicao=%s result=compensacao_falhou reason=%s",
                    listing.id, posicao, integrity_exc,
                )
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail=(
                        "Fila de processamento indisponível e não foi possível desfazer a "
                        "mudança; o anúncio voltou para revisão de imagens — aprove as "
                        "imagens de novo."
                    ),
                )
            logger.error(
                "regen_posicao listing_id=%s posicao=%s result=fila_indisponivel reason=%s",
                listing.id, posicao, exc,
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    "Fila de processamento indisponível no momento; o pedido não foi "
                    "registrado. Tente de novo em instantes."
                ),
            )
        return placeholder

    async def recusar_se_regeneracao_em_andamento(self, listing: Listing) -> None:
        """409 com a mensagem das aprovacoes enquanto houver placeholder `generating`.

        Usado pelos endpoints de promocao (`promote_cover`/`promote_specs`):
        promover aprova uma linha, e `promote_cover` rebaixa quem ocupa
        `sort_order` 0 — inclusive o placeholder `cover_ai` da regeneracao, que
        iria parar em 90. O worker, ao terminar, encontraria o placeholder fora
        do esquema de 5 posicoes.
        """
        posicoes = sorted(
            (
                await self.db.execute(
                    select(ListingImage.sort_order).where(
                        ListingImage.listing_id == listing.id,
                        ListingImage.status == GENERATING_STATUS,
                    )
                )
            ).scalars().all()
        )
        if posicoes:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=_mensagem_regeneracao_em_andamento(posicoes),
            )

    async def regenerate_description(self, listing: Listing) -> None:
        """Refaz a descricao de um anuncio ja pronto, sem tocar nas imagens.

        Existe por causa de uma corrupcao de auditoria. `generate_description`
        so era disparada por `approve_images`/`bulk_approve_images`, entao a
        UNICA forma de refazer a descricao depois de corrigir um atributo era
        reaprovar as imagens — e cada reaprovacao grava um
        `listing_review_events` afirmando revisao humana de imagens que
        ninguem olhou. O dado criado para PROVAR revisao virava prova falsa.

        Este caminho NAO grava evento: nao houve revisao de imagem nenhuma.

        O status muda para `generating_description` e volta sozinho para
        `ready_to_publish` no fim da task. Mudar e' o correto aqui: o anuncio
        ESTA gerando descricao, e esconder isso deixaria a tela mentindo. A
        regra "editar nao avanca etapa" vale para a edicao de atributos, nao
        para esta acao explicita do operador.
        """
        if listing.status != "ready_to_publish":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Regeneração da descrição indisponível no status '{listing.status}'",
            )
        # UPDATE atomico + dispatch, o padrao dos gatilhos do projeto: duas
        # requisicoes simultaneas nao podem enfileirar duas geracoes.
        result = await self.db.execute(
            sa_update(Listing)
            .where(Listing.id == listing.id, Listing.status == "ready_to_publish")
            .values(status="generating_description")
            .execution_options(synchronize_session=False)
        )
        await self.db.commit()
        if result.rowcount != 1:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Este anúncio acabou de mudar de estado; recarregue a página.",
            )
        listing.status = "generating_description"

        from app.workers.tasks.ai_tasks import generate_description
        try:
            generate_description.delay(str(listing.id))
        except Exception as exc:
            # Broker fora: sem a task, o anuncio ficaria preso em
            # `generating_description` para sempre — status que nenhum beat
            # varre e de onde nenhuma acao humana sai.
            await self.db.execute(
                sa_update(Listing)
                .where(Listing.id == listing.id, Listing.status == "generating_description")
                .values(status="ready_to_publish")
                .execution_options(synchronize_session=False)
            )
            await self.db.commit()
            listing.status = "ready_to_publish"
            logger.error(
                "regen_descricao listing_id=%s result=fila_indisponivel reason=%s",
                listing.id, exc,
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    "Fila de processamento indisponível no momento; o pedido não foi "
                    "registrado. Tente de novo em instantes."
                ),
            )

    async def approve_images(
        self,
        listing: Listing,
        approved_ids: list[UUID],
        review_seconds: int | None = None,
        *,
        user_id: UUID,
    ) -> None:
        if listing.status != "pending_image_approval":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Aprovação de imagens disponível apenas no status 'pending_image_approval'",
            )
        if not approved_ids:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Pelo menos uma imagem deve ser aprovada",
            )
        result = await self.db.execute(
            select(ListingImage)
            .where(ListingImage.listing_id == listing.id)
        )
        images = result.scalars().all()

        # Regeneracao de posicao em andamento: aprovar agora marcaria o
        # placeholder como `rejected` e o worker desistiria depois de ja ter
        # pago a chamada (ou publicaria sem a posicao refeita). Bloqueia com
        # mensagem propria, antes de qualquer escrita. Usa a lista ja
        # carregada — nenhuma consulta a mais.
        em_regeneracao = sorted(img.sort_order for img in images if img.status == GENERATING_STATUS)
        if em_regeneracao:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=_mensagem_regeneracao_em_andamento(em_regeneracao),
            )

        approved_set = set(approved_ids)
        by_id = {img.id: img for img in images}
        # Recusa ANTES de qualquer escrita: uma lista so com ids de OUTRO
        # anuncio passava pelo guard de lista vazia, cada id era ignorado em
        # silencio no laco abaixo, e o anuncio avancava com zero aprovadas —
        # e com todas as imagens dele marcadas `rejected` pelo segundo laco.
        if approved_set.isdisjoint(by_id):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Nenhum dos ids informados corresponde a uma imagem deste anúncio",
            )
        approved_ml_ids = []

        # A posicao `COVER_SORT_ORDER` (0) e RESERVADA a kind de capa.
        #
        # A ordem relativa que o operador escolheu e preservada; o que muda e
        # que, se o primeiro aprovado nao for uma capa, a numeracao comeca em 1
        # e o 0 fica vago ate existir uma capa.
        #
        # Isso e invariante estrutural, nao desempate tardio na publicacao.
        # Enquanto uma `individual` podia cair em 0, promover uma capa deixava
        # DUAS linhas empatadas em 0 — `promote_cover` so rebaixa kinds de capa,
        # de proposito, para nunca despublicar foto aprovada — e
        # `publish_service` ordena por `sort_order` sem criterio de desempate,
        # entao qual imagem virava a capa do anuncio era arbitrario.
        #
        # O 0 vago nao muda o anuncio: `publish_service` monta o array de fotos
        # ordenando por `sort_order`, e o ML usa a POSICAO no array, nao o
        # numero. Comecar em 1 publica exatamente a mesma sequencia.
        #
        # `dict.fromkeys` deduplica preservando a ordem: id repetido na
        # requisicao nao pode consumir duas posicoes.
        next_order = COVER_SORT_ORDER
        approved_count = 0
        for uid in dict.fromkeys(approved_ids):
            img = by_id.get(uid)
            if img is None:
                continue  # id que nao e deste anuncio: ignorado, como antes
            if next_order == COVER_SORT_ORDER and img.kind not in PROMOTABLE_COVER_KINDS:
                next_order = COVER_SORT_ORDER + 1
            img.approved = True
            img.sort_order = next_order
            img.status = "approved"
            approved_count += 1
            if img.ml_picture_id:
                approved_ml_ids.append(img.ml_picture_id)
            next_order += 1

        for img in images:
            if img.id not in approved_set:
                img.approved = False
                img.status = "rejected"

        # O indice SKU→imagem (`ProductImage`) nao recebe mais linhas desde a
        # remocao do caminho antigo (2026-09-10) — nenhum caminho o le nem o
        # escreve. A tabela e o model ficam como registro historico dos SKUs
        # 37/38 ate decisao explicita de apagar.

        # Evento de revisao humana, na MESMA transacao da aprovacao: gravado
        # ANTES do commit que persiste `approved`/`status`, pra aprovacao sem
        # evento (ou evento sem aprovacao) ser impossivel.
        self.db.add(ListingReviewEvent(
            listing_id=listing.id,
            user_id=user_id,
            action=REVIEW_ACTION_IMAGES_APPROVED,
            mode=REVIEW_MODE_INDIVIDUAL,
            approved_count=approved_count,
            review_seconds=review_seconds,
        ))

        listing.status = "generating_description"
        await self.db.commit()

        from app.workers.tasks.ai_tasks import generate_description
        generate_description.delay(str(listing.id))

    async def trigger_publish(self, listing: Listing) -> None:
        if listing.status != "ready_to_publish":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Publicação indisponível no status '{listing.status}'",
            )
        listing.status = "publishing"
        await self.db.commit()

        from app.workers.tasks.publish_tasks import publish_listing
        publish_listing.delay(str(listing.id))

    # ------------------------------------------------------------------
    # Bulk methods
    # ------------------------------------------------------------------

    @staticmethod
    def _bulk_result(results: list[BulkItemResult]) -> BulkResult:
        return BulkResult(
            processed=sum(1 for r in results if r.success),
            failed=sum(1 for r in results if not r.success),
            results=results,
        )

    async def bulk_start_pipeline(self, listing_ids: list) -> BulkResult:
        results: list[BulkItemResult] = []
        for lid in listing_ids:
            try:
                r = await self.db.execute(
                    sa_update(Listing)
                    .where(Listing.id == lid, Listing.seller_id == self.seller_id, Listing.status == "draft")
                    .values(status="generating_title")
                    .execution_options(synchronize_session=False)
                )
                await self.db.commit()
                if r.rowcount == 0:
                    results.append(BulkItemResult(listing_id=lid, success=False, error="estado inválido"))
                    continue
                from app.workers.tasks.ai_tasks import generate_title
                generate_title.delay(str(lid))
                results.append(BulkItemResult(listing_id=lid, success=True))
            except Exception as e:
                await self.db.rollback()
                results.append(BulkItemResult(listing_id=lid, success=False, error=str(e)))
        return self._bulk_result(results)

    async def bulk_approve_titles(self, listing_ids: list) -> BulkResult:
        results: list[BulkItemResult] = []
        for lid in listing_ids:
            try:
                r = await self.db.execute(
                    select(Listing).where(Listing.id == lid, Listing.seller_id == self.seller_id)
                )
                listing = r.scalar_one_or_none()
                if not listing or listing.status != "pending_title_approval":
                    results.append(BulkItemResult(listing_id=lid, success=False, error="estado inválido"))
                    continue
                title_r = await self.db.execute(
                    select(ListingTitle)
                    .where(ListingTitle.listing_id == lid)
                    .order_by(ListingTitle.ai_score.desc().nulls_last(), ListingTitle.created_at.asc())
                    .limit(1)
                )
                top = title_r.scalar_one_or_none()
                if not top:
                    results.append(BulkItemResult(listing_id=lid, success=False, error="nenhum título encontrado"))
                    continue
                listing.selected_title = top.title_text
                top.selected = True
                listing.status = "predicting_category"
                await self.db.commit()
                from app.workers.tasks.category_tasks import predict_category
                predict_category.delay(str(lid))
                results.append(BulkItemResult(listing_id=lid, success=True))
            except Exception as e:
                await self.db.rollback()
                results.append(BulkItemResult(listing_id=lid, success=False, error=str(e)))
        return self._bulk_result(results)

    async def bulk_reject_titles(self, listing_ids: list) -> BulkResult:
        results: list[BulkItemResult] = []
        for lid in listing_ids:
            try:
                r = await self.db.execute(
                    select(Listing).where(Listing.id == lid, Listing.seller_id == self.seller_id)
                )
                listing = r.scalar_one_or_none()
                if not listing or listing.status != "pending_title_approval":
                    results.append(BulkItemResult(listing_id=lid, success=False, error="estado inválido"))
                    continue
                await self.db.execute(
                    sa_delete(ListingTitle).where(ListingTitle.listing_id == lid)
                )
                listing.selected_title = None
                listing.status = "draft"
                await self.db.commit()
                results.append(BulkItemResult(listing_id=lid, success=True))
            except Exception as e:
                await self.db.rollback()
                results.append(BulkItemResult(listing_id=lid, success=False, error=str(e)))
        return self._bulk_result(results)

    async def bulk_approve_images(self, listing_ids: list, *, user_id: UUID) -> BulkResult:
        results: list[BulkItemResult] = []
        for lid in listing_ids:
            try:
                r = await self.db.execute(
                    select(Listing).where(Listing.id == lid, Listing.seller_id == self.seller_id)
                )
                listing = r.scalar_one_or_none()
                if not listing or listing.status != "pending_image_approval":
                    results.append(BulkItemResult(listing_id=lid, success=False, error="estado inválido"))
                    continue

                # Aprova o que esta na galeria E subiu ao ML — mesmo criterio
                # da publicacao (`publish_service` so manda `approved and
                # ml_picture_id`). Candidatas de IA sob demanda
                # (sort_order >= CANDIDATE_SORT_ORDER_FLOOR) ficam de fora: elas
                # nascem `approved=False` de proposito e so viram capa/ficha por
                # acao humana explicita (`promote_cover`/`promote_specs`). O
                # filtro de posicao e' por POSICAO, nao por `kind` — `cover_ai`/
                # `specs_ai` sao os MESMOS kinds das posicoes oficiais 0 e 4,
                # entao um filtro por kind deixaria a capa e a ficha oficiais de
                # fora da aprovacao. Ver a docstring de `CANDIDATE_SORT_ORDER_FLOOR`.
                # `ml_picture_id IS NOT NULL` e' o que impede a capa reprovada no
                # QA (que fica `ml_picture_id=None`) e o fallback deterministico
                # do mesmo slot de colidirem no indice unico
                # `uq_listing_images_cover_slot`: a reprovada simplesmente nao
                # entra no UPDATE e continua `approved=False`.
                #
                # Mesmo bloqueio do individual, com item falho e mensagem
                # propria em vez de derrubar o lote. A cerca vive DENTRO do
                # UPDATE: `NOT EXISTS` de placeholder `generating` deste
                # anuncio — atomica com a escrita (sem janela entre checar e
                # aprovar) e sem statement novo antes do UPDATE, que
                # `test_bulk_service` fixa como o 2o da sessao. `aliased` e'
                # obrigatorio: sem ele o SQLAlchemy correlaciona a subconsulta
                # com a propria tabela do UPDATE e a cerca passa a olhar so a
                # linha corrente.
                placeholder = aliased(ListingImage)
                sem_regeneracao = ~(
                    select(placeholder.id)
                    .where(placeholder.listing_id == lid, placeholder.status == GENERATING_STATUS)
                    .exists()
                )
                update_result = await self.db.execute(
                    sa_update(ListingImage)
                    .where(
                        ListingImage.listing_id == lid,
                        ListingImage.sort_order < CANDIDATE_SORT_ORDER_FLOOR,
                        ListingImage.ml_picture_id.isnot(None),
                        sem_regeneracao,
                    )
                    .values(approved=True)
                    .execution_options(synchronize_session=False)
                )
                # Nada bateu o filtro: ou toda posicao reprovou no QA, ou ha
                # regeneracao em andamento (a cerca zera o UPDATE). Rollback
                # antes de seguir, e uma consulta separa os dois casos com a
                # mensagem certa. `sorted(...)` ITERA o resultado de proposito
                # (ver Task 6 do plano 2026-09-12-regenerar-posicao): nao
                # trocar por `if r:`.
                if update_result.rowcount == 0:
                    await self.db.rollback()
                    em_regeneracao = sorted(
                        (
                            await self.db.execute(
                                select(ListingImage.sort_order).where(
                                    ListingImage.listing_id == lid,
                                    ListingImage.status == GENERATING_STATUS,
                                )
                            )
                        ).scalars().all()
                    )
                    erro = (
                        _mensagem_regeneracao_em_andamento(em_regeneracao)
                        if em_regeneracao else "nenhuma imagem aprovável"
                    )
                    results.append(BulkItemResult(listing_id=lid, success=False, error=erro))
                    continue

                # Evento de revisao humana, na MESMA transacao da aprovacao —
                # mesma regra do individual, so que `mode="bulk"` e
                # `review_seconds` sempre NULL (nunca estima/reparte tempo).
                # Se o UPDATE acima estourar o indice unico de slot (capa/
                # ficha duplicada), a excecao interrompe antes daqui e o
                # `except` abaixo faz rollback — nem evento, nem aprovacao.
                self.db.add(ListingReviewEvent(
                    listing_id=listing.id,
                    user_id=user_id,
                    action=REVIEW_ACTION_IMAGES_APPROVED,
                    mode=REVIEW_MODE_BULK,
                    approved_count=update_result.rowcount,
                    review_seconds=None,
                ))

                listing.status = "generating_description"
                await self.db.commit()
                from app.workers.tasks.ai_tasks import generate_description
                generate_description.delay(str(lid))
                results.append(BulkItemResult(listing_id=lid, success=True))
            except Exception as e:
                await self.db.rollback()
                results.append(BulkItemResult(listing_id=lid, success=False, error=str(e)))
        return self._bulk_result(results)

    async def bulk_generate_images(self, listing_ids: list) -> BulkResult:
        results: list[BulkItemResult] = []
        for lid in listing_ids:
            try:
                r = await self.db.execute(
                    sa_update(Listing)
                    .where(
                        Listing.id == lid,
                        Listing.seller_id == self.seller_id,
                        Listing.status == "pending_description",
                    )
                    .values(status="generating_images")
                    .execution_options(synchronize_session=False)
                )
                await self.db.commit()
                if r.rowcount == 0:
                    results.append(BulkItemResult(listing_id=lid, success=False, error="estado inválido"))
                    continue
                from app.workers.tasks.image_tasks import generate_images
                generate_images.delay(str(lid))
                results.append(BulkItemResult(listing_id=lid, success=True))
            except Exception as e:
                await self.db.rollback()
                results.append(BulkItemResult(listing_id=lid, success=False, error=str(e)))
        return self._bulk_result(results)

    async def bulk_publish(self, listing_ids: list) -> BulkResult:
        results: list[BulkItemResult] = []
        for lid in listing_ids:
            try:
                r = await self.db.execute(
                    sa_update(Listing)
                    .where(
                        Listing.id == lid,
                        Listing.seller_id == self.seller_id,
                        Listing.status == "ready_to_publish",
                    )
                    .values(status="publishing")
                    .execution_options(synchronize_session=False)
                )
                await self.db.commit()
                if r.rowcount == 0:
                    results.append(BulkItemResult(listing_id=lid, success=False, error="estado inválido"))
                    continue
                from app.workers.tasks.publish_tasks import publish_listing
                publish_listing.delay(str(lid))
                results.append(BulkItemResult(listing_id=lid, success=True))
            except Exception as e:
                await self.db.rollback()
                results.append(BulkItemResult(listing_id=lid, success=False, error=str(e)))
        return self._bulk_result(results)

    async def bulk_fill_attribute(
        self,
        listing_ids: list,
        attribute_id: str,
        value_name: str,
        value_id: str | None,
    ) -> BulkResult:
        results: list[BulkItemResult] = []
        for lid in listing_ids:
            try:
                r = await self.db.execute(
                    select(Listing).where(
                        Listing.id == lid,
                        Listing.seller_id == self.seller_id,
                        Listing.status.in_(["pending_seller_attributes", "pending_description"]),
                    )
                )
                listing = r.scalar_one_or_none()
                if not listing:
                    results.append(BulkItemResult(listing_id=lid, success=False, error="estado inválido"))
                    continue
                attr_r = await self.db.execute(
                    sa_update(ListingAttribute)
                    .where(
                        ListingAttribute.listing_id == lid,
                        ListingAttribute.attribute_id == attribute_id,
                    )
                    .values(value_name=value_name, value_id=value_id)
                    .execution_options(synchronize_session=False)
                )
                if attr_r.rowcount == 0:
                    results.append(BulkItemResult(listing_id=lid, success=False, error="atributo não encontrado"))
                    continue
                # Advance status if all required attrs are now filled
                unfilled_r = await self.db.execute(
                    select(ListingAttribute).where(
                        ListingAttribute.listing_id == lid,
                        ListingAttribute.is_required == True,
                        ListingAttribute.value_name.is_(None),
                    )
                )
                if not unfilled_r.scalars().all() and listing.status == "pending_seller_attributes":
                    listing.status = "pending_description"
                await self.db.commit()
                results.append(BulkItemResult(listing_id=lid, success=True))
            except Exception as e:
                await self.db.rollback()
                results.append(BulkItemResult(listing_id=lid, success=False, error=str(e)))
        return self._bulk_result(results)
