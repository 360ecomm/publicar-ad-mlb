"""Regeneracao de UMA posicao, ponta a ponta, em Postgres REAL: endpoint
(service) cria o placeholder, o worker preenche, a linha anterior sai so no
sucesso, as outras posicoes nao sao tocadas, o anuncio nao muda de status,
aprovacoes ficam bloqueadas, o custo sai rotulado.

So roda com `TEST_DATABASE_URL` apontando para `publicar_test`
(`_pg_dedicado`). Nunca em paralelo com outra suite contra o mesmo banco.
"""
import os
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest
from fastapi import HTTPException

from tests.test_bulk_approve_por_posicao import _preparar_banco, _semear
from tests.test_cinco_posicoes import _Ambiente

TEST_DB = os.environ.get("TEST_DATABASE_URL")
_precisa_db = pytest.mark.skipif(
    not TEST_DB, reason="TEST_DATABASE_URL nao definido: teste real so roda com Postgres dedicado"
)

CINCO = [
    ("cover_ai", 0, "p0", "uploaded"),
    ("presentation_ai", 1, "p1", "uploaded"),
    ("benefits_ai", 2, "p2", "uploaded"),
    ("detail_ai", 3, "p3", "uploaded"),
    ("specs_ai", 4, "p4", "uploaded"),
]


class _AmbienteComRotulo(_Ambiente):
    """Registra o rotulo de custo vigente em cada chamada ao motor."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.rotulos = []

    async def _edit(self, images, prompt, n, size=None):
        from app.services.ai.cost_log import image_edit_task
        self.rotulos.append(image_edit_task())
        return await super()._edit(images, prompt, n, size)


class _AmbienteQueAprovaDurante(_Ambiente):
    """Um humano aprova a linha anterior ENQUANTO o motor gera.

    A geracao leva minutos; entre a consulta que captura as anteriores e o
    DELETE do sucesso cabe uma aprovacao. Sem o predicado dentro do DELETE, a
    regeneracao apagaria uma imagem aprovada — e publicada.
    """

    def __init__(self, sm, listing_id, ml_picture_id, **kw):
        super().__init__(**kw)
        self._sm = sm
        self._listing_id = listing_id
        self._ml_picture_id = ml_picture_id

    async def _edit(self, images, prompt, n, size=None):
        from sqlalchemy import update

        from app.models.listing_image import ListingImage

        async with self._sm() as s:
            await s.execute(
                update(ListingImage)
                .where(
                    ListingImage.listing_id == self._listing_id,
                    ListingImage.ml_picture_id == self._ml_picture_id,
                )
                .values(approved=True)
            )
            await s.commit()
        return await super()._edit(images, prompt, n, size)


@asynccontextmanager
async def _sessao_real(sm):
    async with sm() as s:
        yield s


async def _linhas(sm, listing_id):
    from sqlalchemy import select

    from app.models.listing_image import ListingImage

    async with sm() as s:
        rows = (await s.execute(
            select(ListingImage).where(ListingImage.listing_id == listing_id)
            .order_by(ListingImage.sort_order, ListingImage.created_at)
        )).scalars().all()
        return [(r.id, r.sort_order, r.kind, r.status, r.ml_picture_id, r.approved) for r in rows]


async def _status_do_listing(sm, listing_id):
    from sqlalchemy import select

    from app.models.listing import Listing

    async with sm() as s:
        return (await s.execute(select(Listing).where(Listing.id == listing_id))).scalar_one().status


async def _eventos(sm, listing_id):
    from sqlalchemy import func, select

    from app.models.listing_review_event import ListingReviewEvent

    async with sm() as s:
        return (await s.execute(
            select(func.count()).select_from(ListingReviewEvent)
            .where(ListingReviewEvent.listing_id == listing_id)
        )).scalar_one()


async def _pedir_regeneracao(sm, listing_id, seller_id, posicao):
    """Chama o service como o endpoint faz; devolve o id do placeholder.
    `regenerate_position.delay` e' mockado — o worker roda a mao no teste."""
    from app.services.listing_service import ListingService

    async with sm() as s:
        svc = ListingService(s, seller_id)
        listing = await svc.get_or_404(listing_id, seller_id)
        with patch("app.workers.tasks.image_tasks.regenerate_position") as task:
            task.delay = MagicMock()
            placeholder = await svc.regenerate_position(listing, posicao)
            task.delay.assert_called_once_with(str(listing_id), str(placeholder.id))
        return placeholder.id


async def _rodar_worker(sm, listing_id, placeholder_id, ambiente):
    from app.workers.tasks.image_tasks import _regenerate_position_async

    with ambiente as amb, \
         patch("app.database.worker_session", lambda: _sessao_real(sm)), \
         patch("app.workers.tasks.image_tasks._fetch_upload_token", new_callable=AsyncMock, return_value="tok"), \
         patch("app.workers.tasks.image_tasks._carregar_fotos_brutas", new_callable=AsyncMock,
               return_value=([b"f1", b"f2", b"f3"], "38")):
        result = await _regenerate_position_async(str(listing_id), str(placeholder_id))
    return result, amb


@_precisa_db
class TestRegeneracaoPontaAPonta:
    @pytest.mark.asyncio
    async def test_substitui_so_a_posicao_pedida_e_rotula_o_custo(self):
        engine, sm = await _preparar_banco()
        try:
            listing_id, seller_id, _ = await _semear(sm, CINCO)
            antes = await _linhas(sm, listing_id)
            id_antiga_2 = next(r[0] for r in antes if r[1] == 2)

            pid = await _pedir_regeneracao(sm, listing_id, seller_id, 2)
            durante = await _linhas(sm, listing_id)
            assert (pid, 2, "benefits_ai", "generating", None, False) in durante

            result, amb = await _rodar_worker(sm, listing_id, pid, _AmbienteComRotulo())

            assert result["status"] == "uploaded" and result["removidas"] == 1
            assert amb.rotulos == ["image_edit_regen"], "uma chamada, rotulada"
            depois = await _linhas(sm, listing_id)
            na_2 = [r for r in depois if r[1] == 2]
            assert na_2 == [(pid, 2, "benefits_ai", "uploaded", na_2[0][4], False)]
            assert na_2[0][4] and na_2[0][4] != "p2"
            assert id_antiga_2 not in {r[0] for r in depois}
            # As outras 4 posicoes: mesmos ids, mesmos ml_picture_id.
            outras_antes = sorted(r for r in antes if r[1] != 2)
            outras_depois = sorted(r for r in depois if r[1] != 2)
            assert outras_antes == outras_depois
            assert await _status_do_listing(sm, listing_id) == "pending_image_approval"
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_falha_do_motor_mantem_a_anterior_e_nao_muda_o_status(self):
        engine, sm = await _preparar_banco()
        try:
            listing_id, seller_id, _ = await _semear(sm, CINCO)
            pid = await _pedir_regeneracao(sm, listing_id, seller_id, 2)

            result, _ = await _rodar_worker(sm, listing_id, pid, _Ambiente(falhar_em={0, 1}))

            assert result["status"] == "generation_failed"
            depois = await _linhas(sm, listing_id)
            na_2 = sorted(r for r in depois if r[1] == 2)
            assert {r[3] for r in na_2} == {"uploaded", "generation_failed"}
            assert any(r[4] == "p2" for r in na_2), "a anterior permanece"
            assert await _status_do_listing(sm, listing_id) == "pending_image_approval"
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_posicao_0_cai_no_fallback_deterministico(self):
        engine, sm = await _preparar_banco()
        try:
            listing_id, seller_id, _ = await _semear(sm, CINCO)
            pid = await _pedir_regeneracao(sm, listing_id, seller_id, 0)

            result, amb = await _rodar_worker(sm, listing_id, pid, _Ambiente(falhar_em={0, 1}))

            assert result["status"] == "uploaded" and result["kind"] == "cover_deterministic"
            assert len(amb.prompts) == 2, "2 tentativas da IA, depois o fallback sem IA"
            na_0 = [r for r in await _linhas(sm, listing_id) if r[1] == 0]
            assert len(na_0) == 1 and na_0[0][0] == pid and na_0[0][2] == "cover_deterministic"
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_posicao_aprovada_e_recusada_sem_placeholder(self):
        from sqlalchemy import update

        from app.models.listing_image import ListingImage

        engine, sm = await _preparar_banco()
        try:
            listing_id, seller_id, _ = await _semear(sm, CINCO)
            async with sm() as s:
                await s.execute(update(ListingImage).where(
                    ListingImage.listing_id == listing_id, ListingImage.sort_order == 2
                ).values(approved=True))
                await s.commit()

            with pytest.raises(HTTPException) as exc:
                await _pedir_regeneracao(sm, listing_id, seller_id, 2)
            assert exc.value.status_code == 409 and "aprovada" in exc.value.detail
            assert len([r for r in await _linhas(sm, listing_id) if r[1] == 2]) == 1
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_duplo_disparo_gera_um_placeholder_so(self):
        engine, sm = await _preparar_banco()
        try:
            listing_id, seller_id, _ = await _semear(sm, CINCO)
            pid = await _pedir_regeneracao(sm, listing_id, seller_id, 2)
            with pytest.raises(HTTPException) as exc:
                await _pedir_regeneracao(sm, listing_id, seller_id, 2)
            assert exc.value.status_code == 409
            assert exc.value.detail == "Regeneração em andamento na posição 2; aguarde."
            gerando = [r for r in await _linhas(sm, listing_id) if r[3] == "generating"]
            assert [r[0] for r in gerando] == [pid]
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_aprovacoes_bloqueadas_durante_regeneracao(self):
        from app.services.listing_service import ListingService

        engine, sm = await _preparar_banco()
        try:
            listing_id, seller_id, user_id = await _semear(sm, CINCO)
            await _pedir_regeneracao(sm, listing_id, seller_id, 3)
            id_da_1 = next(r[0] for r in await _linhas(sm, listing_id) if r[1] == 1)
            esperada = "Regeneração em andamento na posição 3; aguarde a conclusão antes de aprovar."

            async with sm() as s:
                svc = ListingService(s, seller_id)
                listing = await svc.get_or_404(listing_id, seller_id)
                with patch("app.workers.tasks.ai_tasks.generate_description") as gen:
                    with pytest.raises(HTTPException) as exc:
                        await svc.approve_images(listing, [id_da_1], user_id=user_id)
                    assert exc.value.status_code == 409 and exc.value.detail == esperada
                    gen.delay.assert_not_called()

            async with sm() as s:
                with patch("app.workers.tasks.ai_tasks.generate_description") as gen:
                    result = await ListingService(s, seller_id).bulk_approve_images([listing_id], user_id=user_id)
                assert result.failed == 1 and result.results[0].error == esperada
                gen.delay.assert_not_called()

            assert await _status_do_listing(sm, listing_id) == "pending_image_approval"
            assert await _eventos(sm, listing_id) == 0
            assert all(r[5] is False for r in await _linhas(sm, listing_id))
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_worker_apaga_so_a_nao_aprovada_da_posicao(self):
        """Segunda cerca do worker, provada no SQL real (a revisao da Task 4
        apontou que o WHERE da consulta destrutiva so era exercido com mock):
        com uma linha APROVADA e uma nao aprovada na mesma posicao — estado que
        o endpoint recusa, mas o worker nao pode depender disso — mais uma
        candidata em 90, so a nao aprovada sai. O placeholder e' semeado
        direto, sem passar pelo service."""
        from sqlalchemy import update

        from app.models.listing_image import ListingImage

        engine, sm = await _preparar_banco()
        try:
            listing_id, seller_id, _ = await _semear(sm, CINCO + [
                ("benefits_ai", 2, None, "validation_failed"),
                ("cover_ai", 90, "c90", "uploaded"),
                ("benefits_ai", 2, None, "generating"),
            ])
            async with sm() as s:
                await s.execute(update(ListingImage).where(
                    ListingImage.listing_id == listing_id, ListingImage.ml_picture_id == "p2"
                ).values(approved=True))
                await s.commit()
            linhas = await _linhas(sm, listing_id)
            pid = next(r[0] for r in linhas if r[3] == "generating")
            id_aprovada = next(r[0] for r in linhas if r[4] == "p2")
            id_velha = next(r[0] for r in linhas if r[3] == "validation_failed")
            id_candidata = next(r[0] for r in linhas if r[1] == 90)

            result, _ = await _rodar_worker(sm, listing_id, pid, _Ambiente())

            assert result["status"] == "uploaded" and result["removidas"] == 1
            depois = await _linhas(sm, listing_id)
            ids = {r[0] for r in depois}
            assert id_aprovada in ids, "aprovada nunca e' apagada"
            assert id_candidata in ids, "candidata em 90 nao e' desta posicao"
            assert pid in ids and id_velha not in ids
            assert next(r for r in depois if r[0] == id_aprovada)[5] is True
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_anterior_aprovada_durante_a_geracao_sobrevive(self, caplog):
        """A cerca real e' o predicado DO DELETE, avaliado no momento do delete.
        A linha `p2` e' capturada como candidata a remocao, aprovada durante a
        geracao, e o DELETE nao a alcanca: ficam duas linhas na posicao 2."""
        import logging

        engine, sm = await _preparar_banco()
        try:
            listing_id, seller_id, _ = await _semear(sm, CINCO)
            id_antiga_2 = next(r[0] for r in await _linhas(sm, listing_id) if r[1] == 2)

            pid = await _pedir_regeneracao(sm, listing_id, seller_id, 2)
            ambiente = _AmbienteQueAprovaDurante(sm, listing_id, "p2")
            with caplog.at_level(logging.WARNING, logger="app.workers.tasks.image_tasks"):
                result, _ = await _rodar_worker(sm, listing_id, pid, ambiente)

            assert result["status"] == "uploaded"
            assert result["removidas"] == 0, "nada foi apagado — nao pode mentir no retorno"
            assert "anteriores_preservadas=1" in caplog.text

            depois = await _linhas(sm, listing_id)
            na_2 = {r[0]: r for r in depois if r[1] == 2}
            assert len(na_2) == 2, "a aprovada sobrevive ao lado da nova"
            antiga = na_2[id_antiga_2]
            assert antiga[4] == "p2" and antiga[5] is True, "aprovada, intocada"
            novo = na_2[pid]
            assert novo[3] == "uploaded" and novo[5] is False
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_aprovacao_que_venceu_a_corrida_faz_o_worker_desistir(self):
        """Placeholder criado, anuncio aprovado antes de o worker rodar
        (aprovacao em massa nao mexe no status da linha): o worker apaga o
        placeholder e nao gera nada."""
        from sqlalchemy import update

        from app.models.listing import Listing

        engine, sm = await _preparar_banco()
        try:
            listing_id, seller_id, _ = await _semear(sm, CINCO)
            pid = await _pedir_regeneracao(sm, listing_id, seller_id, 2)
            async with sm() as s:
                await s.execute(update(Listing).where(Listing.id == listing_id)
                                .values(status="generating_description"))
                await s.commit()

            result, amb = await _rodar_worker(sm, listing_id, pid, _Ambiente())

            assert result["skipped"] is True and amb.prompts == []
            assert pid not in {r[0] for r in await _linhas(sm, listing_id)}
            assert await _status_do_listing(sm, listing_id) == "generating_description"
        finally:
            await engine.dispose()
