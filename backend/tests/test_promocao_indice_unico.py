"""Indice unico parcial fecha a corrida de promote_cover / promote_specs.

LIMITACAO CONHECIDA que existia: `with_for_update()` so serializa promocoes
que disputem AS MESMAS linhas. Duas promocoes simultaneas de ALVOS
DIFERENTES no mesmo anuncio travavam linhas disjuntas, nenhuma enxergava o
alvo da outra, e as duas terminavam aprovadas na mesma posicao — a capa
publicada virava sorteio.

Fechamento: dois indices unicos parciais em `listing_images`
(migration 3d8f1b2c9e47, tambem declarados no model):

    uq_listing_images_cover_slot  UNIQUE (listing_id)
        WHERE approved AND sort_order = 0 AND kind IN (cover kinds)
    uq_listing_images_specs_slot  UNIQUE (listing_id)
        WHERE approved AND sort_order < 90 AND kind IN (specs kinds)

`approved` faz parte do predicado de proposito: o pipeline grava `cover_ai`
em sort_order 0 com approved=False, e uma regeracao pode deixar duas linhas
assim — o que nao pode existir e' mais de UMA APROVADA no slot. A segunda
transacao da corrida falha no commit com IntegrityError, e o service devolve
409 legivel em vez de 500.

O teste de corrida de verdade (classe `TestCorridaReal`) roda contra um
Postgres real e so quando `TEST_DATABASE_URL` esta definido — nunca contra o
banco do ambiente (a suite roda dentro da imagem de producao).
"""
import asyncio
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError


def _integrity_error():
    return IntegrityError("INSERT", {}, Exception('duplicate key value violates unique constraint "uq_listing_images_cover_slot"'))


class TestViolacaoViraConflito409:
    @pytest.mark.asyncio
    async def test_promote_cover_traduz_integrity_error_em_409(self):
        from app.models.listing_image import COVER_AI_KIND
        from app.services.cover_variant_service import promote_cover

        alvo = MagicMock(); alvo.id = uuid4(); alvo.kind = COVER_AI_KIND; alvo.sort_order = 90; alvo.approved = False
        db = AsyncMock()
        r_alvo = MagicMock(); r_alvo.scalar_one_or_none = MagicMock(return_value=alvo)
        r_outros = MagicMock(); r_outros.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
        db.execute = AsyncMock(side_effect=[r_alvo, r_outros])
        db.commit = AsyncMock(side_effect=_integrity_error())
        db.rollback = AsyncMock()
        listing = MagicMock(); listing.id = uuid4()

        with pytest.raises(HTTPException) as exc:
            await promote_cover(db, listing, alvo.id)

        assert exc.value.status_code == 409
        assert "outra promoção" in exc.value.detail.lower()
        db.rollback.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_promote_specs_traduz_integrity_error_em_409(self):
        from app.models.listing_image import SPECS_AI_KIND
        from app.services.specs_variant_service import promote_specs

        alvo = MagicMock(); alvo.id = uuid4(); alvo.kind = SPECS_AI_KIND; alvo.sort_order = 91; alvo.approved = False
        db = AsyncMock()
        r_alvo = MagicMock(); r_alvo.scalar_one_or_none = MagicMock(return_value=alvo)
        r_ocup = MagicMock(); r_ocup.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
        r_max = MagicMock(); r_max.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=[3])))
        db.execute = AsyncMock(side_effect=[r_alvo, r_ocup, r_max])
        db.commit = AsyncMock(side_effect=_integrity_error())
        db.rollback = AsyncMock()
        listing = MagicMock(); listing.id = uuid4()

        with pytest.raises(HTTPException) as exc:
            await promote_specs(db, listing, alvo.id)

        assert exc.value.status_code == 409
        db.rollback.assert_awaited_once()


class TestIndicesDeclarados:
    def test_model_declara_os_dois_indices_parciais(self):
        from app.models.listing_image import ListingImage

        nomes = {ix.name: ix for ix in ListingImage.__table__.indexes}
        for nome in ("uq_listing_images_cover_slot", "uq_listing_images_specs_slot"):
            assert nome in nomes, nome
            assert nomes[nome].unique
            assert "approved" in str(nomes[nome].dialect_options["postgresql"]["where"])


# ---------------------------------------------------------------------------
# Corrida de verdade: Postgres real, indice real, duas transacoes.
# ---------------------------------------------------------------------------
TEST_DB = os.environ.get("TEST_DATABASE_URL")
_precisa_db = pytest.mark.skipif(not TEST_DB, reason="TEST_DATABASE_URL nao definido: corrida real so roda com Postgres dedicado")


async def _semear(session_maker):
    """1 user, 1 seller, 1 listing, 2 candidatos de capa e 2 de ficha (todos approved=False)."""
    from app.models.listing import Listing
    from app.models.listing_image import COVER_AI_KIND, ListingImage, SPECS_AI_KIND
    from app.models.seller import Seller
    from app.models.user import User

    async with session_maker() as s:
        user = User(email=f"{uuid4()}@t.local", password_hash="x", full_name="t")
        seller = Seller(ml_user_id=int(str(uuid4().int)[:9]), ml_nickname="t", access_token_enc="x",
                        refresh_token_enc="x", token_expires_at=datetime.now(timezone.utc) + timedelta(hours=1))
        s.add_all([user, seller]); await s.flush()
        listing = Listing(seller_id=seller.id, created_by=user.id, sku_description="d", sku_brand="b",
                          price=10, stock_quantity=1, condition="new", listing_type_id="gold_special",
                          status="pending_image_approval", created_via="manual")
        s.add(listing); await s.flush()
        capas = [ListingImage(listing_id=listing.id, ml_picture_id=f"c{i}", status="uploaded",
                              approved=False, sort_order=90, kind=COVER_AI_KIND) for i in range(2)]
        fichas = [ListingImage(listing_id=listing.id, ml_picture_id=f"s{i}", status="uploaded",
                               approved=False, sort_order=91, kind=SPECS_AI_KIND) for i in range(2)]
        s.add_all(capas + fichas); await s.commit()
        return listing.id, [c.id for c in capas], [f.id for f in fichas]


async def _corrida(session_maker, listing_id, promover, alvos):
    """Duas transacoes que so commitam depois que AMBAS leram o estado —
    a barreira reproduz o entrelacamento que o with_for_update nao cobre."""
    from app.models.listing import Listing
    from sqlalchemy import select

    barreira = asyncio.Barrier(2)
    resultados = []

    async def uma(alvo):
        async with session_maker() as s:
            listing = (await s.execute(select(Listing).where(Listing.id == listing_id))).scalar_one()
            commit_real = s.commit

            async def commit_sincronizado():
                await barreira.wait()
                await commit_real()

            s.commit = commit_sincronizado
            try:
                await promover(s, listing, alvo)
                resultados.append("ok")
            except HTTPException as e:
                resultados.append(e.status_code)

    await asyncio.gather(uma(alvos[0]), uma(alvos[1]))
    return resultados


@_precisa_db
class TestCorridaReal:
    @pytest.mark.asyncio
    async def test_duas_promocoes_de_capa_concorrentes_uma_vence_outra_409(self):
        from sqlalchemy import func, select
        from sqlalchemy.ext.asyncio import async_sessionmaker

        from tests._pg_dedicado import _engine_dedicado

        import app.models  # noqa: F401 — registra todas as tabelas
        from app.models.base import Base
        from app.models.listing_image import ListingImage, PROMOTABLE_COVER_KINDS
        from app.services.cover_variant_service import promote_cover

        engine = _engine_dedicado(TEST_DB)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
        sm = async_sessionmaker(engine, expire_on_commit=False)
        try:
            listing_id, capas, _ = await _semear(sm)
            resultados = await _corrida(sm, listing_id, promote_cover, capas)

            assert sorted(map(str, resultados)) == ["409", "ok"], resultados
            async with sm() as s:
                n = (await s.execute(select(func.count()).select_from(ListingImage).where(
                    ListingImage.listing_id == listing_id, ListingImage.approved.is_(True),
                    ListingImage.sort_order == 0, ListingImage.kind.in_(PROMOTABLE_COVER_KINDS)))).scalar_one()
            assert n == 1, "exatamente UMA capa aprovada em sort_order=0"
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_duas_promocoes_de_ficha_concorrentes_uma_vence_outra_409(self):
        from sqlalchemy import func, select
        from sqlalchemy.ext.asyncio import async_sessionmaker

        from tests._pg_dedicado import _engine_dedicado

        import app.models  # noqa: F401
        from app.models.base import Base
        from app.models.listing_image import CANDIDATE_SORT_ORDER_FLOOR, ListingImage, PROMOTABLE_SPECS_KINDS
        from app.services.specs_variant_service import promote_specs

        engine = _engine_dedicado(TEST_DB)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
        sm = async_sessionmaker(engine, expire_on_commit=False)
        try:
            listing_id, _, fichas = await _semear(sm)
            resultados = await _corrida(sm, listing_id, promote_specs, fichas)

            assert sorted(map(str, resultados)) == ["409", "ok"], resultados
            async with sm() as s:
                n = (await s.execute(select(func.count()).select_from(ListingImage).where(
                    ListingImage.listing_id == listing_id, ListingImage.approved.is_(True),
                    ListingImage.sort_order < CANDIDATE_SORT_ORDER_FLOOR,
                    ListingImage.kind.in_(PROMOTABLE_SPECS_KINDS)))).scalar_one()
            assert n == 1, "exatamente UMA ficha aprovada na galeria"
        finally:
            await engine.dispose()
