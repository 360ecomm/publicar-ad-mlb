"""Regeneracao de UMA posicao — lado do worker.

Parte 1 (esta task): `_gerar_posicao` isolada, extraida de
`_gerar_cinco_posicoes` sem mudar o caminho completo (a prova disso sao os
testes existentes de imagem, intocados). Com `alvo`, preenche o placeholder
em vez de abrir linha nova.

Parte 2 (Task 4): orquestracao de `_regenerate_position_async`.

Reusa o ambiente de mocks de `test_cinco_posicoes.py`.
"""
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from tests.test_cinco_posicoes import (
    _Ambiente,
    _atributos_sku38,
    _db_com_atributos,
    _fotos,
    _listing,
    _salvos,
)


def _placeholder(sort_order: int, kind: str):
    from app.models.listing_image import ListingImage
    return ListingImage(
        id=uuid4(), listing_id=uuid4(), status="generating", approved=False,
        sort_order=sort_order, kind=kind,
    )


async def _contexto(db, **kw):
    from app.services.image_position_profiles import PERFIL_PERFUMARIA
    from app.workers.tasks.image_tasks import _montar_contexto
    return await _montar_contexto(db, _listing(), "tok", PERFIL_PERFUMARIA, _fotos(), "38", **kw)


class TestGerarPosicaoIsolada:
    @pytest.mark.asyncio
    async def test_posicao_2_sozinha_faz_uma_chamada_e_grava_uma_linha(self):
        from app.workers.tasks.image_tasks import _gerar_posicao

        db = _db_com_atributos(_atributos_sku38())
        with _Ambiente() as amb:
            ctx = await _contexto(db)
            ok = await _gerar_posicao(db, _listing(), ctx, 2)

        assert ok is True
        assert len(amb.prompts) == 1
        (img,) = _salvos(db)
        assert (img.kind, img.sort_order, img.approved) == ("benefits_ai", 2, False)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("numero,kind", [(0, "cover_ai"), (1, "presentation_ai"), (3, "detail_ai"), (4, "specs_ai")])
    async def test_cada_posicao_grava_o_kind_da_sua_posicao(self, numero, kind):
        from app.workers.tasks.image_tasks import _gerar_posicao

        db = _db_com_atributos(_atributos_sku38())
        with _Ambiente() as amb:
            ctx = await _contexto(db)
            assert await _gerar_posicao(db, _listing(), ctx, numero) is True

        assert len(amb.prompts) == 1
        (img,) = _salvos(db)
        assert (img.kind, img.sort_order) == (kind, numero)

    @pytest.mark.asyncio
    async def test_sem_copy_nao_chama_o_llm(self):
        """Regenerar 0, 1, 3 ou 4 nao pode pagar a copy dos cards."""
        db = _db_com_atributos(_atributos_sku38())
        with _Ambiente(), patch(
            "app.services.image_card_copy_service.generate_card_copy",
            new_callable=AsyncMock, return_value=[],
        ) as copy:
            ctx = await _contexto(db, com_campos=True, com_copy=False)
        copy.assert_not_awaited()
        assert ctx.campos is not None and ctx.campos["beneficios"] is None
        assert ctx.campos["nome"] == "Fatal Black For Her"

    @pytest.mark.asyncio
    async def test_com_copy_chama_o_llm_uma_vez(self):
        db = _db_com_atributos(_atributos_sku38())
        with _Ambiente(), patch(
            "app.services.image_card_copy_service.generate_card_copy",
            new_callable=AsyncMock, return_value=[],
        ) as copy:
            await _contexto(db, com_campos=True, com_copy=True)
        copy.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_sem_campos_nao_consulta_atributos(self):
        db = _db_com_atributos(_atributos_sku38())
        with _Ambiente():
            ctx = await _contexto(db, com_campos=False)
        assert ctx.campos is None
        db.execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_alvo_e_preenchido_em_vez_de_nova_linha(self):
        from app.workers.tasks.image_tasks import _gerar_posicao

        db = _db_com_atributos(_atributos_sku38())
        alvo = _placeholder(3, "detail_ai")
        with _Ambiente():
            ctx = await _contexto(db, com_campos=False)
            ok = await _gerar_posicao(db, _listing(), ctx, 3, alvo=alvo)

        assert ok is True
        db.add.assert_not_called()
        assert alvo.status == "uploaded"
        assert alvo.ml_picture_id.startswith("pic-")
        assert alvo.asset_key == "asset-key-teste"
        assert (alvo.kind, alvo.sort_order, alvo.approved) == ("detail_ai", 3, False)
        assert alvo.validation_error is None

    @pytest.mark.asyncio
    async def test_alvo_reprovado_no_qa_guarda_evidencia(self):
        from app.workers.tasks.image_tasks import _gerar_posicao

        db = _db_com_atributos(_atributos_sku38())
        alvo = _placeholder(3, "detail_ai")
        with _Ambiente(qa_reprova=True):
            ctx = await _contexto(db, com_campos=False)
            ok = await _gerar_posicao(db, _listing(), ctx, 3, alvo=alvo)

        assert ok is False
        db.add.assert_not_called()
        assert alvo.status == "validation_failed"
        assert alvo.ml_picture_id is None
        assert alvo.asset_key == "asset-key-teste", "bytes crus vao ao R2 mesmo reprovados"
        assert alvo.validation_error

    @pytest.mark.asyncio
    async def test_posicao_0_com_ia_falhando_cai_no_fallback_dentro_do_alvo(self):
        """As 2 tentativas da IA falham (indices 0 e 1) -> capa deterministica
        preenche o PROPRIO placeholder, kind cover_deterministic."""
        from app.workers.tasks.image_tasks import _gerar_posicao

        db = _db_com_atributos(_atributos_sku38())
        alvo = _placeholder(0, "cover_ai")
        with _Ambiente(falhar_em={0, 1}) as amb:
            ctx = await _contexto(db, com_campos=False)
            ok = await _gerar_posicao(db, _listing(), ctx, 0, alvo=alvo)

        assert ok is True
        assert len(amb.prompts) == 2
        db.add.assert_not_called()
        assert (alvo.kind, alvo.status, alvo.sort_order) == ("cover_deterministic", "uploaded", 0)
        assert alvo.ml_picture_id and alvo.approved is False

    @pytest.mark.asyncio
    async def test_posicao_0_com_ia_falhando_sem_alvo_grava_linha_nova(self):
        """Mesmo fallback do caminho completo, sem alvo: linha nova."""
        from app.workers.tasks.image_tasks import _gerar_posicao

        db = _db_com_atributos(_atributos_sku38())
        with _Ambiente(falhar_em={0, 1}):
            ctx = await _contexto(db, com_campos=False)
            assert await _gerar_posicao(db, _listing(), ctx, 0) is True
        (img,) = _salvos(db)
        assert (img.kind, img.sort_order) == ("cover_deterministic", 0)

    @pytest.mark.asyncio
    async def test_posicao_0_reprovada_no_qa_guarda_evidencia_e_fallback_em_linha_nova(self):
        """IA produz, QA reprova: o placeholder GUARDA a evidencia
        (validation_failed) e o fallback deterministico vai para uma linha
        NOVA — nunca sobrescreve a evidencia da reprovacao."""
        from app.services.image_service import ImageValidationResult
        from app.workers.tasks.image_tasks import _gerar_posicao

        db = _db_com_atributos(_atributos_sku38())
        alvo = _placeholder(0, "cover_ai")
        with _Ambiente() as amb:
            ctx = await _contexto(db, com_campos=False)
            assert ctx.base == b"preparado"
            # A partir daqui, o QA reprova: a capa deterministica computada em
            # `_montar_contexto` ja passou pelo QA (ctx.base ficou pronta); so
            # a tentativa da IA em `_gerar_posicao` e' que sera reprovada.
            amb.prepare.return_value = (
                None, ImageValidationResult(is_valid=False, errors=["reprovado"])
            )
            ok = await _gerar_posicao(db, _listing(), ctx, 0, alvo=alvo)

        assert ok is True
        assert len(amb.prompts) == 1, "uma chamada de IA, depois o fallback sem IA"

        assert alvo.status == "validation_failed"
        assert alvo.kind == "cover_ai"
        assert alvo.ml_picture_id is None
        assert alvo.asset_key == "asset-key-teste"
        assert alvo.validation_error

        db.add.assert_called_once()
        (nova,) = _salvos(db)
        assert nova.kind == "cover_deterministic"
        assert nova.status == "uploaded"
        assert nova.sort_order == 0
        assert nova.approved is False

    @pytest.mark.asyncio
    async def test_posicao_2_sem_copy_devolve_false_sem_chamar_o_motor(self):
        from app.workers.tasks.image_tasks import _gerar_posicao

        db = _db_com_atributos(_atributos_sku38())
        with _Ambiente() as amb:
            ctx = await _contexto(db, com_campos=True, com_copy=False)
            assert await _gerar_posicao(db, _listing(), ctx, 2) is False
        assert amb.prompts == []
        db.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_posicao_fora_de_0_a_4_levanta(self):
        from app.workers.tasks.image_tasks import _gerar_posicao

        db = _db_com_atributos(_atributos_sku38())
        with _Ambiente():
            ctx = await _contexto(db, com_campos=False)
            with pytest.raises(ValueError):
                await _gerar_posicao(db, _listing(), ctx, 5)


class TestCarregarFotosBrutas:
    @pytest.mark.asyncio
    async def test_sem_config_devolve_none(self):
        from app.workers.tasks.image_tasks import _carregar_fotos_brutas

        db = AsyncMock()
        db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))
        assert await _carregar_fotos_brutas(db, _listing()) is None

    @pytest.mark.asyncio
    async def test_devolve_fotos_e_sku(self):
        from app.workers.tasks.image_tasks import _carregar_fotos_brutas

        cfg = MagicMock(); cfg.raw_base_url = "https://b/x"
        db = AsyncMock()
        db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=cfg)))
        with patch("app.services.seller_image_source_service.fetch_all_raw_photos",
                   new_callable=AsyncMock, return_value={"38": [b"1", b"2"]}):
            assert await _carregar_fotos_brutas(db, _listing()) == ([b"1", b"2"], "38")


# --- parte 2: orquestracao do worker -------------------------------------
from contextlib import asynccontextmanager


@asynccontextmanager
async def _sessao(db):
    yield db


def _listing_pendente():
    listing = MagicMock()
    listing.id = uuid4(); listing.seller_id = uuid4(); listing.sku_external_id = "38"
    listing.ml_category_id = "MLB6284"; listing.status = "pending_image_approval"
    listing.error_message = None
    return listing


def _linha(sort_order, status="uploaded", asset_key="k-antiga", approved=False):
    from app.models.listing_image import ListingImage
    return ListingImage(id=uuid4(), listing_id=uuid4(), status=status, approved=approved,
                        sort_order=sort_order, kind="benefits_ai", asset_key=asset_key)


def _db_worker(alvo, listing, anteriores=(), extra=()):
    """Respostas na ORDEM das consultas de `_regenerate_position_async`:
    placeholder, listing, anteriores, seller (+ `extra`, ex.: recarga do
    placeholder depois de rollback)."""
    db = AsyncMock()
    respostas = [
        MagicMock(scalar_one_or_none=MagicMock(return_value=alvo)),
        MagicMock(scalar_one=MagicMock(return_value=listing)),
        MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=list(anteriores))))),
        MagicMock(scalar_one=MagicMock(return_value=MagicMock())),
        *extra,
    ]
    db.execute = AsyncMock(side_effect=respostas)
    db.add = MagicMock(); db.commit = AsyncMock(); db.rollback = AsyncMock(); db.delete = AsyncMock()
    return db


def _patches_worker(db, gerar):
    """worker_session, token, fotos e contexto mockados; `gerar` substitui `_gerar_posicao`."""
    return [
        patch("app.database.worker_session", lambda: _sessao(db)),
        patch("app.workers.tasks.image_tasks._fetch_upload_token", new_callable=AsyncMock, return_value="tok"),
        patch("app.workers.tasks.image_tasks._carregar_fotos_brutas", new_callable=AsyncMock,
              return_value=([b"1", b"2", b"3"], "38")),
        patch("app.workers.tasks.image_tasks._montar_contexto", new_callable=AsyncMock, return_value=MagicMock()),
        patch("app.workers.tasks.image_tasks._gerar_posicao", gerar),
    ]


async def _rodar(db, gerar, listing_id, image_id):
    from contextlib import ExitStack

    from app.workers.tasks.image_tasks import _regenerate_position_async

    with ExitStack() as stack:
        for p in _patches_worker(db, gerar):
            stack.enter_context(p)
        return await _regenerate_position_async(str(listing_id), str(image_id))


class TestRegenerarPosicaoWorker:
    @pytest.mark.asyncio
    async def test_sucesso_preenche_o_placeholder_e_apaga_as_anteriores(self):
        listing = _listing_pendente()
        alvo = _placeholder(2, "benefits_ai")
        antiga = _linha(2, asset_key="CAFE085/38/benefits_ai-old.jpg")
        rotulos = []

        async def gerar(db, l, ctx, numero, alvo=None):
            from app.services.ai.cost_log import image_edit_task
            rotulos.append((numero, image_edit_task()))
            alvo.status = "uploaded"; alvo.ml_picture_id = "pic-novo"
            return True

        db = _db_worker(alvo, listing, anteriores=[antiga])
        result = await _rodar(db, gerar, listing.id, alvo.id)

        assert rotulos == [(2, "image_edit_regen")]
        db.delete.assert_awaited_once_with(antiga)
        db.commit.assert_awaited()
        assert listing.status == "pending_image_approval" and listing.error_message is None
        assert result["status"] == "uploaded" and result["removidas"] == 1

    @pytest.mark.asyncio
    async def test_sucesso_via_fallback_com_placeholder_reprovado_reporta_uploaded(self):
        """Posicao 0: a IA foi reprovada no QA (o alvo guarda a evidencia como
        `validation_failed`) e o fallback deterministico subiu em linha nova.
        `subiu` e' True — a posicao foi ocupada — entao o resultado reportado
        tem que dizer `uploaded`, nao o status de evidencia do `alvo`."""
        listing = _listing_pendente()
        alvo = _placeholder(0, "cover_ai")
        antiga = _linha(0, asset_key="CAFE085/38/cover_ai-old.jpg")

        async def gerar(db, l, ctx, numero, alvo=None):
            alvo.status = "validation_failed"; alvo.validation_error = "fundo"
            return True

        db = _db_worker(alvo, listing, anteriores=[antiga])
        result = await _rodar(db, gerar, listing.id, alvo.id)

        db.delete.assert_awaited_once_with(antiga)
        assert result["status"] == "uploaded"
        assert result["kind"] == "cover_deterministic"
        assert result["removidas"] == 1
        assert alvo.status == "validation_failed", "evidencia intocada"
        assert listing.status == "pending_image_approval"

    @pytest.mark.asyncio
    async def test_contexto_pede_campos_e_copy_so_quando_a_posicao_exige(self):
        """0 e 3: sem campos. 1 e 4: campos sem copy. 2: campos com copy."""
        from app.workers.tasks.image_tasks import _regenerate_position_async

        esperado = {0: (False, False), 1: (True, False), 2: (True, True), 3: (False, False), 4: (True, False)}
        for numero, (com_campos, com_copy) in esperado.items():
            listing = _listing_pendente()
            alvo = _placeholder(numero, "x")

            async def gerar(db, l, ctx, n, alvo=None):
                alvo.status = "uploaded"; return True

            db = _db_worker(alvo, listing)
            from contextlib import ExitStack
            with ExitStack() as stack:
                patches = _patches_worker(db, gerar)
                for p in patches[:3] + patches[4:]:
                    stack.enter_context(p)
                montar = stack.enter_context(patch(
                    "app.workers.tasks.image_tasks._montar_contexto",
                    new_callable=AsyncMock, return_value=MagicMock(),
                ))
                await _regenerate_position_async(str(listing.id), str(alvo.id))
            kw = montar.await_args.kwargs
            assert (kw["com_campos"], kw["com_copy"]) == (com_campos, com_copy), numero

    @pytest.mark.asyncio
    async def test_motor_nao_produz_vira_generation_failed_e_mantem_a_anterior(self):
        listing = _listing_pendente()
        alvo = _placeholder(2, "benefits_ai")
        antiga = _linha(2)

        async def gerar(db, l, ctx, numero, alvo=None):
            return False  # 2 tentativas falharam; placeholder continua `generating`

        db = _db_worker(alvo, listing, anteriores=[antiga])
        result = await _rodar(db, gerar, listing.id, alvo.id)

        assert alvo.status == "generation_failed"
        assert alvo.validation_error
        db.delete.assert_not_awaited()
        db.commit.assert_awaited()
        assert listing.status == "pending_image_approval" and listing.error_message is None
        assert result["status"] == "generation_failed"

    @pytest.mark.asyncio
    async def test_qa_reprovou_mantem_evidencia_e_a_anterior(self):
        listing = _listing_pendente()
        alvo = _placeholder(2, "benefits_ai")
        antiga = _linha(2)

        async def gerar(db, l, ctx, numero, alvo=None):
            alvo.status = "validation_failed"; alvo.validation_error = "fundo"; alvo.asset_key = "k"
            return False

        db = _db_worker(alvo, listing, anteriores=[antiga])
        result = await _rodar(db, gerar, listing.id, alvo.id)

        assert alvo.status == "validation_failed", "nao pode ser sobrescrito por generation_failed"
        db.delete.assert_not_awaited()
        db.commit.assert_awaited()
        assert result["status"] == "validation_failed"

    @pytest.mark.asyncio
    async def test_motor_indisponivel_faz_rollback_e_generation_failed_sem_standby(self):
        from app.services.image_engines.base import ImageEngineUnavailableError

        listing = _listing_pendente()
        alvo = _placeholder(2, "benefits_ai")

        async def gerar(db, l, ctx, numero, alvo=None):
            raise ImageEngineUnavailableError("OpenAI Edits API 429: insufficient_quota")

        recarga = MagicMock(scalar_one=MagicMock(return_value=alvo))
        db = _db_worker(alvo, listing, extra=[recarga])
        result = await _rodar(db, gerar, listing.id, alvo.id)

        db.rollback.assert_awaited_once()
        assert alvo.status == "generation_failed"
        assert "insufficient_quota" in alvo.validation_error
        assert listing.status == "pending_image_approval", "nunca pending_ai_engine"
        assert listing.error_message is None
        assert result["status"] == "generation_failed"

    @pytest.mark.asyncio
    async def test_fotos_brutas_ausentes_vira_generation_failed(self):
        from contextlib import ExitStack

        from app.workers.tasks.image_tasks import _regenerate_position_async

        listing = _listing_pendente()
        alvo = _placeholder(1, "presentation_ai")
        db = _db_worker(alvo, listing)
        with ExitStack() as stack:
            stack.enter_context(patch("app.database.worker_session", lambda: _sessao(db)))
            stack.enter_context(patch("app.workers.tasks.image_tasks._fetch_upload_token",
                                      new_callable=AsyncMock, return_value="tok"))
            stack.enter_context(patch("app.workers.tasks.image_tasks._carregar_fotos_brutas",
                                      new_callable=AsyncMock, return_value=None))
            montar = stack.enter_context(patch("app.workers.tasks.image_tasks._montar_contexto",
                                               new_callable=AsyncMock))
            await _regenerate_position_async(str(listing.id), str(alvo.id))

        montar.assert_not_awaited()
        assert alvo.status == "generation_failed" and "fotos brutas" in alvo.validation_error
        assert listing.status == "pending_image_approval"

    @pytest.mark.asyncio
    async def test_placeholder_ja_consumido_e_skipped_antes_de_gastar(self):
        listing = _listing_pendente()
        alvo = _placeholder(2, "benefits_ai"); alvo.status = "rejected"
        gerar = AsyncMock()
        db = _db_worker(alvo, listing)
        result = await _rodar(db, gerar, listing.id, alvo.id)

        assert result["skipped"] is True
        gerar.assert_not_awaited()
        assert db.execute.await_count == 1
        db.delete.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_placeholder_inexistente_e_skipped(self):
        listing = _listing_pendente()
        db = _db_worker(None, listing)
        result = await _rodar(db, AsyncMock(), listing.id, uuid4())
        assert result["skipped"] is True

    @pytest.mark.asyncio
    async def test_anuncio_que_saiu_de_pending_image_approval_apaga_o_placeholder(self):
        listing = _listing_pendente(); listing.status = "generating_description"
        alvo = _placeholder(2, "benefits_ai")
        gerar = AsyncMock()
        db = _db_worker(alvo, listing)
        result = await _rodar(db, gerar, listing.id, alvo.id)

        assert result["skipped"] is True
        gerar.assert_not_awaited()
        db.delete.assert_awaited_once_with(alvo)
        db.commit.assert_awaited_once()
        assert listing.status == "generating_description", "o worker nao toca no status"

    @pytest.mark.asyncio
    async def test_mark_regen_failed_so_toca_placeholder_generating(self):
        from app.workers.tasks.image_tasks import _mark_regen_failed

        alvo = _placeholder(2, "benefits_ai")
        db = AsyncMock()
        db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=alvo)))
        db.commit = AsyncMock()
        with patch("app.database.worker_session", lambda: _sessao(db)):
            await _mark_regen_failed(str(alvo.id), "boom")
        assert alvo.status == "generation_failed" and alvo.validation_error == "boom"

        ja_pronto = _placeholder(2, "benefits_ai"); ja_pronto.status = "uploaded"
        db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=ja_pronto)))
        with patch("app.database.worker_session", lambda: _sessao(db)):
            await _mark_regen_failed(str(ja_pronto.id), "boom")
        assert ja_pronto.status == "uploaded"

    def test_task_registrada_na_fila_de_imagens(self):
        from app.workers.celery_app import celery_app
        from app.workers.tasks.image_tasks import regenerate_position

        assert regenerate_position.name == "app.workers.tasks.image_tasks.regenerate_position"
        assert celery_app.conf.task_routes["app.workers.tasks.image_tasks.*"] == {"queue": "images"}
        assert regenerate_position.max_retries == 2
