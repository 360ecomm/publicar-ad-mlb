"""Atributos guardam as `tags` do ML e dizem se sao editaveis.

A tela de atributos do SKU 45 (MLB7863) mostraria 80 linhas, e 67 delas sao
campos internos do ML (`hidden`/`read_only`: CATALOG_TITLE,
SEARCH_ENHANCEMENT_FIELDS, EXCLUDED_PLATFORMS, fiscais, regulatorios...). O
backend ja le `attr["tags"]` em `category_service`, mas so aproveita
`required`/`conditional_required` e joga o resto fora — inclusive o que
permitiria filtrar.

Regra num lugar so: `ListingAttribute.is_editable` (mesmo principio de
`ListingImage.is_candidate`). O frontend consome o booleano, nunca
reimplementa. Na duvida (linha antiga sem tags gravadas), mostrar.

Os testes de schema/endpoint e o de `_save_attributes` nao precisam de
banco; o HTTP do endpoint de detalhe roda em Postgres real (so
`publicar_test`, via `_pg_dedicado`).
"""
import os
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

TEST_DB = os.environ.get("TEST_DATABASE_URL")
_precisa_db = pytest.mark.skipif(
    not TEST_DB, reason="TEST_DATABASE_URL nao definido: teste real so roda com Postgres dedicado"
)


def _attr_model(tags):
    from app.models.listing_attribute import ListingAttribute

    # `id` explicito: o default do model so entra no flush, e o schema exige UUID.
    return ListingAttribute(
        id=uuid4(),
        attribute_id="X",
        attribute_name="x",
        attribute_type="string",
        is_required=False,
        source="ai",
        tags=tags,
    )


class TestIsEditable:
    """`False` so quando as tags trazem `hidden` ou `read_only` verdadeiros.
    Tudo o mais — inclusive a AUSENCIA de tags — e' editavel: nunca esconder
    por falta de dado."""

    def test_hidden_nao_e_editavel(self):
        assert _attr_model({"hidden": True}).is_editable is False

    def test_read_only_nao_e_editavel(self):
        assert _attr_model({"read_only": True}).is_editable is False

    def test_tags_nulas_e_editavel(self):
        """Linhas antigas (antes da coluna existir) ficam com `tags` NULL —
        e' exatamente o caso em que nao sabemos nada, entao mostramos."""
        assert _attr_model(None).is_editable is True

    def test_dicionario_vazio_e_editavel(self):
        assert _attr_model({}).is_editable is True

    def test_so_required_e_editavel(self):
        assert _attr_model({"required": True}).is_editable is True

    def test_fixed_nao_entra_na_regra_por_enquanto(self):
        """`fixed` (VEHICLE_TYPE em MLB7863: valor unico, obrigatorio) fica
        fora da regra ate decisao do dono do produto — hoje continua editavel."""
        assert _attr_model({"fixed": True, "required": True}).is_editable is True


# ── _save_attributes grava as tags inteiras ─────────────────────────────────

def _listing():
    listing = MagicMock()
    listing.id = "lid"
    listing.ml_category_id = "MLB7863"
    listing.condition = "new"
    listing.sku_brand = "Arteb"
    listing.sku_model = None
    listing.sku_external_id = "FAROL01"
    listing.package_weight_kg = None
    listing.package_length_cm = None
    listing.package_width_cm = None
    listing.package_height_cm = None
    return listing


def _db():
    db = AsyncMock()
    db.add = MagicMock()
    db.execute = AsyncMock()
    db.commit = AsyncMock()
    return db


def _salvos(db):
    return {c.args[0].attribute_id: c.args[0] for c in db.add.call_args_list}


# Recorte real de MLB7863 (API publica, 2026-09-12): tags variadas, inclusive
# um atributo SEM a chave `tags`.
_ATTRS_MLB7863 = [
    {"id": "BRAND", "name": "Marca", "value_type": "string",
     "tags": {"required": True}},
    {"id": "CATALOG_TITLE", "name": "Titulo de catalogo", "value_type": "string",
     "tags": {"hidden": True, "read_only": True}},
    {"id": "EXCLUDED_PLATFORMS", "name": "Plataformas excluidas", "value_type": "list",
     "tags": {"hidden": True, "multivalued": True}},
    {"id": "GTIN", "name": "Codigo universal", "value_type": "string",
     "tags": {"conditional_required": True}},
    {"id": "VEHICLE_TYPE", "name": "Tipo de veiculo", "value_type": "list",
     "values": [{"id": "1", "name": "Carro/Caminhonete"}],
     "tags": {"catalog_required": True, "fixed": True, "required": True}},
    {"id": "OEM", "name": "Codigo OEM", "value_type": "string"},
]


class TestSaveAttributesGravaTags:
    @pytest.mark.asyncio
    async def test_grava_o_dicionario_de_tags_inteiro_como_o_ml_devolve(self):
        """Um campo por tag exigiria migracao a cada tag nova do ML; o JSONB
        com o dicionario inteiro acompanha sozinho (mesmo padrao de
        `allowed_values`)."""
        from app.services.category_service import CategoryService

        db = _db()
        await CategoryService(db)._save_attributes(_listing(), _ATTRS_MLB7863)

        salvos = _salvos(db)
        assert salvos["BRAND"].tags == {"required": True}
        assert salvos["CATALOG_TITLE"].tags == {"hidden": True, "read_only": True}
        assert salvos["EXCLUDED_PLATFORMS"].tags == {"hidden": True, "multivalued": True}
        assert salvos["VEHICLE_TYPE"].tags == {
            "catalog_required": True, "fixed": True, "required": True,
        }

    @pytest.mark.asyncio
    async def test_atributo_sem_chave_tags_nasce_com_tags_nulas(self):
        """8 dos 80 atributos de MLB7863 vem sem `tags`. Gravar NULL (e nao
        `{}`) preserva a distincao "o ML nao mandou" — e `is_editable`
        continua True nos dois casos."""
        from app.services.category_service import CategoryService

        db = _db()
        await CategoryService(db)._save_attributes(_listing(), _ATTRS_MLB7863)

        oem = _salvos(db)["OEM"]
        assert oem.tags is None
        assert oem.is_editable is True

    @pytest.mark.asyncio
    async def test_is_required_continua_igual_ao_de_hoje(self):
        """`required` e `conditional_required` -> True; `hidden` sozinho ->
        False; sem tags -> False. Nada disso muda com a coluna nova."""
        from app.services.category_service import CategoryService

        db = _db()
        await CategoryService(db)._save_attributes(_listing(), _ATTRS_MLB7863)

        salvos = _salvos(db)
        assert salvos["BRAND"].is_required is True
        assert salvos["GTIN"].is_required is True
        assert salvos["VEHICLE_TYPE"].is_required is True
        assert salvos["CATALOG_TITLE"].is_required is False
        assert salvos["EXCLUDED_PLATFORMS"].is_required is False
        assert salvos["OEM"].is_required is False

    @pytest.mark.asyncio
    async def test_is_editable_derivado_das_tags_gravadas(self):
        from app.services.category_service import CategoryService

        db = _db()
        await CategoryService(db)._save_attributes(_listing(), _ATTRS_MLB7863)

        salvos = _salvos(db)
        assert salvos["BRAND"].is_editable is True
        assert salvos["CATALOG_TITLE"].is_editable is False
        assert salvos["EXCLUDED_PLATFORMS"].is_editable is False
        assert salvos["GTIN"].is_editable is True


# ── Schema ──────────────────────────────────────────────────────────────────

class TestAttributeOut:
    def test_expoe_tags_e_is_editable(self):
        """`is_editable` e' calculado no backend (propriedade do model) e o
        `from_attributes` do Pydantic o le como qualquer coluna."""
        from app.schemas.listing import AttributeOut

        out = AttributeOut.model_validate(_attr_model({"hidden": True}))
        assert out.tags == {"hidden": True}
        assert out.is_editable is False

    def test_tags_nulas_saem_como_null_e_editavel(self):
        from app.schemas.listing import AttributeOut

        out = AttributeOut.model_validate(_attr_model(None))
        assert out.tags is None
        assert out.is_editable is True


# ── Endpoint de detalhe (Postgres real) ─────────────────────────────────────

@_precisa_db
class TestDetalheTrazTagsNosAtributos:
    @pytest.mark.asyncio
    async def test_get_listing_devolve_tags_e_is_editable_por_atributo(self):
        from types import SimpleNamespace

        from httpx import ASGITransport, AsyncClient

        from app.core.dependencies import get_active_seller, get_db
        from app.main import app
        from app.models.listing_attribute import ListingAttribute
        from tests.test_listagem_em_escala import _preparar_banco, _semear

        engine, sm = await _preparar_banco()
        try:
            user_id, (seller_id,) = await _semear(
                sm, [[{"status": "pending_seller_attributes", "sku": "FAROL01"}]]
            )
            async with sm() as s:
                from sqlalchemy import select

                from app.models.listing import Listing

                listing_id = (await s.execute(
                    select(Listing.id).where(Listing.seller_id == seller_id)
                )).scalar_one()
                s.add_all([
                    ListingAttribute(
                        listing_id=listing_id, attribute_id="BRAND", attribute_name="Marca",
                        attribute_type="string", is_required=True, source="seller",
                        tags={"required": True},
                    ),
                    ListingAttribute(
                        listing_id=listing_id, attribute_id="CATALOG_TITLE",
                        attribute_name="Titulo de catalogo", attribute_type="string",
                        is_required=False, source="ai",
                        tags={"hidden": True, "read_only": True},
                    ),
                    ListingAttribute(
                        listing_id=listing_id, attribute_id="OEM", attribute_name="OEM",
                        attribute_type="string", is_required=False, source="ai",
                        tags=None,
                    ),
                ])
                await s.commit()

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
                    resp = await client.get(f"/api/v1/listings/{listing_id}")
                assert resp.status_code == 200, resp.text
                por_id = {a["attribute_id"]: a for a in resp.json()["attributes"]}
                assert por_id["BRAND"]["tags"] == {"required": True}
                assert por_id["BRAND"]["is_editable"] is True
                assert por_id["CATALOG_TITLE"]["tags"] == {"hidden": True, "read_only": True}
                assert por_id["CATALOG_TITLE"]["is_editable"] is False
                assert por_id["OEM"]["tags"] is None
                assert por_id["OEM"]["is_editable"] is True
            finally:
                app.dependency_overrides.clear()
        finally:
            await engine.dispose()
