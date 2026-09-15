"""feat/publicar-ativo: o item nasce ativo e o sistema reporta o estado real.

O passo 0 (leitura do anuncio 31) provou que o ML IGNORA o "status": "paused"
enviado na criacao e devolve o item em outro estado; o PUT que vinha em
seguida e' que o pausava. Esta branch para de desfazer o padrao do ML:
cria ativo, espera a validacao das fotos e reporta o que o ML decidiu.
"""
import logging

import httpx
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.publish_service import PublishService
from app.workers.tasks.publish_tasks import _status_apos_publicacao
from tests.test_publish_service import _listing, _image, _sem_teto_de_categoria


class TestStatusAposPublicacao:
    def test_active_vira_published(self):
        assert _status_apos_publicacao("active") == "published"

    def test_under_review_vira_published_under_review(self):
        assert _status_apos_publicacao("under_review") == "published_under_review"

    def test_paused_vira_published_paused(self):
        assert _status_apos_publicacao("paused") == "published_paused"

    def test_estado_desconhecido_cai_na_anomalia(self):
        for estado in ("", "inactive", "closed", "qualquer_coisa"):
            assert _status_apos_publicacao(estado) == "published_paused", estado

    def test_todos_os_retornos_existem_no_vocabulario(self):
        from app.models.listing import LISTING_STATUSES
        for estado in ("active", "under_review", "paused", ""):
            assert _status_apos_publicacao(estado) in LISTING_STATUSES


class TestPublishCriaAtivoESemPut:
    @pytest.mark.asyncio
    async def test_payload_de_criacao_pede_active(self):
        create_response = MagicMock()
        create_response.status_code = 201
        create_response.json.return_value = {"id": "MLB1", "status": "active", "sub_status": []}
        mock_post = AsyncMock(return_value=create_response)
        with patch("httpx.AsyncClient") as mock_client_cls, _sem_teto_de_categoria():
            client = mock_client_cls.return_value.__aenter__.return_value
            client.post = mock_post
            client.put = AsyncMock()
            await PublishService(db=MagicMock()).publish(
                listing=_listing(), attributes=[], images=[_image()],
                description_html=None, access_token="token",
            )
        body = mock_post.await_args_list[0].kwargs["json"]
        assert body["status"] == "active"

    @pytest.mark.asyncio
    async def test_ml_ativa_na_hora_devolve_active_sem_put(self):
        create_response = MagicMock()
        create_response.status_code = 201
        create_response.json.return_value = {"id": "MLB1", "status": "active", "sub_status": []}
        mock_put = AsyncMock()
        with patch("httpx.AsyncClient") as mock_client_cls, _sem_teto_de_categoria():
            client = mock_client_cls.return_value.__aenter__.return_value
            client.post = AsyncMock(return_value=create_response)
            client.put = mock_put
            item_id, estado = await PublishService(db=MagicMock()).publish(
                listing=_listing(), attributes=[], images=[_image()],
                description_html=None, access_token="token",
            )
        assert (item_id, estado) == ("MLB1", "active")
        mock_put.assert_not_called()

    @pytest.mark.asyncio
    async def test_ml_devolve_paused_e_ninguem_forca_nada(self):
        create_response = MagicMock()
        create_response.status_code = 201
        create_response.json.return_value = {"id": "MLB1", "status": "paused", "sub_status": []}
        mock_put = AsyncMock()
        with patch("httpx.AsyncClient") as mock_client_cls, _sem_teto_de_categoria():
            client = mock_client_cls.return_value.__aenter__.return_value
            client.post = AsyncMock(return_value=create_response)
            client.put = mock_put
            _, estado = await PublishService(db=MagicMock()).publish(
                listing=_listing(), attributes=[], images=[_image()],
                description_html=None, access_token="token",
            )
        assert estado == "paused"
        mock_put.assert_not_called()

    @pytest.mark.asyncio
    async def test_ml_devolve_under_review_e_o_estado_e_reportado(self):
        create_response = MagicMock()
        create_response.status_code = 201
        create_response.json.return_value = {"id": "MLB1", "status": "under_review", "sub_status": []}
        mock_put = AsyncMock()
        with patch("httpx.AsyncClient") as mock_client_cls, _sem_teto_de_categoria():
            client = mock_client_cls.return_value.__aenter__.return_value
            client.post = AsyncMock(return_value=create_response)
            client.put = mock_put
            _, estado = await PublishService(db=MagicMock()).publish(
                listing=_listing(), attributes=[], images=[_image()],
                description_html=None, access_token="token",
            )
        assert estado == "under_review"
        mock_put.assert_not_called()

    @pytest.mark.asyncio
    async def test_espera_picture_download_pending_e_reporta_o_estado_final(self):
        create_response = MagicMock()
        create_response.status_code = 201
        create_response.json.return_value = {
            "id": "MLB1", "status": "paused", "sub_status": ["picture_download_pending"],
        }
        get_response = MagicMock()
        get_response.status_code = 200
        get_response.json.return_value = {"id": "MLB1", "status": "active", "sub_status": []}
        mock_put = AsyncMock()
        with patch("httpx.AsyncClient") as mock_client_cls, \
             patch("asyncio.sleep", new_callable=AsyncMock), \
             _sem_teto_de_categoria():
            client = mock_client_cls.return_value.__aenter__.return_value
            client.post = AsyncMock(return_value=create_response)
            client.get = AsyncMock(return_value=get_response)
            client.put = mock_put
            _, estado = await PublishService(db=MagicMock()).publish(
                listing=_listing(), attributes=[], images=[_image()],
                description_html=None, access_token="token",
            )
        assert estado == "active"
        mock_put.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_de_checagem_falhando_reporta_o_ultimo_estado_visto(self):
        create_response = MagicMock()
        create_response.status_code = 201
        create_response.json.return_value = {
            "id": "MLB1", "status": "paused", "sub_status": ["picture_download_pending"],
        }
        get_response = MagicMock()
        get_response.status_code = 500
        get_response.text = "boom"
        mock_put = AsyncMock()
        with patch("httpx.AsyncClient") as mock_client_cls, \
             patch("asyncio.sleep", new_callable=AsyncMock), \
             _sem_teto_de_categoria():
            client = mock_client_cls.return_value.__aenter__.return_value
            client.post = AsyncMock(return_value=create_response)
            client.get = AsyncMock(return_value=get_response)
            client.put = mock_put
            _, estado = await PublishService(db=MagicMock()).publish(
                listing=_listing(), attributes=[], images=[_image()],
                description_html=None, access_token="token",
            )
        assert estado == "paused"
        mock_put.assert_not_called()


class TestPublishSobreviveAFalhaPosCriacao:
    """F1: a partir do POST bem-sucedido o item JA esta no ar. Falha nas
    etapas seguintes (checagem de estado, descricao) NAO PODE subir — subiria
    ao retry do Celery, que criaria um SEGUNDO item vivo (`publish()` roda de
    novo do zero). `publish()` engole a excecao, loga, e devolve o estado da
    criacao como "foto do momento"."""

    @pytest.mark.asyncio
    async def test_erro_de_rede_no_polling_nao_sobe_e_devolve_estado_da_criacao(self):
        create_response = MagicMock()
        create_response.status_code = 201
        create_response.json.return_value = {
            "id": "MLB1", "status": "paused", "sub_status": ["picture_download_pending"],
        }
        mock_put = AsyncMock()
        with patch("httpx.AsyncClient") as mock_client_cls, \
             patch("asyncio.sleep", new_callable=AsyncMock), \
             _sem_teto_de_categoria():
            client = mock_client_cls.return_value.__aenter__.return_value
            client.post = AsyncMock(return_value=create_response)
            client.get = AsyncMock(side_effect=httpx.ConnectError("boom"))
            client.put = mock_put
            item_id, estado = await PublishService(db=MagicMock()).publish(
                listing=_listing(), attributes=[], images=[_image()],
                description_html=None, access_token="token",
            )
        assert (item_id, estado) == ("MLB1", "paused")
        mock_put.assert_not_called()

    @pytest.mark.asyncio
    async def test_erro_ao_postar_descricao_nao_sobe_e_ainda_devolve_o_item(self):
        create_response = MagicMock()
        create_response.status_code = 201
        create_response.json.return_value = {"id": "MLB1", "status": "active", "sub_status": []}
        mock_put = AsyncMock()
        with patch("httpx.AsyncClient") as mock_client_cls, _sem_teto_de_categoria(), \
             patch.object(
                 PublishService, "_post_description",
                 new_callable=AsyncMock, side_effect=httpx.ConnectError("boom"),
             ):
            client = mock_client_cls.return_value.__aenter__.return_value
            client.post = AsyncMock(return_value=create_response)
            client.put = mock_put
            item_id, estado = await PublishService(db=MagicMock()).publish(
                listing=_listing(), attributes=[], images=[_image()],
                description_html="<p>oi</p>", access_token="token",
            )
        assert (item_id, estado) == ("MLB1", "active")
        mock_put.assert_not_called()


class TestInvariantes:
    def test_ensure_paused_nao_existe_mais(self):
        """Guarda contra reintroducao E contra patch antigo passando em silencio."""
        assert not hasattr(PublishService, "_ensure_paused")

    def test_activate_continua_sendo_o_unico_caminho_de_ativacao_explicita(self):
        """activate_listing segue existindo e segue mandando PUT active —
        o caminho de volta do pausado (botao 'Reativar' na tela)."""
        assert hasattr(PublishService, "activate_listing")

    @pytest.mark.asyncio
    async def test_anuncio_ja_pausado_nao_e_tocado_pelo_worker(self):
        """O guard de idempotencia do worker: listing fora de 'publishing'
        (ex.: published_paused, os anuncios 31/37/38) e' pulado sem nenhuma
        chamada ao ML."""
        from app.workers.tasks import publish_tasks as pt

        listing = MagicMock()
        listing.status = "published_paused"

        execute_result = MagicMock()
        execute_result.scalar_one.return_value = listing

        db = MagicMock()
        db.execute = AsyncMock(return_value=execute_result)
        db.commit = AsyncMock()

        session_ctx = MagicMock()
        session_ctx.__aenter__ = AsyncMock(return_value=db)
        session_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("app.database.worker_session", return_value=session_ctx), \
             patch.object(PublishService, "publish", new_callable=AsyncMock) as mock_publish:
            result = await pt._publish_listing_async("lst-1")

        assert result == {"listing_id": "lst-1", "skipped": True}
        mock_publish.assert_not_called()
        assert listing.status == "published_paused"


class TestWorkerGravaOEstadoTraduzido:
    def _roda_worker(self, estado_ml):
        from app.workers.tasks import publish_tasks as pt

        listing = MagicMock()
        listing.status = "publishing"
        listing.id = "lst-1"

        one = MagicMock(); one.scalar_one.return_value = listing
        seller = MagicMock(); one_seller = MagicMock(); one_seller.scalar_one.return_value = seller
        vazio = MagicMock(); vazio.scalars.return_value.all.return_value = []
        nenhum = MagicMock(); nenhum.scalar_one_or_none.return_value = None

        db = MagicMock()
        db.execute = AsyncMock(side_effect=[one, one_seller, vazio, vazio, nenhum])
        db.commit = AsyncMock()

        session_ctx = MagicMock()
        session_ctx.__aenter__ = AsyncMock(return_value=db)
        session_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("app.database.worker_session", return_value=session_ctx), \
             patch("app.services.publish_service.get_valid_access_token", new_callable=AsyncMock, return_value="tok"), \
             patch.object(PublishService, "publish", new_callable=AsyncMock, return_value=("MLB77", estado_ml)):
            import asyncio as _a
            _a.run(pt._publish_listing_async("lst-1"))
        return listing

    def test_active_grava_published(self):
        assert self._roda_worker("active").status == "published"

    def test_under_review_grava_published_under_review(self):
        assert self._roda_worker("under_review").status == "published_under_review"

    def test_paused_grava_published_paused(self):
        assert self._roda_worker("paused").status == "published_paused"

    def test_paused_loga_o_warning_de_anomalia(self, caplog):
        # F2: published_paused e' o balde de anomalia — tem que deixar rastro.
        with caplog.at_level(logging.WARNING, logger="app.workers.tasks.publish_tasks"):
            self._roda_worker("paused")
        avisos = [r for r in caplog.records if r.getMessage().startswith("publish_estado_nao_ativo")]
        assert len(avisos) == 1
        assert "estado_ml=paused" in avisos[0].getMessage()
        assert "status_local=published_paused" in avisos[0].getMessage()

    def test_active_nao_loga_warning_nenhum(self, caplog):
        # Caminho feliz nao pode gerar ruido de anomalia no log.
        with caplog.at_level(logging.WARNING, logger="app.workers.tasks.publish_tasks"):
            self._roda_worker("active")
        avisos = [r for r in caplog.records if r.getMessage().startswith("publish_estado_nao_ativo")]
        assert avisos == []
