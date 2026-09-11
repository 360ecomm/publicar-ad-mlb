"""Standby por motor de IA indisponivel (credito OpenAI esgotado, 401/403/5xx,
timeout): `pending_ai_engine`.

O que acontecia (investigado em 2026-09-10): `_tentar` engolia QUALQUER
excecao por posicao, inclusive `ImageEngineUnavailableError`. Com credito
zerado, as 5 posicoes falhavam em silencio (10 chamadas pagas-que-falham por
tentativa), `salvas=0` virava RuntimeError generico, o Celery retentava 2x
(30 chamadas ao todo) e o listing acabava em `failed` com "Nenhuma imagem
valida foi gerada" — mensagem que nao diz que o problema e' o motor. Pior:
se o credito acabasse NO MEIO, o anuncio seguia para revisao com 2 ou 3
posicoes e nenhum erro — galeria parcial silenciosa.

Agora: `_tentar` continua tolerando falha generica por posicao, mas quando a
ultima tentativa falha com `ImageEngineUnavailableError` a excecao SOBE e
aborta a geracao inteira; o worker descarta as posicoes parciais desta
tentativa (rollback), poe o listing em `pending_ai_engine` com a mensagem
real do motor, e nada e' enfileirado depois do standby. Retomada: beat a cada 15
min (credito pode voltar a qualquer momento, sem aviso) ou endpoint manual —
sem pre-checagem, porque a API da OpenAI nao expoe saldo: tentar E' a
checagem.
"""
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.image_engines.base import ImageEngineUnavailableError


@asynccontextmanager
async def _sessao(db):
    yield db


class TestTentarAbortaNoMotorIndisponivel:
    @pytest.mark.asyncio
    async def test_motor_indisponivel_sobe_depois_das_tentativas(self):
        from app.workers.tasks.image_tasks import _tentar

        chamadas = []

        async def fabrica():
            chamadas.append(1)
            raise ImageEngineUnavailableError("OpenAI Edits API 429: insufficient_quota")

        with pytest.raises(ImageEngineUnavailableError):
            await _tentar("1-capa", "lid", fabrica)
        assert len(chamadas) == 2, "ainda tenta 2x: 5xx/timeout podem ser transientes"

    @pytest.mark.asyncio
    async def test_falha_generica_continua_devolvendo_none(self):
        from app.workers.tasks.image_tasks import _tentar

        async def fabrica():
            raise RuntimeError("imagem corrompida")

        assert await _tentar("2-apresentacao", "lid", fabrica) is None

    @pytest.mark.asyncio
    async def test_transiente_na_primeira_e_sucesso_na_segunda(self):
        from app.workers.tasks.image_tasks import _tentar

        vez = [0]

        async def fabrica():
            vez[0] += 1
            if vez[0] == 1:
                raise ImageEngineUnavailableError("OpenAI Edits API 503")
            return b"ok"

        assert await _tentar("1-capa", "lid", fabrica) == b"ok"


class TestWorkerEntraEmStandby:
    def _db(self, listing):
        db = AsyncMock()
        db.execute = AsyncMock(return_value=MagicMock(scalar_one=MagicMock(return_value=listing)))
        db.add = MagicMock(); db.commit = AsyncMock(); db.rollback = AsyncMock()
        return db

    @pytest.mark.asyncio
    async def test_motor_indisponivel_vira_pending_ai_engine(self):
        from app.workers.tasks.image_tasks import _generate_images_async

        listing = MagicMock(); listing.id = "lid"; listing.status = "generating_images"
        listing.sku_external_id = "45"; listing.seller_id = "sid"; listing.created_via = "batch"
        db = self._db(listing)

        with patch("app.database.worker_session", lambda: _sessao(db)), \
             patch("app.workers.tasks.image_tasks._fetch_upload_token", new_callable=AsyncMock, return_value="tok"), \
             patch("app.workers.tasks.image_tasks._try_i2i_generation", new_callable=AsyncMock,
                   side_effect=ImageEngineUnavailableError("OpenAI Edits API 429: insufficient_quota")):
            result = await _generate_images_async("lid")

        assert listing.status == "pending_ai_engine"
        assert "insufficient_quota" in listing.error_message
        assert result == {"listing_id": "lid", "pending_ai_engine": True}
        db.rollback.assert_awaited_once(), "posicoes parciais desta tentativa sao descartadas"

    @pytest.mark.asyncio
    async def test_nao_e_failed_nem_publica(self):
        from app.workers.tasks.image_tasks import _generate_images_async

        listing = MagicMock(); listing.id = "lid"; listing.status = "generating_images"
        listing.sku_external_id = "45"; listing.seller_id = "sid"; listing.created_via = "batch"
        db = self._db(listing)

        with patch("app.database.worker_session", lambda: _sessao(db)), \
             patch("app.workers.tasks.image_tasks._fetch_upload_token", new_callable=AsyncMock, return_value="tok"), \
             patch("app.workers.tasks.image_tasks._try_i2i_generation", new_callable=AsyncMock,
                   side_effect=ImageEngineUnavailableError("Timeout ao chamar a OpenAI (edits)")):
            await _generate_images_async("lid")

        assert listing.status not in ("failed", "generating_description", "publishing")


def _listing(status="pending_ai_engine", created_via="batch"):
    listing = MagicMock(); listing.id = "lid"; listing.status = status
    listing.sku_external_id = "45"; listing.seller_id = "sid"; listing.created_via = created_via
    listing.error_message = "x"
    return listing


class TestRetomada:
    @pytest.mark.asyncio
    async def test_retoma_e_despacha_pelo_mesmo_ponto(self):
        from app.services.ai_engine_standby_service import try_resume_ai_engine

        listing = _listing()
        db = AsyncMock(); db.execute = AsyncMock(return_value=MagicMock(rowcount=1)); db.commit = AsyncMock()
        with patch("app.services.ai_engine_standby_service.dispatch_image_generation") as dispatch:
            assert await try_resume_ai_engine(db, listing) is True
        dispatch.assert_called_once_with(listing)
        assert listing.status == "generating_images" and listing.error_message is None

    @pytest.mark.asyncio
    async def test_perdeu_a_corrida_nao_despacha(self):
        from app.services.ai_engine_standby_service import try_resume_ai_engine

        db = AsyncMock(); db.execute = AsyncMock(return_value=MagicMock(rowcount=0)); db.commit = AsyncMock()
        with patch("app.services.ai_engine_standby_service.dispatch_image_generation") as dispatch:
            assert await try_resume_ai_engine(db, _listing()) is False
        dispatch.assert_not_called()


class TestTarefaPeriodica:
    @pytest.mark.asyncio
    async def test_retoma_todos_os_pendentes(self):
        from app.workers.tasks.ai_engine_tasks import _check_pending_ai_engine_async

        a, b = _listing(), _listing(); a.id, b.id = "a", "b"
        db = AsyncMock()
        db.execute = AsyncMock(return_value=MagicMock(
            scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[a, b])))))
        with patch("app.database.worker_session", lambda: _sessao(db)), \
             patch("app.services.ai_engine_standby_service.try_resume_ai_engine",
                   new_callable=AsyncMock, return_value=True) as resume:
            result = await _check_pending_ai_engine_async()
        assert result == {"checked": 2, "resumed": 2}
        assert resume.await_count == 2

    def test_beat_e_rota(self):
        from app.workers.celery_app import celery_app
        import app.workers.tasks.ai_engine_tasks  # noqa: F401

        entrada = celery_app.conf.beat_schedule["check-pending-ai-engine"]
        assert entrada["task"] == "app.workers.tasks.ai_engine_tasks.check_pending_ai_engine"
        assert entrada["schedule"] == 15 * 60
        assert "app.workers.tasks.ai_engine_tasks.check_pending_ai_engine" in celery_app.tasks
        assert celery_app.conf.task_routes["app.workers.tasks.ai_engine_tasks.*"] == {"queue": "default"}


class TestRetomadaManual:
    @pytest.mark.asyncio
    async def test_status_errado_da_409(self):
        from fastapi import HTTPException
        from app.services.listing_service import ListingService

        with pytest.raises(HTTPException) as exc:
            await ListingService(AsyncMock()).resume_ai_engine(_listing(status="pending_image_approval"))
        assert exc.value.status_code == 409

    @pytest.mark.asyncio
    async def test_retoma_sob_demanda(self):
        from app.services.listing_service import ListingService

        with patch("app.services.ai_engine_standby_service.try_resume_ai_engine",
                   new_callable=AsyncMock, return_value=True) as resume:
            await ListingService(AsyncMock()).resume_ai_engine(_listing())
        resume.assert_awaited_once()
