"""`EMPTY_GTIN_REASON` so e pendencia quando o GTIN esta vazio.

A API do ML marca os dois com a MESMA tag, `conditional_required`, e nenhum
com `required` (conferido em MLB6284 em 2026-09-09):

    GTIN               {'conditional_required': True, ...}
    EMPTY_GTIN_REASON  {'conditional_required': True, 'hidden': True, ...}

Sao um par: o ML exige um OU o outro. A API nao diz de que o "conditional"
depende, entao a dependencia (`EMPTY_GTIN_REASON` so faz sentido sem GTIN) e
regra nossa — mas gateada pela tag da API: se um dia o ML marcar
`EMPTY_GTIN_REASON` como `required` puro, voltamos a exigir sempre.

Sem isso, todo anuncio com EAN valido parava em `pending_seller_attributes`
por causa de um atributo oculto que o proprio ML nao pediria — e em lote isso
significa que nenhum SKU de perfumaria chegava as imagens sozinho.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest


def _attr_ml(attr_id, name, value_type="string", tags=None):
    a = {"id": attr_id, "name": name, "value_type": value_type}
    if tags:
        a["tags"] = tags
    return a


_MOTIVOS = [
    {"id": "1", "name": "O produto não tem código de barras"},
    {"id": "2", "name": "Fabricante não fornece o código"},
]


def _par_gtin(empty_reason_tags=None):
    """GTIN + EMPTY_GTIN_REASON com as tags reais de MLB6284."""
    return [
        _attr_ml("GTIN", "Código universal de produto", "string",
                 tags={"conditional_required": True, "validate": True}),
        _attr_ml("EMPTY_GTIN_REASON", "Motivo de GTIN vazio", "list",
                 tags=empty_reason_tags or {"conditional_required": True, "hidden": True})
        | {"values": _MOTIVOS},
    ]


def _listing():
    listing = MagicMock()
    listing.id = "lid"
    listing.ml_category_id = "MLB6284"
    listing.condition = "new"
    listing.sku_brand = "Wepink"
    listing.sku_model = None
    listing.sku_external_id = "38"
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


class TestEmptyGtinReasonCondicional:
    @pytest.mark.asyncio
    async def test_com_gtin_preenchido_nao_e_pendencia(self):
        """EAN valido chega ao GTIN; o motivo de GTIN vazio nao tem o que
        justificar e nao pode segurar o anuncio."""
        from app.services.category_service import CategoryService

        db = _db()
        listing = _listing()

        await CategoryService(db)._save_attributes(listing, _par_gtin(), ean="7908647604106")

        salvos = _salvos(db)
        assert salvos["GTIN"].value_name == "7908647604106"
        assert salvos["EMPTY_GTIN_REASON"].is_required is False
        assert listing.status == "pending_description"

    @pytest.mark.asyncio
    async def test_sem_gtin_continua_exigindo(self):
        """Produto sem EAN: o par fica pendente e o seller precisa agir."""
        from app.services.category_service import CategoryService

        db = _db()
        listing = _listing()

        await CategoryService(db)._save_attributes(listing, _par_gtin(), ean=None)

        salvos = _salvos(db)
        assert salvos["GTIN"].value_name is None
        assert salvos["EMPTY_GTIN_REASON"].is_required is True
        assert listing.status == "pending_seller_attributes"

    @pytest.mark.asyncio
    async def test_ean_invalido_conta_como_gtin_vazio(self):
        """EAN "NA" nao vira GTIN (regra ja existente), entao o motivo volta a
        ser pendencia — a condicao e o GTIN gravado, nao o EAN recebido."""
        from app.services.category_service import CategoryService

        db = _db()
        listing = _listing()

        await CategoryService(db)._save_attributes(listing, _par_gtin(), ean="NA")

        salvos = _salvos(db)
        assert salvos["GTIN"].value_name is None
        assert salvos["EMPTY_GTIN_REASON"].is_required is True
        assert listing.status == "pending_seller_attributes"

    @pytest.mark.asyncio
    async def test_required_puro_da_api_prevalece(self):
        """A regra e gateada pela tag: se o ML marcar `required` (nao
        condicional), obedecemos a API e exigimos mesmo com GTIN."""
        from app.services.category_service import CategoryService

        db = _db()
        listing = _listing()
        attrs = _par_gtin(empty_reason_tags={"required": True})

        await CategoryService(db)._save_attributes(listing, attrs, ean="7908647604106")

        salvos = _salvos(db)
        assert salvos["EMPTY_GTIN_REASON"].is_required is True
        assert listing.status == "pending_seller_attributes"
