"""Desconectar um seller: apaga SO os tokens, marca `is_active = False` e
mantem TODO o historico (listings, products, listing_images,
listing_review_events, user_seller_access). Reconectar pelo OAuth devolve a
conta a ativa com token novo. Postgres REAL (`publicar_test`).

Os endpoints sao exercitados pelo app de verdade (ASGITransport) com
`get_db` e `get_current_user` sobrescritos; nada de rede (conftest bloqueia).
"""
import os
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

TEST_DB = os.environ.get("TEST_DATABASE_URL")
_precisa_db = pytest.mark.skipif(
    not TEST_DB, reason="TEST_DATABASE_URL nao definido: teste real so roda com Postgres dedicado"
)


async def _semear_conta_com_historico(sm):
    """1 user admin, 1 seller ATIVO com acesso, 2 listings, 1 product,
    1 listing_image e 1 listing_review_event. Devolve (user_id, seller_id).
    Le os models para os campos obrigatorios: `tests/test_listagem_em_escala._semear`
    mostra como criar User/Seller/Listing validos."""
    from app.core.security import encrypt_value
    from app.models.listing import Listing
    from app.models.listing_image import ListingImage
    from app.models.listing_review_event import ListingReviewEvent
    from app.models.product import Product
    from app.models.seller import Seller
    from app.models.user import User
    from app.models.user_seller_access import UserSellerAccess

    async with sm() as s:
        user = User(email=f"{uuid4()}@t.local", password_hash="x", full_name="t", role="admin")
        s.add(user)
        await s.flush()
        seller = Seller(
            ml_user_id=int(str(uuid4().int)[:9]), ml_nickname="LOJA-T",
            access_token_enc=encrypt_value("acesso-antigo"),
            refresh_token_enc=encrypt_value("refresh-antigo"),
            token_expires_at=datetime.now(timezone.utc), is_active=True,
        )
        s.add(seller)
        await s.flush()
        s.add(UserSellerAccess(user_id=user.id, seller_id=seller.id, role="admin"))
        # Listing/Product/ListingImage/ListingReviewEvent: preencher os campos
        # obrigatorios conforme os models (ver `_semear` citado acima).
        # `stock_quantity` e `condition` sao NOT NULL sem default no model
        # (Listing) e nao estavam no seed original do brief.
        l1 = Listing(seller_id=seller.id, created_by=user.id, sku_external_id="T1", sku_description="d",
                     sku_brand="b", price=10, stock_quantity=1, condition="new", status="draft")
        l2 = Listing(seller_id=seller.id, created_by=user.id, sku_external_id="T2", sku_description="d",
                     sku_brand="b", price=10, stock_quantity=1, condition="new", status="published",
                     mlb_id=f"MLB{uuid4().int % 10**9}")
        s.add_all([l1, l2])
        await s.flush()
        s.add(Product(seller_id=seller.id, sku="T1", description="d"))
        s.add(ListingImage(listing_id=l1.id, sort_order=0, kind="cover_ai", status="uploaded", approved=False))
        s.add(ListingReviewEvent(listing_id=l1.id, user_id=user.id, action="approve", mode="individual",
                                 approved_count=0, review_seconds=None))
        await s.commit()
        return user.id, seller.id


async def _contagens(sm, seller_id):
    from sqlalchemy import func, select

    from app.models.listing import Listing
    from app.models.listing_image import ListingImage
    from app.models.listing_review_event import ListingReviewEvent
    from app.models.product import Product
    from app.models.user_seller_access import UserSellerAccess

    async with sm() as s:
        listings = (await s.execute(select(func.count()).select_from(Listing).where(Listing.seller_id == seller_id))).scalar_one()
        products = (await s.execute(select(func.count()).select_from(Product).where(Product.seller_id == seller_id))).scalar_one()
        images = (await s.execute(
            select(func.count()).select_from(ListingImage).join(Listing, Listing.id == ListingImage.listing_id)
            .where(Listing.seller_id == seller_id))).scalar_one()
        events = (await s.execute(
            select(func.count()).select_from(ListingReviewEvent).join(Listing, Listing.id == ListingReviewEvent.listing_id)
            .where(Listing.seller_id == seller_id))).scalar_one()
        acessos = (await s.execute(select(func.count()).select_from(UserSellerAccess).where(UserSellerAccess.seller_id == seller_id))).scalar_one()
    return {"listings": listings, "products": products, "images": images, "events": events, "acessos": acessos}


def _client_como(app, sm, user_id):
    from httpx import ASGITransport, AsyncClient

    from app.core.dependencies import get_current_user, get_db

    async def _override_get_db():
        async with sm() as session:
            yield session

    async def _override_user():
        return SimpleNamespace(id=user_id, role="admin", is_active=True)

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_current_user] = _override_user
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def test_rota_de_desconexao_existe():
    """Sem banco: a rota e' POST /api/v1/sellers/{seller_id}/disconnect."""
    from app.main import app

    rotas = {(r.path, tuple(sorted(r.methods))) for r in app.routes if getattr(r, "methods", None)}
    assert ("/api/v1/sellers/{seller_id}/disconnect", ("POST",)) in rotas


@_precisa_db
class TestDesconectar:
    @pytest.mark.asyncio
    async def test_apaga_tokens_marca_inativo_e_preserva_o_historico(self):
        from sqlalchemy import select

        from app.main import app
        from app.models.seller import Seller
        from tests.test_listagem_em_escala import _preparar_banco

        engine, sm = await _preparar_banco()
        try:
            user_id, seller_id = await _semear_conta_com_historico(sm)
            antes = await _contagens(sm, seller_id)
            assert antes == {"listings": 2, "products": 1, "images": 1, "events": 1, "acessos": 1}

            async with _client_como(app, sm, user_id) as client:
                resp = await client.post(f"/api/v1/sellers/{seller_id}/disconnect")
            assert resp.status_code == 200, resp.text
            corpo = resp.json()
            assert corpo["id"] == str(seller_id)
            assert corpo["is_active"] is False
            assert corpo["ml_nickname"] == "LOJA-T"

            async with sm() as s:
                seller = (await s.execute(select(Seller).where(Seller.id == seller_id))).scalar_one()
            assert seller.access_token_enc is None
            assert seller.refresh_token_enc is None
            assert seller.is_active is False
            assert await _contagens(sm, seller_id) == antes
        finally:
            app.dependency_overrides.clear()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_segunda_chamada_e_idempotente(self):
        from app.main import app
        from tests.test_listagem_em_escala import _preparar_banco

        engine, sm = await _preparar_banco()
        try:
            user_id, seller_id = await _semear_conta_com_historico(sm)
            async with _client_como(app, sm, user_id) as client:
                r1 = await client.post(f"/api/v1/sellers/{seller_id}/disconnect")
                r2 = await client.post(f"/api/v1/sellers/{seller_id}/disconnect")
            assert (r1.status_code, r2.status_code) == (200, 200)
            assert r2.json()["is_active"] is False
        finally:
            app.dependency_overrides.clear()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_sem_acesso_ao_seller_devolve_404_e_nao_mexe(self):
        from sqlalchemy import select

        from app.main import app
        from app.models.seller import Seller
        from tests.test_listagem_em_escala import _preparar_banco

        engine, sm = await _preparar_banco()
        try:
            _, seller_id = await _semear_conta_com_historico(sm)
            outro_user, _ = await _semear_conta_com_historico(sm)  # user sem acesso ao 1o seller
            async with _client_como(app, sm, outro_user) as client:
                resp = await client.post(f"/api/v1/sellers/{seller_id}/disconnect")
            assert resp.status_code == 404
            assert resp.json() == {"detail": "Conta não encontrada ou sem acesso."}
            async with sm() as s:
                seller = (await s.execute(select(Seller).where(Seller.id == seller_id))).scalar_one()
            assert seller.is_active is True and seller.access_token_enc is not None
        finally:
            app.dependency_overrides.clear()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_desconectada_continua_na_lista_e_no_dashboard_com_is_active_false(self):
        from app.main import app
        from tests.test_listagem_em_escala import _preparar_banco

        engine, sm = await _preparar_banco()
        try:
            user_id, seller_id = await _semear_conta_com_historico(sm)
            async with _client_como(app, sm, user_id) as client:
                await client.post(f"/api/v1/sellers/{seller_id}/disconnect")
                lista = (await client.get("/api/v1/sellers")).json()
                dash = (await client.get("/api/v1/dashboard")).json()
            assert [s["is_active"] for s in lista if s["id"] == str(seller_id)] == [False]
            entrada = [e for e in dash["sellers"] if e["seller_id"] == str(seller_id)]
            assert len(entrada) == 1
            assert entrada[0]["is_active"] is False
            assert entrada[0]["total_listings"] == 2
        finally:
            app.dependency_overrides.clear()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_desconectada_e_recusada_como_x_seller_id(self):
        """`get_active_seller` ja filtra `is_active`; a desconexao tem que
        cair nessa recusa (403), e' o que faz os anuncios pararem."""
        from app.main import app
        from tests.test_listagem_em_escala import _preparar_banco

        engine, sm = await _preparar_banco()
        try:
            user_id, seller_id = await _semear_conta_com_historico(sm)
            async with _client_como(app, sm, user_id) as client:
                await client.post(f"/api/v1/sellers/{seller_id}/disconnect")
                resp = await client.get("/api/v1/listings", headers={"X-Seller-ID": str(seller_id)})
            assert resp.status_code == 403
        finally:
            app.dependency_overrides.clear()
            await engine.dispose()


@_precisa_db
class TestReconectar:
    @pytest.mark.asyncio
    async def test_callback_do_oauth_reativa_com_token_novo(self):
        from unittest.mock import AsyncMock, patch

        from sqlalchemy import select

        from app.core.security import decrypt_value
        from app.main import app
        from app.models.seller import Seller
        from app.services.ml_oauth_service import MLOAuthService
        from tests.test_listagem_em_escala import _preparar_banco

        engine, sm = await _preparar_banco()
        try:
            user_id, seller_id = await _semear_conta_com_historico(sm)
            async with _client_como(app, sm, user_id) as client:
                await client.post(f"/api/v1/sellers/{seller_id}/disconnect")
            async with sm() as s:
                ml_user_id = (await s.execute(select(Seller.ml_user_id).where(Seller.id == seller_id))).scalar_one()

            token_data = {"access_token": "acesso-novo", "refresh_token": "refresh-novo", "expires_in": 21600}
            with patch("app.services.ml_oauth_service._pop_state", new=AsyncMock(return_value=str(user_id))), \
                 patch.object(MLOAuthService, "_exchange_code", new=AsyncMock(return_value=token_data)), \
                 patch.object(MLOAuthService, "_get_ml_user", new=AsyncMock(return_value={"id": ml_user_id, "nickname": "LOJA-T"})):
                async with sm() as s:
                    await MLOAuthService().handle_callback("code", "state", s)

            async with sm() as s:
                seller = (await s.execute(select(Seller).where(Seller.id == seller_id))).scalar_one()
            assert seller.is_active is True
            assert decrypt_value(seller.access_token_enc) == "acesso-novo"
            assert decrypt_value(seller.refresh_token_enc) == "refresh-novo"
            assert seller.token_expires_at > datetime.now(timezone.utc)
        finally:
            app.dependency_overrides.clear()
            await engine.dispose()
