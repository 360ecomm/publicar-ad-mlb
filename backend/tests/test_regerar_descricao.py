"""Regerar a descricao sem passar pela aprovacao de imagens. Sem banco.

Existe por causa de uma corrupcao de auditoria: ate aqui a unica saida era
reaprovar as imagens, e cada reaprovacao grava um `listing_review_events`
afirmando revisao humana que nao aconteceu."""
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException


def _listing(status="ready_to_publish"):
    listing = MagicMock()
    listing.id = uuid.uuid4()
    listing.status = status
    listing.sku_external_id = "91"
    return listing


def _db(rowcount=1):
    db = AsyncMock()
    r = MagicMock()
    r.rowcount = rowcount
    db.execute = AsyncMock(return_value=r)
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    return db


class TestGuardas:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("st", [
        "draft", "pending_image_approval", "generating_description",
        "publishing", "published", "failed",
    ])
    async def test_recusa_fora_de_ready_to_publish_antes_de_consultar(self, st):
        from app.services.listing_service import ListingService

        db = _db()
        with pytest.raises(HTTPException) as exc:
            await ListingService(db).regenerate_description(_listing(st))
        assert exc.value.status_code == 409
        assert st in exc.value.detail
        db.execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_corrida_perdida_devolve_409(self):
        from app.services.listing_service import ListingService

        db = _db(rowcount=0)
        with patch("app.workers.tasks.ai_tasks.generate_description") as task:
            with pytest.raises(HTTPException) as exc:
                await ListingService(db).regenerate_description(_listing())
        assert exc.value.status_code == 409
        task.delay.assert_not_called()


class TestCaminhoNormal:
    @pytest.mark.asyncio
    async def test_muda_para_generating_description_e_enfileira(self):
        from app.services.listing_service import ListingService

        listing = _listing()
        db = _db()
        with patch("app.workers.tasks.ai_tasks.generate_description") as task:
            await ListingService(db).regenerate_description(listing)
        assert listing.status == "generating_description"
        task.delay.assert_called_once_with(str(listing.id))

    @pytest.mark.asyncio
    async def test_commit_antes_de_enfileirar(self):
        """Mesma ordem de `regenerate_position`: o worker le do banco."""
        from app.services.listing_service import ListingService

        ordem = []
        db = _db()
        db.commit = AsyncMock(side_effect=lambda: ordem.append("commit"))
        with patch("app.workers.tasks.ai_tasks.generate_description") as task:
            task.delay = MagicMock(side_effect=lambda *_a: ordem.append("delay"))
            await ListingService(db).regenerate_description(_listing())
        assert ordem.index("commit") < ordem.index("delay")

    @pytest.mark.asyncio
    async def test_nao_grava_evento_de_revisao(self):
        """O ponto todo desta tarefa: regerar descricao NAO e' revisao humana
        de imagem, e nao pode gravar um evento dizendo que foi."""
        from app.services.listing_service import ListingService

        db = _db()
        with patch("app.workers.tasks.ai_tasks.generate_description"):
            await ListingService(db).regenerate_description(_listing())
        db.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_nao_dispara_imagem_nem_publicacao(self):
        from app.services.listing_service import ListingService

        db = _db()
        with patch("app.workers.tasks.ai_tasks.generate_description"), \
             patch("app.workers.tasks.image_tasks.generate_images") as gi, \
             patch("app.workers.tasks.publish_tasks.publish_listing") as pl:
            await ListingService(db).regenerate_description(_listing())
        gi.delay.assert_not_called()
        pl.delay.assert_not_called()


class TestBrokerFora:
    @pytest.mark.asyncio
    async def test_devolve_o_status_e_responde_503(self):
        from app.services.listing_service import ListingService

        listing = _listing()
        db = _db()
        with patch("app.workers.tasks.ai_tasks.generate_description") as task:
            task.delay = MagicMock(side_effect=OSError("redis fora"))
            with pytest.raises(HTTPException) as exc:
                await ListingService(db).regenerate_description(listing)
        assert exc.value.status_code == 503
        assert listing.status == "ready_to_publish"


class TestRota:
    def test_rota_existe_e_devolve_listing_summary(self):
        from app.api.v1.endpoints import listings
        from app.schemas.listing import ListingSummary

        alvo = [
            r for r in listings.router.routes
            if getattr(r, "path", None) == "/listings/{listing_id}/pipeline/regenerate_description"
        ]
        assert len(alvo) == 1
        assert "POST" in alvo[0].methods
        assert alvo[0].response_model is ListingSummary
