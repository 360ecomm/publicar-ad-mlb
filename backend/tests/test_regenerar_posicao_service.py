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


def test_rota_declarada_com_202_e_image_out():
    from app.main import app

    rotas = {
        (route.path, tuple(sorted(route.methods))): route
        for route in app.routes if getattr(route, "methods", None)
    }
    rota = rotas[("/api/v1/listings/{listing_id}/images/positions/{posicao}/regenerate", ("POST",))]
    assert rota.status_code == 202
    assert rota.response_model.__name__ == "ImageOut"
