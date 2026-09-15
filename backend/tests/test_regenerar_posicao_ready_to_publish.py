"""Regenerar UMA posicao a partir de `ready_to_publish`. Sem banco.

Em `ready_to_publish` as 5 posicoes estao aprovadas. Regenerar ali DESAPROVA
a posicao pedida e devolve o anuncio a `pending_image_approval` — sem isso o
endpoint devolveria 409 em 100% dos casos, e o worker
(`image_tasks.py:708`) apagaria o placeholder sozinho ao ver o anuncio fora
de `pending_image_approval`: um no-op silencioso, sem erro visivel."""
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError


def _listing(status):
    listing = MagicMock()
    listing.id = uuid.uuid4()
    listing.status = status
    listing.sku_external_id = "91"
    return listing


def _linha(approved, status="approved"):
    img = MagicMock()
    img.approved = approved
    img.status = status
    return img


def _db(ocupantes):
    db = AsyncMock()
    r = MagicMock()
    r.scalars.return_value.all.return_value = list(ocupantes)
    db.execute = AsyncMock(return_value=r)
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    db.refresh = AsyncMock()
    db.delete = AsyncMock()
    return db


class TestReadyToPublish:
    @pytest.mark.asyncio
    async def test_desaprova_a_posicao_e_volta_para_revisao(self):
        from app.services.listing_service import ListingService

        aprovada = _linha(approved=True)
        listing = _listing("ready_to_publish")
        db = _db([aprovada])
        with patch("app.workers.tasks.image_tasks.regenerate_position") as task:
            await ListingService(db).regenerate_position(listing, 4)
        assert aprovada.approved is False
        assert aprovada.status == "uploaded"
        assert listing.status == "pending_image_approval"
        task.delay.assert_called_once()
        # protege contra um commit intermediario que persista a desaprovacao
        # sem o placeholder (deixaria o anuncio desaprovado sem nada rodando)
        db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_volta_para_revisao_mesmo_com_a_posicao_ja_nao_aprovada(self):
        """Anuncio que chegou a ready_to_publish com 4 de 5 aprovadas: sem
        esta regra o status ficaria em ready_to_publish e o worker
        (`image_tasks.py:708`) apagaria o placeholder sozinho — a
        regeneracao nunca aconteceria, sem ninguem perceber."""
        from app.services.listing_service import ListingService

        nao_aprovada = _linha(approved=False, status="validation_failed")
        listing = _listing("ready_to_publish")
        db = _db([nao_aprovada])
        with patch("app.workers.tasks.image_tasks.regenerate_position") as task:
            await ListingService(db).regenerate_position(listing, 3)
        assert listing.status == "pending_image_approval"
        task.delay.assert_called_once()
        # a linha nao aprovada nao pode ser tocada: apagaria a evidencia do
        # QA (validation_failed) sem nenhum teste perceber
        assert nao_aprovada.status == "validation_failed"

    @pytest.mark.asyncio
    async def test_cria_o_placeholder_e_enfileira(self):
        from app.models.listing_image import GENERATING_STATUS
        from app.services.listing_service import ListingService

        db = _db([_linha(approved=True)])
        with patch("app.workers.tasks.image_tasks.regenerate_position") as task:
            placeholder = await ListingService(db).regenerate_position(
                _listing("ready_to_publish"), 0
            )
        assert placeholder.status == GENERATING_STATUS
        assert placeholder.approved is False
        assert placeholder.sort_order == 0
        task.delay.assert_called_once()


class TestBrokerForaCompensaReadyToPublish:
    """O 503 abaixo afirma "o pedido nao foi registrado". Vindo de
    ready_to_publish isso so e' verdade se a desaprovacao e a volta de
    status tambem forem desfeitas — senao o operador leria "nada aconteceu"
    vendo o anuncio ter andado para tras de verdade."""

    @pytest.mark.asyncio
    async def test_broker_fora_restaura_aprovacao_e_status_original(self):
        from app.services.listing_service import ListingService

        aprovada = _linha(approved=True)
        listing = _listing("ready_to_publish")
        db = _db([aprovada])
        with patch("app.workers.tasks.image_tasks.regenerate_position") as task:
            task.delay = MagicMock(side_effect=Exception("redis down"))
            with pytest.raises(HTTPException) as exc:
                await ListingService(db).regenerate_position(listing, 4)
        assert exc.value.status_code == 503
        db.delete.assert_awaited_once()
        assert listing.status == "ready_to_publish"
        assert aprovada.approved is True
        assert aprovada.status == "approved"

    @pytest.mark.asyncio
    async def test_compensacao_que_estoura_integrity_error_vira_503_proprio_sem_500(self):
        from app.services.listing_service import ListingService

        aprovada = _linha(approved=True)
        listing = _listing("ready_to_publish")
        db = _db([aprovada])
        chamadas = []

        async def commit_side_effect():
            chamadas.append(1)
            if len(chamadas) == 2:  # 1a = placeholder OK; 2a = compensacao falha
                raise IntegrityError("UPDATE", {}, Exception("uq_listing_images_cover_slot"))

        db.commit = AsyncMock(side_effect=commit_side_effect)
        with patch("app.workers.tasks.image_tasks.regenerate_position") as task:
            task.delay = MagicMock(side_effect=Exception("redis down"))
            with pytest.raises(HTTPException) as exc:
                await ListingService(db).regenerate_position(listing, 4)
        assert exc.value.status_code == 503
        assert "não foi possível desfazer" in exc.value.detail
        db.rollback.assert_awaited_once()


class TestPendingImageApprovalNaoMuda:
    @pytest.mark.asyncio
    async def test_posicao_aprovada_continua_recusada_com_409(self):
        from app.services.listing_service import ListingService

        aprovada = _linha(approved=True)
        listing = _listing("pending_image_approval")
        db = _db([aprovada])
        with patch("app.workers.tasks.image_tasks.regenerate_position") as task:
            with pytest.raises(HTTPException) as exc:
                await ListingService(db).regenerate_position(listing, 4)
        assert exc.value.status_code == 409
        assert "já está aprovada" in exc.value.detail
        assert aprovada.approved is True
        assert listing.status == "pending_image_approval"
        db.add.assert_not_called()
        task.delay.assert_not_called()

    @pytest.mark.asyncio
    async def test_status_intocado_no_caminho_normal(self):
        from app.services.listing_service import ListingService

        listing = _listing("pending_image_approval")
        db = _db([_linha(approved=False, status="uploaded")])
        with patch("app.workers.tasks.image_tasks.regenerate_position"):
            await ListingService(db).regenerate_position(listing, 2)
        assert listing.status == "pending_image_approval"


class TestStatusRecusados:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("st", ["draft", "pending_description", "publishing", "published"])
    async def test_recusa_com_409_antes_de_consultar(self, st):
        from app.services.listing_service import ListingService

        db = _db([])
        with pytest.raises(HTTPException) as exc:
            await ListingService(db).regenerate_position(_listing(st), 2)
        assert exc.value.status_code == 409
        db.execute.assert_not_awaited()
