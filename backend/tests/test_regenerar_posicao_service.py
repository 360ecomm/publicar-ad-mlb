"""Regeneracao de UMA posicao — service, bloqueio das aprovacoes e rota.
Sem banco (sempre roda). O comportamento com linhas reais esta em
`test_regenerar_posicao_pg.py`."""
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError


def _listing(status="pending_image_approval"):
    listing = MagicMock()
    listing.id = uuid.uuid4(); listing.seller_id = uuid.uuid4()
    listing.status = status; listing.sku_external_id = "38"
    return listing


def _db_com_linhas(linhas):
    db = AsyncMock()
    r = MagicMock(); r.scalars.return_value.all.return_value = list(linhas)
    db.execute = AsyncMock(return_value=r)
    db.add = MagicMock(); db.commit = AsyncMock(); db.rollback = AsyncMock(); db.refresh = AsyncMock()
    return db


def _linha(approved):
    img = MagicMock(); img.approved = approved; img.status = "uploaded"; return img


class TestRegenerarPosicaoService:
    @pytest.mark.asyncio
    async def test_recusa_fora_de_pending_image_approval_antes_de_consultar(self):
        from app.services.listing_service import ListingService

        db = _db_com_linhas([])
        svc = ListingService(db)
        with patch("app.workers.tasks.image_tasks.regenerate_position") as task:
            with pytest.raises(HTTPException) as exc:
                await svc.regenerate_position(_listing("ready_to_publish"), 2)
        assert exc.value.status_code == 409
        assert "pending_image_approval" in exc.value.detail and "ready_to_publish" in exc.value.detail
        db.execute.assert_not_awaited(); task.delay.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("posicao", [-1, 5, 90])
    async def test_recusa_posicao_fora_de_0_a_4(self, posicao):
        from app.services.listing_service import ListingService

        db = _db_com_linhas([])
        with pytest.raises(HTTPException) as exc:
            await ListingService(db).regenerate_position(_listing(), posicao)
        assert exc.value.status_code == 422
        db.execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_recusa_posicao_aprovada_sem_criar_placeholder_nem_enfileirar(self):
        from app.services.listing_service import ListingService

        db = _db_com_linhas([_linha(approved=True)])
        with patch("app.workers.tasks.image_tasks.regenerate_position") as task:
            with pytest.raises(HTTPException) as exc:
                await ListingService(db).regenerate_position(_listing(), 2)
        assert exc.value.status_code == 409 and "aprovada" in exc.value.detail
        db.add.assert_not_called(); db.commit.assert_not_awaited(); task.delay.assert_not_called()

    @pytest.mark.asyncio
    async def test_cria_placeholder_commita_e_enfileira_nessa_ordem(self):
        from app.models.listing_image import ListingImage
        from app.services.listing_service import ListingService

        listing = _listing()
        db = _db_com_linhas([_linha(approved=False)])
        ordem = []
        db.commit = AsyncMock(side_effect=lambda: ordem.append("commit"))
        with patch("app.workers.tasks.image_tasks.regenerate_position") as task:
            task.delay = MagicMock(side_effect=lambda *a: ordem.append("delay"))
            placeholder = await ListingService(db).regenerate_position(listing, 2)

        assert isinstance(placeholder, ListingImage)
        assert (placeholder.status, placeholder.kind, placeholder.sort_order, placeholder.approved) == (
            "generating", "benefits_ai", 2, False)
        assert placeholder.listing_id == listing.id and placeholder.source_sku == "38"
        db.add.assert_called_once_with(placeholder)
        assert ordem == ["commit", "delay"], "enfileira so DEPOIS do commit"
        task.delay.assert_called_once_with(str(listing.id), str(placeholder.id))

    @pytest.mark.asyncio
    @pytest.mark.parametrize("posicao,kind", [(0, "cover_ai"), (1, "presentation_ai"), (3, "detail_ai"), (4, "specs_ai")])
    async def test_kind_do_placeholder_e_o_da_posicao(self, posicao, kind):
        from app.services.listing_service import ListingService

        db = _db_com_linhas([])
        with patch("app.workers.tasks.image_tasks.regenerate_position"):
            placeholder = await ListingService(db).regenerate_position(_listing(), posicao)
        assert placeholder.kind == kind and placeholder.sort_order == posicao

    @pytest.mark.asyncio
    async def test_segundo_clique_vira_409_proprio_sem_enfileirar(self):
        from app.services.listing_service import ListingService

        db = _db_com_linhas([_linha(approved=False)])
        db.commit = AsyncMock(side_effect=IntegrityError("INSERT", {}, Exception("uq_listing_images_generating_slot")))
        with patch("app.workers.tasks.image_tasks.regenerate_position") as task:
            with pytest.raises(HTTPException) as exc:
                await ListingService(db).regenerate_position(_listing(), 2)
        assert exc.value.status_code == 409
        assert exc.value.detail == "Regeneração em andamento na posição 2; aguarde."
        db.rollback.assert_awaited_once(); task.delay.assert_not_called()

    @pytest.mark.asyncio
    async def test_broker_fora_apaga_o_placeholder_e_devolve_503(self):
        """Sem task, o placeholder viraria trava eterna: bloqueia as aprovacoes
        do anuncio e o indice unico parcial recusa qualquer nova tentativa na
        posicao. O pedido tem que ser desfeito."""
        from app.services.listing_service import ListingService

        db = _db_com_linhas([_linha(approved=False)])
        commits = []
        db.commit = AsyncMock(side_effect=lambda: commits.append(1))
        with patch("app.workers.tasks.image_tasks.regenerate_position") as task:
            task.delay = MagicMock(side_effect=Exception("redis down"))
            with pytest.raises(HTTPException) as exc:
                await ListingService(db).regenerate_position(_listing(), 2)

        assert exc.value.status_code == 503
        assert "não foi registrado" in exc.value.detail
        db.delete.assert_awaited_once()
        (apagado,) = db.delete.await_args.args
        assert apagado.status == "generating" and apagado.sort_order == 2
        assert len(commits) == 2, "commit do placeholder + commit da remocao"


def test_rota_declarada_com_202_e_image_out():
    from app.main import app

    rotas = {
        (route.path, tuple(sorted(route.methods))): route
        for route in app.routes if getattr(route, "methods", None)
    }
    rota = rotas[("/api/v1/listings/{listing_id}/images/positions/{posicao}/regenerate", ("POST",))]
    assert rota.status_code == 202
    assert rota.response_model.__name__ == "ImageOut"


class TestAprovacaoBloqueadaDuranteRegeneracao:
    def _img(self, sort_order, status, approved=False):
        img = MagicMock(); img.id = uuid.uuid4(); img.sort_order = sort_order
        img.status = status; img.approved = approved; img.kind = "benefits_ai"; img.ml_picture_id = "p"
        return img

    @pytest.mark.asyncio
    async def test_approve_images_409_com_mensagem_propria(self):
        from app.services.listing_service import ListingService

        listing = _listing()
        normal, gerando = self._img(1, "uploaded"), self._img(2, "generating")
        db = _db_com_linhas([normal, gerando])
        with patch("app.workers.tasks.ai_tasks.generate_description") as gen:
            with pytest.raises(HTTPException) as exc:
                await ListingService(db).approve_images(listing, [normal.id], user_id=uuid.uuid4())
        assert exc.value.status_code == 409
        assert exc.value.detail == "Regeneração em andamento na posição 2; aguarde a conclusão antes de aprovar."
        assert listing.status == "pending_image_approval"
        db.add.assert_not_called(), "nenhum evento de revisao"
        db.commit.assert_not_awaited(); gen.delay.assert_not_called()
        assert normal.approved is False and gerando.status == "generating"

    @pytest.mark.asyncio
    async def test_approve_images_lista_todas_as_posicoes_em_regeneracao(self):
        from app.services.listing_service import ListingService

        db = _db_com_linhas([self._img(4, "generating"), self._img(0, "generating"), self._img(1, "uploaded")])
        with pytest.raises(HTTPException) as exc:
            await ListingService(db).approve_images(_listing(), [uuid.uuid4()], user_id=uuid.uuid4())
        assert "posição 0, 4;" in exc.value.detail

    def _db_bulk(self, listing, rowcount, em_regeneracao):
        """Statements de `bulk_approve_images`, na ordem: 1 SELECT Listing,
        2 UPDATE (com a cerca NOT EXISTS dentro), 3 (so quando rowcount == 0)
        SELECT das posicoes em regeneracao."""
        db = AsyncMock(); statements = []

        async def execute_side(stmt):
            statements.append(stmt)
            r = MagicMock()
            if len(statements) == 1:
                r.scalar_one_or_none = MagicMock(return_value=listing)
            elif len(statements) == 2:
                r.rowcount = rowcount
            else:
                r.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=list(em_regeneracao))))
            return r

        db.execute = execute_side; db.commit = AsyncMock(); db.add = MagicMock(); db.rollback = AsyncMock()
        return db, statements

    @pytest.mark.asyncio
    async def test_bulk_approve_images_item_falho_com_mensagem_propria(self):
        """A cerca vive DENTRO do UPDATE (NOT EXISTS placeholder generating):
        com regeneracao em andamento o UPDATE aprova zero linhas, e a
        consulta seguinte e' o que separa este caso de "nenhuma imagem
        aprovavel"."""
        from app.services.listing_service import ListingService

        listing = _listing()
        db, statements = self._db_bulk(listing, rowcount=0, em_regeneracao=[3])
        with patch("app.workers.tasks.ai_tasks.generate_description") as gen:
            result = await ListingService(db, listing.seller_id).bulk_approve_images(
                [listing.id], user_id=uuid.uuid4())

        assert result.processed == 0 and result.failed == 1
        assert result.results[0].error == "Regeneração em andamento na posição 3; aguarde a conclusão antes de aprovar."
        assert listing.status == "pending_image_approval"
        db.commit.assert_not_awaited(); db.rollback.assert_awaited(); gen.delay.assert_not_called()
        assert len(statements) == 3
        update_sql = str(statements[1])
        assert update_sql.startswith("UPDATE listing_images"), update_sql
        assert "NOT (EXISTS" in update_sql, update_sql
        assert "listing_images_1.status" in update_sql, "subconsulta com alias, sem correlacionar com o UPDATE"
        assert "listing_images.kind" not in update_sql

    @pytest.mark.asyncio
    async def test_bulk_sem_nada_aprovavel_continua_com_a_mensagem_antiga(self):
        from app.services.listing_service import ListingService

        listing = _listing()
        db, statements = self._db_bulk(listing, rowcount=0, em_regeneracao=[])
        with patch("app.workers.tasks.ai_tasks.generate_description") as gen:
            result = await ListingService(db, listing.seller_id).bulk_approve_images(
                [listing.id], user_id=uuid.uuid4())
        assert result.failed == 1 and result.results[0].error == "nenhuma imagem aprovável"
        assert len(statements) == 3 and listing.status == "pending_image_approval"
        gen.delay.assert_not_called()

    @pytest.mark.asyncio
    async def test_bulk_sem_regeneracao_segue_normal_com_o_update_em_segundo(self):
        from app.services.listing_service import ListingService

        listing = _listing()
        db, statements = self._db_bulk(listing, rowcount=5, em_regeneracao=[])
        with patch("app.workers.tasks.ai_tasks.generate_description") as gen:
            result = await ListingService(db, listing.seller_id).bulk_approve_images(
                [listing.id], user_id=uuid.uuid4())
        assert result.processed == 1 and listing.status == "generating_description"
        gen.delay.assert_called_once()
        assert len(statements) == 2, "sem rowcount 0 nao ha consulta extra"
        update_sql = str(statements[1])
        assert "UPDATE listing_images" in update_sql
        assert "listing_images.sort_order <" in update_sql
        assert "listing_images.ml_picture_id IS NOT NULL" in update_sql
        assert "listing_images.kind" not in update_sql


class TestCercaDasPromocoes:
    """`promote_cover` rebaixa quem ocupa `sort_order` 0 para 90 — inclusive o
    placeholder `generating` da regeneracao da capa, que o worker depois
    encontraria fora do esquema de 5 posicoes. `promote_specs` aprova uma linha
    da posicao que ainda esta sendo refeita. Os dois recusam durante a
    regeneracao, com a MESMA mensagem das aprovacoes."""

    def _db_posicoes(self, posicoes):
        db = AsyncMock()
        r = MagicMock(); r.scalars.return_value.all.return_value = list(posicoes)
        db.execute = AsyncMock(return_value=r)
        return db

    @pytest.mark.asyncio
    async def test_helper_levanta_409_com_a_mensagem_das_aprovacoes(self):
        from app.services.listing_service import ListingService

        svc = ListingService(self._db_posicoes([4, 0]))
        with pytest.raises(HTTPException) as exc:
            await svc.recusar_se_regeneracao_em_andamento(_listing())
        assert exc.value.status_code == 409
        assert exc.value.detail == (
            "Regeneração em andamento na posição 0, 4; aguarde a conclusão antes de aprovar.")

    @pytest.mark.asyncio
    async def test_helper_nao_levanta_sem_placeholder(self):
        from app.services.listing_service import ListingService

        svc = ListingService(self._db_posicoes([]))
        assert await svc.recusar_se_regeneracao_em_andamento(_listing()) is None

    def _patches_endpoint(self, listing, cerca, modulo, nome, ordem):
        from app.api.v1.endpoints import listings as rota

        svc = MagicMock()
        svc.get_or_404 = AsyncMock(return_value=listing)
        svc.recusar_se_regeneracao_em_andamento = AsyncMock(side_effect=cerca)
        # Desde a correcao de `approved_image_count` pos-commit, o endpoint
        # serializa por `svc.summary_after_commit` (async), nao mais por
        # `ListingSummary.model_validate` solto.
        svc.summary_after_commit = AsyncMock()
        promocao = AsyncMock(side_effect=lambda *a, **k: ordem.append("promocao"))
        return svc, promocao, [
            patch.object(rota, "ListingService", return_value=svc),
            patch(f"{modulo}.{nome}", promocao),
            patch.object(rota.ListingSummary, "model_validate", MagicMock()),
        ]

    _ENDPOINTS = [
        ("promote_cover", "app.services.cover_variant_service", "promote_cover"),
        ("promote_specs", "app.services.specs_variant_service", "promote_specs"),
    ]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("endpoint,modulo,nome", _ENDPOINTS)
    async def test_a_cerca_roda_antes_da_promocao(self, endpoint, modulo, nome):
        from contextlib import ExitStack

        from app.api.v1.endpoints import listings as rota

        ordem = []
        svc, promocao, patches = self._patches_endpoint(
            _listing(), lambda l: ordem.append("cerca"), modulo, nome, ordem)
        with ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            await getattr(rota, endpoint)(
                listing_id=uuid.uuid4(), image_id=uuid.uuid4(),
                active_seller=MagicMock(), db=AsyncMock())

        assert ordem == ["cerca", "promocao"]
        svc.recusar_se_regeneracao_em_andamento.assert_awaited_once()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("endpoint,modulo,nome", _ENDPOINTS)
    async def test_cerca_que_recusa_impede_a_promocao(self, endpoint, modulo, nome):
        from contextlib import ExitStack

        from app.api.v1.endpoints import listings as rota

        ordem = []
        esperada = "Regeneração em andamento na posição 0; aguarde a conclusão antes de aprovar."

        def _recusar(_listing_arg):
            raise HTTPException(status_code=409, detail=esperada)

        _svc, promocao, patches = self._patches_endpoint(
            _listing(), _recusar, modulo, nome, ordem)
        with ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            with pytest.raises(HTTPException) as exc:
                await getattr(rota, endpoint)(
                    listing_id=uuid.uuid4(), image_id=uuid.uuid4(),
                    active_seller=MagicMock(), db=AsyncMock())

        assert exc.value.status_code == 409 and exc.value.detail == esperada
        promocao.assert_not_awaited()
        assert ordem == []


def test_mensagem_de_regeneracao_em_andamento():
    from app.services.listing_service import _mensagem_regeneracao_em_andamento
    assert _mensagem_regeneracao_em_andamento([2]) == (
        "Regeneração em andamento na posição 2; aguarde a conclusão antes de aprovar.")
    assert _mensagem_regeneracao_em_andamento([0, 4]).startswith("Regeneração em andamento na posição 0, 4;")
