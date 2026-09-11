"""Aprovacao de imagens recusa anuncio em que NADA e' aprovavel.

Sem isso, um anuncio sem imagem aprovada avancava assim mesmo: ia para
`generating_description`, gastava uma chamada ao Gemini, chegava a
`ready_to_publish` sem imagem e ainda gravava um evento de revisao dizendo
que um humano aprovou zero imagens.

- Em massa (`bulk_approve_images`): toda posicao reprovada no QA
  (`ml_picture_id=None`) deixa o UPDATE com `rowcount == 0`. O item tem que
  falhar com erro legivel, sem evento, sem mudar status e sem disparar
  `generate_description` — e sem contaminar o proximo item do lote.
- Individual (`approve_images`): o guard de lista vazia ja existia, mas uma
  lista so com ids de OUTRO anuncio passava por ele. Tem que ser 422 ANTES
  de qualquer escrita: nenhuma imagem do anuncio pode virar `rejected`.

Postgres real, mesma infraestrutura de `test_eventos_de_revisao.py`: so
roda com `TEST_DATABASE_URL` apontando para o banco dedicado
`publicar_test`, nunca contra o banco do ambiente.
"""
import os
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from tests.test_eventos_de_revisao import (
    _eventos,
    _linhas_padrao,
    _preparar_banco,
    _semear_listing,
    _semear_user_e_seller,
)

TEST_DB = os.environ.get("TEST_DATABASE_URL")
_precisa_db = pytest.mark.skipif(
    not TEST_DB, reason="TEST_DATABASE_URL nao definido: teste real so roda com Postgres dedicado"
)

ERRO_NADA_APROVAVEL = "nenhuma imagem aprovável"


def _linhas_tudo_reprovado() -> list[tuple]:
    """As 5 posicoes oficiais, todas reprovadas no QA (`ml_picture_id=None`,
    `status="validation_failed"` pela semeadura)."""
    return [
        ("cover_ai", 0, None),
        ("presentation_ai", 1, None),
        ("benefits_ai", 2, None),
        ("detail_ai", 3, None),
        ("specs_ai", 4, None),
    ]


async def _status_e_imagens(session_maker, listing_id):
    from app.models.listing import Listing
    from app.models.listing_image import ListingImage

    async with session_maker() as s:
        status = (
            await s.execute(select(Listing.status).where(Listing.id == listing_id))
        ).scalar_one()
        rows = (
            await s.execute(
                select(ListingImage)
                .where(ListingImage.listing_id == listing_id)
                .order_by(ListingImage.sort_order)
            )
        ).scalars().all()
    return status, rows


@_precisa_db
class TestBulkRecusaSemNadaAprovavel:
    @pytest.mark.asyncio
    async def test_tudo_reprovado_no_qa_falha_sem_evento_sem_status_sem_dispatch(self):
        from app.services.listing_service import ListingService

        engine, sm = await _preparar_banco()
        try:
            user_id, seller_id = await _semear_user_e_seller(sm)
            listing_id, _ = await _semear_listing(
                sm, seller_id, user_id, "pending_image_approval", _linhas_tudo_reprovado()
            )

            async with sm() as s:
                svc = ListingService(s, seller_id)
                with patch("app.workers.tasks.ai_tasks.generate_description") as mock_task:
                    mock_task.delay = MagicMock()
                    result = await svc.bulk_approve_images([listing_id], user_id=user_id)

            assert result.processed == 0, result.results
            assert result.failed == 1, result.results
            assert result.results[0].success is False
            assert result.results[0].error == ERRO_NADA_APROVAVEL, result.results[0].error

            mock_task.delay.assert_not_called()
            assert await _eventos(sm, listing_id) == []

            status, rows = await _status_e_imagens(sm, listing_id)
            assert status == "pending_image_approval", status
            assert all(r.approved is False for r in rows), [
                (r.kind, r.sort_order, r.approved) for r in rows
            ]
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_lote_misto_aprova_o_normal_e_falha_so_o_vazio(self):
        """Um item ruim nao contamina o outro: o normal gera 1 evento e vai
        para `generating_description`; o vazio falha e fica intacto."""
        from app.services.listing_service import ListingService

        engine, sm = await _preparar_banco()
        try:
            user_id, seller_id = await _semear_user_e_seller(sm)
            vazio, _ = await _semear_listing(
                sm, seller_id, user_id, "pending_image_approval", _linhas_tudo_reprovado()
            )
            normal, _ = await _semear_listing(
                sm, seller_id, user_id, "pending_image_approval", _linhas_padrao()
            )

            async with sm() as s:
                svc = ListingService(s, seller_id)
                with patch("app.workers.tasks.ai_tasks.generate_description") as mock_task:
                    mock_task.delay = MagicMock()
                    # O vazio vem PRIMEIRO de proposito: e' o estado dele que
                    # nao pode vazar para o item seguinte.
                    result = await svc.bulk_approve_images([vazio, normal], user_id=user_id)

            assert result.processed == 1, result.results
            assert result.failed == 1, result.results
            por_id = {r.listing_id: r for r in result.results}
            assert por_id[vazio].success is False
            assert por_id[vazio].error == ERRO_NADA_APROVAVEL, por_id[vazio].error
            assert por_id[normal].success is True, por_id[normal].error

            mock_task.delay.assert_called_once_with(str(normal))

            assert await _eventos(sm, vazio) == []
            eventos_normal = await _eventos(sm, normal)
            assert len(eventos_normal) == 1, eventos_normal
            assert eventos_normal[0].approved_count == 5

            status_vazio, _ = await _status_e_imagens(sm, vazio)
            status_normal, rows_normal = await _status_e_imagens(sm, normal)
            assert status_vazio == "pending_image_approval", status_vazio
            assert status_normal == "generating_description", status_normal
            assert sum(1 for r in rows_normal if r.approved) == 5
        finally:
            await engine.dispose()


@_precisa_db
class TestIndividualRecusaSemNadaAprovavel:
    @pytest.mark.asyncio
    async def test_ids_de_outro_anuncio_da_422_sem_escrever_nada(self):
        """Ids que existem mas pertencem a OUTRO listing: 422, zero eventos,
        status intacto e — o ponto — nenhuma imagem deste anuncio virou
        `rejected`."""
        from app.models.listing import Listing
        from app.services.listing_service import ListingService

        engine, sm = await _preparar_banco()
        try:
            user_id, seller_id = await _semear_user_e_seller(sm)
            alvo, _ = await _semear_listing(
                sm, seller_id, user_id, "pending_image_approval", _linhas_padrao()
            )
            outro, ids_do_outro = await _semear_listing(
                sm, seller_id, user_id, "pending_image_approval", _linhas_padrao()
            )

            async with sm() as s:
                listing = (
                    await s.execute(select(Listing).where(Listing.id == alvo))
                ).scalar_one()
                svc = ListingService(s, seller_id)
                with patch("app.workers.tasks.ai_tasks.generate_description") as mock_task:
                    mock_task.delay = MagicMock()
                    with pytest.raises(HTTPException) as exc:
                        await svc.approve_images(listing, ids_do_outro[:5], user_id=user_id)

            assert exc.value.status_code == 422, exc.value
            mock_task.delay.assert_not_called()
            assert await _eventos(sm, alvo) == []
            assert await _eventos(sm, outro) == []

            status, rows = await _status_e_imagens(sm, alvo)
            assert status == "pending_image_approval", status
            assert all(r.status == "uploaded" for r in rows), [
                (r.kind, r.sort_order, r.status) for r in rows
            ]
            assert all(r.approved is False for r in rows)

            _, rows_outro = await _status_e_imagens(sm, outro)
            assert all(r.status == "uploaded" and r.approved is False for r in rows_outro)
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_lista_vazia_continua_422(self):
        from app.models.listing import Listing
        from app.services.listing_service import ListingService

        engine, sm = await _preparar_banco()
        try:
            user_id, seller_id = await _semear_user_e_seller(sm)
            listing_id, _ = await _semear_listing(
                sm, seller_id, user_id, "pending_image_approval", _linhas_padrao()
            )

            async with sm() as s:
                listing = (
                    await s.execute(select(Listing).where(Listing.id == listing_id))
                ).scalar_one()
                svc = ListingService(s, seller_id)
                with patch("app.workers.tasks.ai_tasks.generate_description") as mock_task:
                    mock_task.delay = MagicMock()
                    with pytest.raises(HTTPException) as exc:
                        await svc.approve_images(listing, [], user_id=user_id)

            assert exc.value.status_code == 422, exc.value
            mock_task.delay.assert_not_called()
            assert await _eventos(sm, listing_id) == []
            status, rows = await _status_e_imagens(sm, listing_id)
            assert status == "pending_image_approval", status
            assert all(r.status == "uploaded" for r in rows)
        finally:
            await engine.dispose()
