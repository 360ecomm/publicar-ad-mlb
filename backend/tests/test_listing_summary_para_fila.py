"""`ListingSummary` ganha os tres campos que a fila (`WorkQueue`) precisa:
`ml_category_id`, `sku_description` e `approved_image_count`.

`approved_image_count` e' CALCULADO: imagens do anuncio com `approved = True`
e `sort_order < CANDIDATE_SORT_ORDER_FLOOR` (candidata aprovada em 90/91 nao
conta). A listagem devolve ate 200 linhas por pagina, entao a contagem NAO
pode virar uma consulta por anuncio — `test_numero_de_consultas_nao_cresce`
captura o SQL emitido para 1 e para 10 anuncios e exige o mesmo numero de
statements.

So roda com `TEST_DATABASE_URL` apontando pro banco dedicado `publicar_test`
(`_pg_dedicado`), como os outros testes de Postgres real. Nunca rodar junto
com outra suite que use o mesmo banco: todos fazem `drop_all`/`create_all`.
"""
import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

TEST_DB = os.environ.get("TEST_DATABASE_URL")
_precisa_db = pytest.mark.skipif(
    not TEST_DB, reason="TEST_DATABASE_URL nao definido: teste real so roda com Postgres dedicado"
)

# Kinds das 5 posicoes oficiais, na ordem — respeitam os indices unicos
# parciais dos slots de capa (0) e ficha (specs_ai < 90).
_KINDS_OFICIAIS = ("cover_ai", "presentation_ai", "benefits_ai", "detail_ai", "specs_ai")


async def _semear(session_maker, anuncios):
    """Cria 1 user, 1 seller e um listing por item de `anuncios`. Cada dict
    aceita `sku`, `description`, `category` (-> `ml_category_id`), `title` e
    `images`: lista de tuplas `(sort_order, approved)`; o kind sai da posicao
    (`_KINDS_OFICIAIS[sort_order]`) e, para candidata (>= 90), e' `cover_ai`.
    Devolve `(seller_id, {sku: listing_id})`."""
    from app.models.listing import Listing
    from app.models.listing_image import ListingImage
    from app.models.seller import Seller
    from app.models.user import User

    async with session_maker() as s:
        user = User(email=f"{uuid4()}@t.local", password_hash="x", full_name="t")
        s.add(user)
        await s.flush()
        seller = Seller(
            ml_user_id=int(str(uuid4().int)[:9]),
            ml_nickname="t",
            access_token_enc="x",
            refresh_token_enc="x",
            token_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        s.add(seller)
        await s.flush()

        ids = {}
        for spec in anuncios:
            listing = Listing(
                seller_id=seller.id,
                created_by=user.id,
                sku_external_id=spec["sku"],
                sku_description=spec.get("description", "descricao de origem"),
                sku_brand="b",
                price=10,
                stock_quantity=1,
                condition="new",
                listing_type_id="gold_special",
                status=spec.get("status", "pending_image_approval"),
                created_via="batch",
                selected_title=spec.get("title"),
                ml_category_id=spec.get("category"),
            )
            s.add(listing)
            await s.flush()
            ids[spec["sku"]] = listing.id
            for sort_order, approved in spec.get("images", []):
                kind = _KINDS_OFICIAIS[sort_order] if sort_order < 90 else "cover_ai"
                s.add(
                    ListingImage(
                        listing_id=listing.id,
                        ml_picture_id=f"pic-{uuid4().hex[:8]}",
                        status="uploaded",
                        approved=approved,
                        sort_order=sort_order,
                        kind=kind,
                    )
                )
        await s.commit()
        return seller.id, ids


def _cinco(aprovadas=5):
    return [(i, i < aprovadas) for i in range(5)]


@_precisa_db
class TestListingSummaryDeLinhaReal:
    @pytest.mark.asyncio
    async def test_serializa_os_tres_campos_de_uma_linha_real(self):
        """`ListingSummary.model_validate(listing)` sobre uma linha carregada
        do banco traz `ml_category_id`, `sku_description` e
        `approved_image_count` — e o JSON de saida tambem."""
        from sqlalchemy import select

        from app.models.listing import Listing
        from app.schemas.listing import ListingSummary
        from tests.test_listagem_em_escala import _preparar_banco

        engine, sm = await _preparar_banco()
        try:
            _, ids = await _semear(sm, [{
                "sku": "SKU37", "description": "Perfume Martin 100ml",
                "category": "MLB6284", "images": _cinco(2),
            }])
            async with sm() as s:
                listing = (await s.execute(
                    select(Listing).where(Listing.id == ids["SKU37"])
                )).scalar_one()
                summary = ListingSummary.model_validate(listing)
            assert summary.ml_category_id == "MLB6284"
            assert summary.sku_description == "Perfume Martin 100ml"
            assert summary.approved_image_count == 2
            dumped = summary.model_dump(mode="json")
            assert {"ml_category_id", "sku_description", "approved_image_count"} <= dumped.keys()
        finally:
            await engine.dispose()


@_precisa_db
class TestContagemDeAprovadas:
    @pytest.mark.asyncio
    async def test_cinco_quatro_candidata_e_nenhuma(self):
        """Na MESMA resposta de `list_listings`: 5 aprovadas -> 5; 4 -> 4;
        5 aprovadas + candidata aprovada em sort_order 90 -> 5 (a candidata
        nao conta); 5 geradas e nenhuma aprovada -> 0; sem imagem -> 0."""
        from app.services.listing_service import ListingService
        from tests.test_listagem_em_escala import _preparar_banco

        engine, sm = await _preparar_banco()
        try:
            seller_id, ids = await _semear(sm, [
                {"sku": "CINCO", "images": _cinco(5)},
                {"sku": "QUATRO", "images": _cinco(4)},
                {"sku": "CANDIDATA", "images": _cinco(5) + [(90, True)]},
                {"sku": "NAO_REVISADO", "images": _cinco(0)},
                {"sku": "SEM_IMAGEM"},
            ])
            async with sm() as s:
                page = await ListingService(s).list_listings(seller_id, None, 1, 50)
            por_sku = {i.sku_external_id: i.approved_image_count for i in page.items}
            assert por_sku == {
                "CINCO": 5, "QUATRO": 4, "CANDIDATA": 5, "NAO_REVISADO": 0, "SEM_IMAGEM": 0,
            }
        finally:
            await engine.dispose()


def _capturar_sql(engine):
    """Registra cada statement emitido pelo engine (via `before_cursor_execute`
    no engine sincrono por baixo do async) numa lista devolvida."""
    from sqlalchemy import event

    emitidos: list[str] = []

    def _ouvir(conn, cursor, statement, parameters, context, executemany):
        emitidos.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", _ouvir)
    return emitidos


@_precisa_db
class TestNumeroDeConsultas:
    @pytest.mark.asyncio
    async def test_numero_de_consultas_nao_cresce_com_o_numero_de_anuncios(self, capsys):
        """A contagem de aprovadas sai na PROPRIA consulta da listagem: com 1
        anuncio e com 10, `list_listings` emite o mesmo numero de statements
        (contagem total + pagina). O SQL vai para a saida (`-s`) como prova."""
        from sqlalchemy import select

        from app.services.listing_service import ListingService
        from tests.test_listagem_em_escala import _preparar_banco

        engine, sm = await _preparar_banco()
        try:
            emitidos = _capturar_sql(engine)

            async def _rodar(qtd):
                seller_id, _ = await _semear(
                    sm, [{"sku": f"S{n}", "images": _cinco(n % 6)} for n in range(qtd)]
                )
                del emitidos[:]
                async with sm() as s:
                    page = await ListingService(s).list_listings(seller_id, None, 1, 200)
                assert len(page.items) == qtd
                return list(emitidos)

            sql_1 = await _rodar(1)
            sql_10 = await _rodar(10)

            selects_1 = [q for q in sql_1 if q.lstrip().upper().startswith("SELECT")]
            selects_10 = [q for q in sql_10 if q.lstrip().upper().startswith("SELECT")]
            print("\n--- SQL emitido por list_listings com 1 anuncio ---")
            print("\n\n".join(selects_1))
            print("\n--- SQL emitido por list_listings com 10 anuncios ---")
            print("\n\n".join(selects_10))
            assert len(selects_1) == len(selects_10), (
                f"{len(selects_1)} consultas com 1 anuncio, {len(selects_10)} com 10: "
                "a contagem virou consulta por anuncio"
            )
            assert len(selects_10) <= 2
        finally:
            await engine.dispose()


@_precisa_db
class TestRotaListagem:
    @pytest.mark.asyncio
    async def test_get_listings_devolve_os_tres_campos_por_item(self):
        """`GET /api/v1/listings` real (ASGI) devolve, por item,
        `ml_category_id`, `sku_description` e `approved_image_count` certos
        para varios anuncios na mesma resposta."""
        from types import SimpleNamespace

        from httpx import ASGITransport, AsyncClient

        from app.core.dependencies import get_active_seller, get_db
        from app.main import app
        from tests.test_listagem_em_escala import _preparar_banco

        engine, sm = await _preparar_banco()
        try:
            seller_id, _ = await _semear(sm, [
                {"sku": "A", "description": "Body splash", "category": "MLB6284", "images": _cinco(5)},
                {"sku": "B", "description": "Rolamento", "category": None, "images": _cinco(3)},
                {"sku": "C", "description": "Caderno", "category": "MLB1234"},
            ])

            async def _override_get_db():
                async with sm() as session:
                    yield session

            async def _override_get_active_seller():
                return SimpleNamespace(id=seller_id)

            app.dependency_overrides[get_db] = _override_get_db
            app.dependency_overrides[get_active_seller] = _override_get_active_seller
            try:
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    resp = await client.get("/api/v1/listings?page_size=50")
                assert resp.status_code == 200, resp.text
                por_sku = {i["sku_external_id"]: i for i in resp.json()["items"]}
                assert por_sku["A"]["ml_category_id"] == "MLB6284"
                assert por_sku["A"]["sku_description"] == "Body splash"
                assert por_sku["A"]["approved_image_count"] == 5
                assert por_sku["B"]["ml_category_id"] is None
                assert por_sku["B"]["approved_image_count"] == 3
                assert por_sku["C"]["sku_description"] == "Caderno"
                assert por_sku["C"]["approved_image_count"] == 0
            finally:
                app.dependency_overrides.clear()
        finally:
            await engine.dispose()
