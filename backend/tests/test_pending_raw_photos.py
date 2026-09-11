"""Standby por falta de foto bruta: `pending_raw_photos`.

Antes, quando faltava `{sku}-1.jpg` ou `-2.jpg` no bucket do seller,
`_generate_images_async` caia no texto-imagem antigo (prompt do LLM + motor
de geracao do zero), que em lote auto-aprovava e PUBLICAVA. Decisao de
2026-09-10: o caminho texto-imagem foi removido; o listing entra em
`pending_raw_photos` e espera as fotos chegarem.

Retomada: automatica (beat, a cada 15 min) ou manual (endpoint). As duas
usam o mesmo `try_resume_raw_photos`, que reusa a sondagem do bucket que ja
existe (`fetch_raw_photos`) e reentra no pipeline pelo mesmo ponto de sempre
(`generate_images.delay`, igual para lote e manual — publicar continua sendo
sempre acao humana).
"""
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@asynccontextmanager
async def _sessao(db):
    yield db


def _listing(created_via="batch", status="pending_raw_photos"):
    listing = MagicMock()
    listing.id = "lid"
    listing.status = status
    listing.sku_external_id = "T99"
    listing.seller_id = "sid"
    listing.created_via = created_via
    listing.ml_category_id = "MLB6284"
    listing.error_message = None
    return listing


class TestWorkerEntraEmStandby:
    @pytest.mark.asyncio
    async def test_sem_foto_bruta_vai_para_pending_raw_photos(self):
        from app.workers.tasks.image_tasks import _generate_images_async

        listing = _listing(status="generating_images")
        db = AsyncMock()
        db.execute = AsyncMock(return_value=MagicMock(scalar_one=MagicMock(return_value=listing)))
        db.add = MagicMock()
        db.commit = AsyncMock()

        with patch("app.database.worker_session", lambda: _sessao(db)), \
             patch("app.workers.tasks.image_tasks._fetch_upload_token", new_callable=AsyncMock, return_value="tok"), \
             patch("app.workers.tasks.image_tasks._try_i2i_generation", new_callable=AsyncMock, return_value=None):
            result = await _generate_images_async("lid")

        assert listing.status == "pending_raw_photos"
        assert "T99-1 e T99-2" in listing.error_message
        assert "em .jpg, .png ou .webp" in listing.error_message
        assert result == {"listing_id": "lid", "pending_raw_photos": True}
        assert db.add.call_args_list == [], "nada gerado, nada gravado"

    @pytest.mark.asyncio
    async def test_lote_sem_foto_nao_publica(self):
        """O status novo nao e' `generating_description` nem `publishing`:
        o listing nao avanca sozinho para as proximas etapas."""
        from app.workers.tasks.image_tasks import _generate_images_async

        listing = _listing(created_via="batch", status="generating_images")
        db = AsyncMock()
        db.execute = AsyncMock(return_value=MagicMock(scalar_one=MagicMock(return_value=listing)))
        db.add = MagicMock()
        db.commit = AsyncMock()

        with patch("app.database.worker_session", lambda: _sessao(db)), \
             patch("app.workers.tasks.image_tasks._fetch_upload_token", new_callable=AsyncMock, return_value="tok"), \
             patch("app.workers.tasks.image_tasks._try_i2i_generation", new_callable=AsyncMock, return_value=None):
            await _generate_images_async("lid")

        assert listing.status not in ("generating_description", "publishing", "published_paused")


def _db_com_config(base_url="https://bucket.r2.dev", rowcount=1):
    config = MagicMock()
    config.raw_base_url = base_url
    db = AsyncMock()
    r = MagicMock()
    r.scalar_one_or_none = MagicMock(return_value=config)
    r.rowcount = rowcount
    db.execute = AsyncMock(return_value=r)
    db.commit = AsyncMock()
    return db


class TestRetomada:
    @pytest.mark.asyncio
    async def test_fotos_disponiveis_retoma_lote_pela_chain(self):
        from app.services.raw_photo_standby_service import try_resume_raw_photos

        listing = _listing(created_via="batch")
        db = _db_com_config()
        with patch("app.services.raw_photo_standby_service.fetch_raw_photos",
                   new_callable=AsyncMock, return_value=[b"1", b"2"]) as sonda, \
             patch("app.services.raw_photo_standby_service.dispatch_image_generation") as dispatch:
            retomou = await try_resume_raw_photos(db, listing)

        assert retomou is True
        sonda.assert_awaited_once_with("https://bucket.r2.dev", "T99")
        dispatch.assert_called_once_with(listing)

    @pytest.mark.asyncio
    async def test_fotos_ausentes_continua_esperando(self):
        from app.services.raw_photo_standby_service import try_resume_raw_photos

        listing = _listing()
        db = _db_com_config()
        with patch("app.services.raw_photo_standby_service.fetch_raw_photos",
                   new_callable=AsyncMock, return_value=None), \
             patch("app.services.raw_photo_standby_service.dispatch_image_generation") as dispatch:
            retomou = await try_resume_raw_photos(db, listing)

        assert retomou is False
        dispatch.assert_not_called()
        assert listing.status == "pending_raw_photos"

    @pytest.mark.asyncio
    async def test_sem_seller_image_config_continua_esperando(self):
        from app.services.raw_photo_standby_service import try_resume_raw_photos

        listing = _listing()
        db = AsyncMock()
        db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))
        with patch("app.services.raw_photo_standby_service.fetch_raw_photos", new_callable=AsyncMock) as sonda, \
             patch("app.services.raw_photo_standby_service.dispatch_image_generation") as dispatch:
            retomou = await try_resume_raw_photos(db, listing)

        assert retomou is False
        sonda.assert_not_awaited()
        dispatch.assert_not_called()

    @pytest.mark.asyncio
    async def test_update_atomico_perdeu_a_corrida_nao_despacha(self):
        """Beat e endpoint manual podem colidir: so quem mudou a linha despacha."""
        from app.services.raw_photo_standby_service import try_resume_raw_photos

        listing = _listing()
        db = _db_com_config(rowcount=0)
        with patch("app.services.raw_photo_standby_service.fetch_raw_photos",
                   new_callable=AsyncMock, return_value=[b"1", b"2"]), \
             patch("app.services.raw_photo_standby_service.dispatch_image_generation") as dispatch:
            retomou = await try_resume_raw_photos(db, listing)

        assert retomou is False
        dispatch.assert_not_called()


class TestDispatchReentraNoMesmoPonto:
    def test_lote_usa_delay_avulso_igual_ao_manual(self):
        """Lote e manual despacham generate_images.delay direto — chain de 1
        elemento era ruído, e generate_description/publish_listing nunca
        rodavam sozinhos por este caminho (publicar é sempre ação humana:
        trigger_publish / bulk_publish)."""
        from app.services.raw_photo_standby_service import dispatch_image_generation

        listing = _listing(created_via="batch")
        with patch("app.workers.tasks.image_tasks.generate_images") as gi, \
             patch("app.workers.tasks.ai_tasks.generate_description") as gd, \
             patch("app.workers.tasks.publish_tasks.publish_listing") as pl:
            dispatch_image_generation(listing)

        gi.delay.assert_called_once_with("lid")
        gd.si.assert_not_called()
        gd.delay.assert_not_called()
        pl.si.assert_not_called()
        pl.delay.assert_not_called()

    def test_manual_usa_delay_avulso(self):
        from app.services.raw_photo_standby_service import dispatch_image_generation

        listing = _listing(created_via="manual")
        with patch("app.workers.tasks.image_tasks.generate_images") as gi:
            dispatch_image_generation(listing)

        gi.delay.assert_called_once_with("lid")


class TestTarefaPeriodica:
    @pytest.mark.asyncio
    async def test_retoma_so_quem_tem_foto(self):
        from app.workers.tasks.raw_photo_tasks import _check_pending_raw_photos_async

        com_foto, sem_foto = _listing(), _listing()
        com_foto.id, sem_foto.id = "com", "sem"
        db = AsyncMock()
        db.execute = AsyncMock(return_value=MagicMock(
            scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[com_foto, sem_foto])))
        ))

        async def _resume(_db, listing):
            return listing.id == "com"

        with patch("app.database.worker_session", lambda: _sessao(db)), \
             patch("app.services.raw_photo_standby_service.try_resume_raw_photos", side_effect=_resume):
            result = await _check_pending_raw_photos_async()

        assert result == {"checked": 2, "resumed": 1}

    def test_beat_agenda_a_cada_15_minutos(self):
        from app.workers.celery_app import celery_app

        entrada = celery_app.conf.beat_schedule["check-pending-raw-photos"]
        assert entrada["task"] == "app.workers.tasks.raw_photo_tasks.check_pending_raw_photos"
        assert entrada["schedule"] == 15 * 60

    def test_task_registrada_e_roteada_para_default(self):
        from app.workers.celery_app import celery_app
        import app.workers.tasks.raw_photo_tasks  # noqa: F401 — registra a task

        assert "app.workers.tasks.raw_photo_tasks.check_pending_raw_photos" in celery_app.tasks
        assert celery_app.conf.task_routes["app.workers.tasks.raw_photo_tasks.*"] == {"queue": "default"}


class TestMensagemFotosAusentes:
    """A mensagem que o operador le (error_message do listing e 409 do
    'Verificar fotos agora') e derivada das constantes, nao de texto fixo:
    nomes obrigatorios de RAW_PHOTOS_MIN, formatos de RAW_PHOTO_EXTENSIONS."""

    def test_lista_cada_extensao_aceita(self):
        from app.services.raw_photo_standby_service import missing_photos_message
        from app.services.seller_image_source_service import RAW_PHOTO_EXTENSIONS

        msg = missing_photos_message("FAROL01")
        for ext in RAW_PHOTO_EXTENSIONS:
            assert f".{ext}" in msg, f"extensao .{ext} ausente em: {msg}"
        assert "em .jpg, .png ou .webp" in msg

    def test_lista_cada_nome_obrigatorio_sem_extensao_fixa(self):
        from app.services.raw_photo_standby_service import missing_photos_message
        from app.services.seller_image_source_service import RAW_PHOTOS_MIN

        msg = missing_photos_message("FAROL01")
        nomes = [f"FAROL01-{n}" for n in range(1, RAW_PHOTOS_MIN + 1)]
        for nome in nomes:
            assert nome in msg
        assert "FAROL01-1 e FAROL01-2" in msg, msg
        assert "FAROL01-1.jpg" not in msg, "extensao nao pode estar cravada no nome"


class TestRetomadaManual:
    @pytest.mark.asyncio
    async def test_status_errado_da_409(self):
        from fastapi import HTTPException
        from app.services.listing_service import ListingService

        listing = _listing(status="pending_image_approval")
        with pytest.raises(HTTPException) as exc:
            await ListingService(AsyncMock()).resume_raw_photos(listing)
        assert exc.value.status_code == 409

    @pytest.mark.asyncio
    async def test_fotos_ainda_ausentes_da_409_e_explica(self):
        from fastapi import HTTPException
        from app.services.listing_service import ListingService

        listing = _listing()
        with patch("app.services.raw_photo_standby_service.try_resume_raw_photos",
                   new_callable=AsyncMock, return_value=False):
            with pytest.raises(HTTPException) as exc:
                await ListingService(AsyncMock()).resume_raw_photos(listing)
        assert exc.value.status_code == 409
        assert "T99-1 e T99-2" in exc.value.detail and ".webp" in exc.value.detail

    @pytest.mark.asyncio
    async def test_fotos_presentes_retoma(self):
        from app.services.listing_service import ListingService

        listing = _listing()
        with patch("app.services.raw_photo_standby_service.try_resume_raw_photos",
                   new_callable=AsyncMock, return_value=True) as resume:
            await ListingService(AsyncMock()).resume_raw_photos(listing)
        resume.assert_awaited_once()
