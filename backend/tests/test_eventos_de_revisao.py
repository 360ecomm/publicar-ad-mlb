"""Toda aprovacao humana de imagens grava UM evento em `listing_review_events`,
na MESMA transacao da aprovacao: aprovacao sem evento ou evento sem aprovacao
tem que ser impossivel. Individual grava o `review_seconds` recebido (pode
ser `None`); em massa grava sempre `review_seconds=NULL` e `mode="bulk"` —
nunca estima nem reparte tempo entre os anuncios do lote. Listing que falha
na aprovacao em massa nao ganha evento.

Postgres real, mesma infraestrutura de `test_bulk_approve_por_posicao.py`:
so roda com `TEST_DATABASE_URL` apontando para o banco dedicado
`publicar_test`, nunca contra o banco do ambiente.
"""
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy import select

TEST_DB = os.environ.get("TEST_DATABASE_URL")
_precisa_db = pytest.mark.skipif(
    not TEST_DB, reason="TEST_DATABASE_URL nao definido: teste real so roda com Postgres dedicado"
)


async def _preparar_banco():
    """Cria o engine do banco dedicado, zera o schema e devolve
    `(engine, session_maker)`. Quem chama e' responsavel por
    `await engine.dispose()` num `finally`."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from tests._pg_dedicado import _engine_dedicado

    import app.models  # noqa: F401 — registra todas as tabelas
    from app.models.base import Base

    engine = _engine_dedicado()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    return engine, sm


async def _semear_user_e_seller(session_maker):
    from app.models.seller import Seller
    from app.models.user import User

    async with session_maker() as s:
        user = User(email=f"{uuid4()}@t.local", password_hash="x", full_name="t")
        seller = Seller(
            ml_user_id=int(str(uuid4().int)[:9]),
            ml_nickname="t",
            access_token_enc="x",
            refresh_token_enc="x",
            token_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        s.add_all([user, seller])
        await s.commit()
        return user.id, seller.id


async def _semear_listing(session_maker, seller_id, user_id, status, linhas):
    """Cria 1 listing (`seller_id`/`user_id`) no `status` dado com as
    ListingImage descritas em `linhas`: tuplas `(kind, sort_order, ml_picture_id)`.
    Todas nascem `approved=False` — quem decide o que fica aprovado e' o
    metodo sob teste, nao a semeadura. Devolve `(listing_id, [image_ids na
    ordem de `linhas`])`."""
    from app.models.listing import Listing
    from app.models.listing_image import ListingImage

    async with session_maker() as s:
        listing = Listing(
            seller_id=seller_id,
            created_by=user_id,
            sku_description="d",
            sku_brand="b",
            price=10,
            stock_quantity=1,
            condition="new",
            listing_type_id="gold_special",
            status=status,
            created_via="batch",
        )
        s.add(listing)
        await s.flush()
        rows = [
            ListingImage(
                listing_id=listing.id,
                ml_picture_id=ml_picture_id,
                status="uploaded" if ml_picture_id else "validation_failed",
                approved=False,
                sort_order=sort_order,
                kind=kind,
            )
            for (kind, sort_order, ml_picture_id) in linhas
        ]
        s.add_all(rows)
        await s.commit()
        return listing.id, [r.id for r in rows]


def _linhas_padrao(cover_kind: str = "cover_ai") -> list[tuple]:
    """As 5 posicoes oficiais (0..4, `cover_kind`/0) e 1 candidata (90), todas
    com `ml_picture_id` preenchido — o caso feliz, sem reprovacao de QA."""
    from app.models.listing_image import SPECS_AI_KIND

    return [
        (cover_kind, 0, "p0"),
        ("presentation_ai", 1, "p1"),
        ("benefits_ai", 2, "p2"),
        ("detail_ai", 3, "p3"),
        (SPECS_AI_KIND, 4, "p4"),
        (cover_kind, 90, "c90"),
    ]


async def _eventos(session_maker, listing_id):
    from app.models.listing_review_event import ListingReviewEvent

    async with session_maker() as s:
        rows = (
            await s.execute(
                select(ListingReviewEvent).where(ListingReviewEvent.listing_id == listing_id)
            )
        ).scalars().all()
    return rows


@_precisa_db
class TestEventoIndividual:
    @pytest.mark.asyncio
    async def test_individual_grava_um_evento_com_usuario_modo_contagem_e_tempo(self):
        from app.models.listing import Listing
        from app.services.listing_service import ListingService

        engine, sm = await _preparar_banco()
        try:
            user_id, seller_id = await _semear_user_e_seller(sm)
            listing_id, image_ids = await _semear_listing(
                sm, seller_id, user_id, "pending_image_approval", _linhas_padrao()
            )
            aprovaveis = image_ids[:5]  # so as 5 posicoes oficiais, sem a candidata 90

            async with sm() as s:
                listing = (
                    await s.execute(select(Listing).where(Listing.id == listing_id))
                ).scalar_one()
                svc = ListingService(s, seller_id)
                with patch("app.workers.tasks.ai_tasks.generate_description") as mock_task:
                    mock_task.delay = MagicMock()
                    await svc.approve_images(listing, aprovaveis, review_seconds=42, user_id=user_id)

            eventos = await _eventos(sm, listing_id)
            assert len(eventos) == 1, eventos
            evento = eventos[0]
            assert evento.user_id == user_id
            assert evento.action == "images_approved"
            assert evento.mode == "individual"
            assert evento.approved_count == 5
            assert evento.review_seconds == 42
            assert evento.created_at is not None
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_individual_sem_tempo_grava_review_seconds_nulo(self):
        from app.models.listing import Listing
        from app.services.listing_service import ListingService

        engine, sm = await _preparar_banco()
        try:
            user_id, seller_id = await _semear_user_e_seller(sm)
            listing_id, image_ids = await _semear_listing(
                sm, seller_id, user_id, "pending_image_approval", _linhas_padrao()
            )
            aprovaveis = image_ids[:5]

            async with sm() as s:
                listing = (
                    await s.execute(select(Listing).where(Listing.id == listing_id))
                ).scalar_one()
                svc = ListingService(s, seller_id)
                with patch("app.workers.tasks.ai_tasks.generate_description") as mock_task:
                    mock_task.delay = MagicMock()
                    await svc.approve_images(listing, aprovaveis, user_id=user_id)

            eventos = await _eventos(sm, listing_id)
            assert len(eventos) == 1, eventos
            assert eventos[0].review_seconds is None
            assert eventos[0].mode == "individual"
            assert eventos[0].approved_count == 5
        finally:
            await engine.dispose()


@_precisa_db
class TestEventoBulk:
    @pytest.mark.asyncio
    async def test_bulk_grava_um_evento_por_anuncio_aprovado_e_nenhum_para_invalido(self):
        from app.services.listing_service import ListingService

        engine, sm = await _preparar_banco()
        try:
            user_id, seller_id = await _semear_user_e_seller(sm)
            listing_a, _ = await _semear_listing(
                sm, seller_id, user_id, "pending_image_approval", _linhas_padrao()
            )
            listing_b, _ = await _semear_listing(
                sm, seller_id, user_id, "pending_image_approval", _linhas_padrao()
            )
            listing_c, _ = await _semear_listing(
                sm, seller_id, user_id, "ready_to_publish", _linhas_padrao()
            )

            async with sm() as s:
                svc = ListingService(s, seller_id)
                with patch("app.workers.tasks.ai_tasks.generate_description") as mock_task:
                    mock_task.delay = MagicMock()
                    result = await svc.bulk_approve_images(
                        [listing_a, listing_b, listing_c], user_id=user_id
                    )

            assert result.processed == 2, result.results
            assert result.failed == 1, result.results

            eventos_a = await _eventos(sm, listing_a)
            eventos_b = await _eventos(sm, listing_b)
            eventos_c = await _eventos(sm, listing_c)
            assert len(eventos_a) == 1, eventos_a
            assert len(eventos_b) == 1, eventos_b
            assert len(eventos_c) == 0, eventos_c
            for ev in (eventos_a[0], eventos_b[0]):
                assert ev.mode == "bulk", ev.mode
                assert ev.review_seconds is None, ev.review_seconds
                assert ev.approved_count == 5, ev.approved_count
                assert ev.user_id == user_id
                assert ev.action == "images_approved"
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_bulk_approved_count_conta_so_o_que_foi_aprovado(self):
        """`cover_ai`/0 reprovada no QA (`ml_picture_id=None`) fica de fora;
        o fallback `cover_deterministic`/0 mais as posicoes 1-4 somam 5 —
        a candidata 90 fica de fora por posicao."""
        from app.services.listing_service import ListingService

        engine, sm = await _preparar_banco()
        try:
            user_id, seller_id = await _semear_user_e_seller(sm)
            linhas = [
                ("cover_ai", 0, None),
                ("cover_deterministic", 0, "pcd"),
                ("presentation_ai", 1, "p1"),
                ("benefits_ai", 2, "p2"),
                ("detail_ai", 3, "p3"),
                ("specs_ai", 4, "p4"),
                ("cover_ai", 90, "c90"),
            ]
            listing_id, _ = await _semear_listing(
                sm, seller_id, user_id, "pending_image_approval", linhas
            )

            async with sm() as s:
                svc = ListingService(s, seller_id)
                with patch("app.workers.tasks.ai_tasks.generate_description") as mock_task:
                    mock_task.delay = MagicMock()
                    result = await svc.bulk_approve_images([listing_id], user_id=user_id)

            assert result.processed == 1, result.results

            eventos = await _eventos(sm, listing_id)
            assert len(eventos) == 1, eventos
            assert eventos[0].approved_count == 5, eventos[0].approved_count
            assert eventos[0].mode == "bulk"
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_bulk_falha_na_aprovacao_nao_deixa_evento(self):
        """Duas linhas aprovaveis em `sort_order=0` (`cover_ai` e
        `cover_deterministic`, ambas com `ml_picture_id`) estouram o indice
        unico `uq_listing_images_cover_slot` no UPDATE em massa. A falha tem
        que deixar ZERO eventos e nenhuma imagem aprovada — prova a
        atomicidade evento+aprovacao."""
        from app.models.listing_image import ListingImage
        from app.services.listing_service import ListingService

        engine, sm = await _preparar_banco()
        try:
            user_id, seller_id = await _semear_user_e_seller(sm)
            linhas = [
                ("cover_ai", 0, "p0"),
                ("cover_deterministic", 0, "pcd"),
                ("presentation_ai", 1, "p1"),
                ("benefits_ai", 2, "p2"),
                ("detail_ai", 3, "p3"),
                ("specs_ai", 4, "p4"),
            ]
            listing_id, _ = await _semear_listing(
                sm, seller_id, user_id, "pending_image_approval", linhas
            )

            async with sm() as s:
                svc = ListingService(s, seller_id)
                with patch("app.workers.tasks.ai_tasks.generate_description") as mock_task:
                    mock_task.delay = MagicMock()
                    result = await svc.bulk_approve_images([listing_id], user_id=user_id)

            assert result.failed == 1, result.results
            assert result.processed == 0, result.results

            eventos = await _eventos(sm, listing_id)
            assert eventos == [], eventos

            async with sm() as s:
                rows = (
                    await s.execute(
                        select(ListingImage).where(ListingImage.listing_id == listing_id)
                    )
                ).scalars().all()
            assert all(r.approved is False for r in rows), [
                (r.kind, r.sort_order, r.approved) for r in rows
            ]
        finally:
            await engine.dispose()
