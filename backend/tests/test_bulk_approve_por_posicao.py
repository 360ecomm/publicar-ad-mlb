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


async def _semear(session_maker, cover_kind: str):
    """1 user, 1 seller, 1 listing em pending_image_approval (created_via
    batch) e 7 ListingImage: as 5 posicoes oficiais (0..4, `cover_kind`/0) e
    2 candidatas (cover_ai/90, specs_ai/91). Todas approved=False."""
    from app.models.listing import Listing
    from app.models.listing_image import COVER_AI_KIND, ListingImage, SPECS_AI_KIND
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

        posicoes = [
            ListingImage(listing_id=listing.id, ml_picture_id="p0", status="uploaded",
                         approved=False, sort_order=0, kind=cover_kind),
            ListingImage(listing_id=listing.id, ml_picture_id="p1", status="uploaded",
                         approved=False, sort_order=1, kind="presentation"),
            ListingImage(listing_id=listing.id, ml_picture_id="p2", status="uploaded",
                         approved=False, sort_order=2, kind="benefits_ai"),
            ListingImage(listing_id=listing.id, ml_picture_id="p3", status="uploaded",
                         approved=False, sort_order=3, kind="detail_ai"),
            ListingImage(listing_id=listing.id, ml_picture_id="p4", status="uploaded",
                         approved=False, sort_order=4, kind=SPECS_AI_KIND),
        ]
        candidatas = [
            ListingImage(listing_id=listing.id, ml_picture_id="c90", status="uploaded",
                         approved=False, sort_order=90, kind=COVER_AI_KIND),
            ListingImage(listing_id=listing.id, ml_picture_id="c91", status="uploaded",
                         approved=False, sort_order=91, kind=SPECS_AI_KIND),
        ]
        s.add_all(posicoes + candidatas)
        await s.commit()
        return listing.id, seller.id, [img.id for img in posicoes + candidatas]


async def _rodar_e_verificar(cover_kind: str):
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    import app.models  # noqa: F401 — registra todas as tabelas
    from app.models.base import Base
    from app.models.listing import Listing
    from app.models.listing_image import ListingImage
    from app.services.listing_service import ListingService

    engine = create_async_engine(TEST_DB)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    try:
        listing_id, seller_id, _ = await _semear(sm, cover_kind)

        async with sm() as s:
            svc = ListingService(s, seller_id)
            with patch("app.workers.tasks.ai_tasks.generate_description") as mock_task:
                mock_task.delay = MagicMock()
                result = await svc.bulk_approve_images([listing_id])

            assert result.processed == 1, result

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
