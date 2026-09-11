"""`bulk_approve_images` aprova as 5 posicoes oficiais (0..4) e exclui SOMENTE
as candidatas de IA sob demanda (sort_order 90/91) — a exclusao e' por
POSICAO, nunca por `kind`, porque `cover_ai`/`specs_ai` sao tambem os kinds
das posicoes oficiais 0 (capa) e 4 (ficha).

Postgres real, mesma infraestrutura de `test_promocao_indice_unico.py`: so
roda com `TEST_DATABASE_URL` apontando para o banco dedicado `publicar_test`,
nunca contra o banco do ambiente.
"""
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

TEST_DB = os.environ.get("TEST_DATABASE_URL")
_precisa_db = pytest.mark.skipif(
    not TEST_DB, reason="TEST_DATABASE_URL nao definido: teste real so roda com Postgres dedicado"
)


async def _semear(session_maker, linhas):
    """1 user, 1 seller, 1 listing em pending_image_approval (created_via
    batch) e as ListingImage descritas em `linhas`: cada item e' uma tupla
    `(kind, sort_order, ml_picture_id, status)`. Todas nascem
    `approved=False` — quem decide o que fica aprovado e' `bulk_approve_images`,
    nao a semeadura."""
    from app.models.listing import Listing
    from app.models.listing_image import ListingImage
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
        await s.flush()
        listing = Listing(
            seller_id=seller.id,
            created_by=user.id,
            sku_description="d",
            sku_brand="b",
            price=10,
            stock_quantity=1,
            condition="new",
            listing_type_id="gold_special",
            status="pending_image_approval",
            created_via="batch",
        )
        s.add(listing)
        await s.flush()

        rows = [
            ListingImage(listing_id=listing.id, ml_picture_id=ml_picture_id, status=status,
                         approved=False, sort_order=sort_order, kind=kind)
            for (kind, sort_order, ml_picture_id, status) in linhas
        ]
        s.add_all(rows)
        await s.commit()
        return listing.id, seller.id


def _linhas_padrao(cover_kind: str) -> list[tuple]:
    """As 5 posicoes oficiais (0..4, `cover_kind`/0) e 2 candidatas
    (cover_ai/90, specs_ai/91), todas com `ml_picture_id` preenchido e
    `status="uploaded"` — o caso feliz, sem reprovacao de QA."""
    from app.models.listing_image import COVER_AI_KIND, SPECS_AI_KIND

    return [
        (cover_kind, 0, "p0", "uploaded"),
        ("presentation", 1, "p1", "uploaded"),
        ("benefits_ai", 2, "p2", "uploaded"),
        ("detail_ai", 3, "p3", "uploaded"),
        (SPECS_AI_KIND, 4, "p4", "uploaded"),
        (COVER_AI_KIND, 90, "c90", "uploaded"),
        (SPECS_AI_KIND, 91, "c91", "uploaded"),
    ]


async def _rodar_e_verificar(cover_kind: str):
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from tests._pg_dedicado import _engine_dedicado

    import app.models  # noqa: F401 — registra todas as tabelas
    from app.models.base import Base
    from app.models.listing import Listing
    from app.models.listing_image import ListingImage
    from app.services.listing_service import ListingService

    engine = _engine_dedicado(TEST_DB)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    try:
        listing_id, seller_id = await _semear(sm, _linhas_padrao(cover_kind))

        async with sm() as s:
            svc = ListingService(s, seller_id)
            with patch("app.workers.tasks.ai_tasks.generate_description") as mock_task:
                mock_task.delay = MagicMock()
                result = await svc.bulk_approve_images([listing_id])

            assert result.processed == 1, result

            s.expire_all()
            rows = (
                await s.execute(
                    select(ListingImage)
                    .where(ListingImage.listing_id == listing_id)
                    .order_by(ListingImage.sort_order)
                )
            ).scalars().all()
            assert len(rows) == 7, [(r.sort_order, r.approved) for r in rows]

            por_posicao = {r.sort_order: r.approved for r in rows}
            for pos in range(5):
                assert por_posicao[pos] is True, f"posicao {pos} deveria estar aprovada: {por_posicao}"
            for pos in (90, 91):
                assert por_posicao[pos] is False, f"candidata {pos} NAO deveria estar aprovada: {por_posicao}"

            s.expire_all()
            listing = (await s.execute(select(Listing).where(Listing.id == listing_id))).scalar_one()
            assert listing.status == "generating_description", listing.status

            mock_task.delay.assert_called_once_with(str(listing_id))
    finally:
        await engine.dispose()


@_precisa_db
class TestBulkApproveImagesPorPosicao:
    @pytest.mark.asyncio
    async def test_aprova_as_5_posicoes_e_exclui_as_2_candidatas(self):
        from app.models.listing_image import COVER_AI_KIND

        await _rodar_e_verificar(cover_kind=COVER_AI_KIND)

    @pytest.mark.asyncio
    async def test_capa_deterministica_na_posicao_0_tambem_e_aprovada(self):
        """Fallback da capa: `cover_deterministic`/0 no lugar de `cover_ai`/0.
        A posicao 0 e' aprovada de qualquer forma — o filtro e' por
        `sort_order`, nao por `kind`."""
        from app.models.listing_image import COVER_DETERMINISTIC_KIND

        await _rodar_e_verificar(cover_kind=COVER_DETERMINISTIC_KIND)


@_precisa_db
class TestBulkApproveImagesRespeitaMlPictureId:
    """`bulk_approve_images` usa o MESMO criterio da publicacao
    (`publish_service` so manda `approved and ml_picture_id`): aprovar uma
    linha que nunca subiu ao ML e' aprovar um item que nunca sera' enviado —
    e no caso da capa, colide com o fallback no mesmo slot."""

    @pytest.mark.asyncio
    async def test_capa_reprovada_no_qa_mais_fallback_so_aprova_o_fallback(self):
        """Reproduz o que o worker grava de verdade quando a capa por IA
        reprova no QA de fundo branco (`image_tasks.py:338-342`): a linha
        `cover_ai`/0 fica com `ml_picture_id=None` e
        `status="validation_failed"`; o fallback `cover_deterministic`/0 e'
        quem tem `ml_picture_id`. Aprovar as duas linhas de sort_order 0
        estoura o indice unico `uq_listing_images_cover_slot` — so a linha
        com `ml_picture_id` pode ser aprovada."""
        from sqlalchemy import select
        from sqlalchemy.ext.asyncio import async_sessionmaker

        from tests._pg_dedicado import _engine_dedicado

        import app.models  # noqa: F401 — registra todas as tabelas
        from app.models.base import Base
        from app.models.listing import Listing
        from app.models.listing_image import (
            COVER_AI_KIND,
            COVER_DETERMINISTIC_KIND,
            ListingImage,
            SPECS_AI_KIND,
        )
        from app.services.listing_service import ListingService

        linhas = [
            (COVER_AI_KIND, 0, None, "validation_failed"),
            (COVER_DETERMINISTIC_KIND, 0, "pcd", "uploaded"),
            ("presentation", 1, "p1", "uploaded"),
            ("benefits_ai", 2, "p2", "uploaded"),
            ("detail_ai", 3, "p3", "uploaded"),
            (SPECS_AI_KIND, 4, "p4", "uploaded"),
            (COVER_AI_KIND, 90, "c90", "uploaded"),
            (SPECS_AI_KIND, 91, "c91", "uploaded"),
        ]

        engine = _engine_dedicado(TEST_DB)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
        sm = async_sessionmaker(engine, expire_on_commit=False)
        try:
            listing_id, seller_id = await _semear(sm, linhas)

            async with sm() as s:
                svc = ListingService(s, seller_id)
                with patch("app.workers.tasks.ai_tasks.generate_description") as mock_task:
                    mock_task.delay = MagicMock()
                    result = await svc.bulk_approve_images([listing_id])

                assert result.processed == 1, result.results

                s.expire_all()
                rows = (
                    await s.execute(
                        select(ListingImage)
                        .where(ListingImage.listing_id == listing_id)
                        .order_by(ListingImage.sort_order, ListingImage.kind)
                    )
                ).scalars().all()

                aprovadas_pos0 = [r for r in rows if r.sort_order == 0 and r.approved]
                assert len(aprovadas_pos0) == 1, [(r.kind, r.approved) for r in rows if r.sort_order == 0]
                assert aprovadas_pos0[0].kind == COVER_DETERMINISTIC_KIND

                reprovada = next(r for r in rows if r.sort_order == 0 and r.kind == COVER_AI_KIND)
                assert reprovada.approved is False, reprovada.approved

                for pos in (1, 2, 3, 4):
                    row = next(r for r in rows if r.sort_order == pos)
                    assert row.approved is True, f"posicao {pos} deveria estar aprovada"
                for pos in (90, 91):
                    row = next(r for r in rows if r.sort_order == pos)
                    assert row.approved is False, f"candidata {pos} NAO deveria estar aprovada"

                s.expire_all()
                listing = (await s.execute(select(Listing).where(Listing.id == listing_id))).scalar_one()
                assert listing.status == "generating_description", listing.status

                mock_task.delay.assert_called_once_with(str(listing_id))
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_posicao_do_meio_reprovada_no_qa_nao_e_aprovada(self):
        """`benefits_ai`/2 reprovada no QA (`ml_picture_id=None`,
        `status="validation_failed"`) continua `approved=False` depois da
        aprovacao em massa; as outras 4 posicoes oficiais aprovam
        normalmente e as candidatas seguem de fora."""
        from sqlalchemy import select
        from sqlalchemy.ext.asyncio import async_sessionmaker

        from tests._pg_dedicado import _engine_dedicado

        import app.models  # noqa: F401 — registra todas as tabelas
        from app.models.base import Base
        from app.models.listing import Listing
        from app.models.listing_image import COVER_AI_KIND, ListingImage, SPECS_AI_KIND
        from app.services.listing_service import ListingService

        linhas = [
            (COVER_AI_KIND, 0, "p0", "uploaded"),
            ("presentation", 1, "p1", "uploaded"),
            ("benefits_ai", 2, None, "validation_failed"),
            ("detail_ai", 3, "p3", "uploaded"),
            (SPECS_AI_KIND, 4, "p4", "uploaded"),
            (COVER_AI_KIND, 90, "c90", "uploaded"),
            (SPECS_AI_KIND, 91, "c91", "uploaded"),
        ]

        engine = _engine_dedicado(TEST_DB)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
        sm = async_sessionmaker(engine, expire_on_commit=False)
        try:
            listing_id, seller_id = await _semear(sm, linhas)

            async with sm() as s:
                svc = ListingService(s, seller_id)
                with patch("app.workers.tasks.ai_tasks.generate_description") as mock_task:
                    mock_task.delay = MagicMock()
                    result = await svc.bulk_approve_images([listing_id])

                assert result.processed == 1, result.results

                s.expire_all()
                rows = (
                    await s.execute(
                        select(ListingImage)
                        .where(ListingImage.listing_id == listing_id)
                        .order_by(ListingImage.sort_order)
                    )
                ).scalars().all()
                por_posicao = {r.sort_order: r.approved for r in rows}

                assert por_posicao[2] is False, f"benefits_ai reprovada nao deveria estar aprovada: {por_posicao}"
                for pos in (0, 1, 3, 4):
                    assert por_posicao[pos] is True, f"posicao {pos} deveria estar aprovada: {por_posicao}"
                for pos in (90, 91):
                    assert por_posicao[pos] is False, f"candidata {pos} NAO deveria estar aprovada: {por_posicao}"

                s.expire_all()
                listing = (await s.execute(select(Listing).where(Listing.id == listing_id))).scalar_one()
                assert listing.status == "generating_description", listing.status

                mock_task.delay.assert_called_once_with(str(listing_id))
        finally:
            await engine.dispose()
