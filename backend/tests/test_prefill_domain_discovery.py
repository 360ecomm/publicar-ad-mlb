"""Atributos previstos pelo `domain_discovery` como fonte ADICIONAL de prefill.

Dado medido em 2026-09-10 (MLB6284): cada candidato do `domain_discovery`
traz `attributes`, uma lista de `{id, name, value_id, value_name}` — sempre
com `value_id`, sem `value_type`. O tipo vem de `/categories/{id}/attributes`,
que ja buscamos. Para o T38 vieram BRAND, LINE, PERFUME_NAME, VERSION, GENDER,
IS_REFILLABLE e UNIT_VOLUME — PERFUME_NAME e UNIT_VOLUME sao exatamente os
dois obrigatorios que travavam o lote em `pending_seller_attributes`.

Regras (as mesmas do prefill que ja existe):
- prefill de fonte mais confiavel (EAN do produto, marca do catalogo...) VENCE;
  o discovery so preenche o que ficou vazio;
- tipo `list` (e `boolean`, que tambem tem `values`): so entra se o
  `value_id` previsto existir nos `allowed_values` da categoria real — nome
  solto sem id nunca, e' o guard que segurou o PERFUME_TYPE;
- texto livre (`string`, `number_unit`): entra o `value_name`, sem id, e o ML
  resolve — mesmo tratamento de BRAND/MODEL, e o mesmo que publicou o SKU 38
  (PERFUME_NAME e UNIT_VOLUME foram ao ar so com `value_name`).
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _attr_ml(attr_id, name, value_type, values=None, tags=None):
    a = {"id": attr_id, "name": name, "value_type": value_type}
    if values is not None:
        a["values"] = values
    if tags:
        a["tags"] = tags
    return a


_GENEROS = [{"id": "339665", "name": "Feminino"}, {"id": "339666", "name": "Masculino"}]
_TIPOS = [{"id": "111075", "name": "Água de colônia"}, {"id": "111076", "name": "Eau de parfum"}]
_BOOL = [{"id": "242084", "name": "Não"}, {"id": "242085", "name": "Sim"}]


def _categoria_mlb6284():
    return [
        _attr_ml("BRAND", "Marca", "string", [{"id": "23688", "name": "Benetton"}], tags={"required": True}),
        _attr_ml("GTIN", "Código universal", "string", tags={"conditional_required": True}),
        _attr_ml("PERFUME_NAME", "Nome do perfume", "string", tags={"required": True}),
        _attr_ml("UNIT_VOLUME", "Volume da unidade", "number_unit", tags={"required": True}),
        _attr_ml("GENDER", "Gênero", "list", _GENEROS),
        _attr_ml("PERFUME_TYPE", "Tipo de perfume", "list", _TIPOS),
        _attr_ml("IS_REFILLABLE", "É recarregável", "boolean", _BOOL),
    ]


# Payload real devolvido para "Body Splash Fatal Black For Her Colônia 200ml Wepink".
_DISCOVERY_T38 = [
    {"id": "BRAND", "name": "Marca", "value_id": "13065330", "value_name": "Wepink"},
    {"id": "LINE", "name": "Linha", "value_id": "65152903", "value_name": "Fatal Black"},
    {"id": "PERFUME_NAME", "name": "Nome do perfume", "value_id": "65152906", "value_name": "Fatal Black For Her"},
    {"id": "VERSION", "name": "Versão", "value_id": "17492901", "value_name": "For Her"},
    {"id": "GENDER", "name": "Gênero", "value_id": "339665", "value_name": "Feminino"},
    {"id": "IS_REFILLABLE", "name": "É recarregável", "value_id": "242084", "value_name": "Não"},
    {"id": "UNIT_VOLUME", "name": "Volume da unidade", "value_id": "112849", "value_name": "200 mL"},
]


def _listing(brand="Wepink"):
    listing = MagicMock()
    listing.id = "lid"
    listing.ml_category_id = "MLB6284"
    listing.condition = "new"
    listing.sku_brand = brand
    listing.sku_model = None
    listing.sku_external_id = "T38"
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


class TestDiscoveryPreencheObrigatorios:
    @pytest.mark.asyncio
    async def test_t38_nao_para_mais_por_perfume_name_e_unit_volume(self):
        from app.services.category_service import CategoryService

        db = _db()
        listing = _listing()

        await CategoryService(db)._save_attributes(
            listing, _categoria_mlb6284(), ean="7908647604106", discovered=_DISCOVERY_T38
        )

        s = _salvos(db)
        assert s["PERFUME_NAME"].value_name == "Fatal Black For Her"
        assert s["PERFUME_NAME"].value_id is None, "texto livre: o ML resolve o id"
        assert s["PERFUME_NAME"].source == "discovery"
        assert s["UNIT_VOLUME"].value_name == "200 mL"
        assert s["UNIT_VOLUME"].value_id is None
        assert listing.status == "pending_description"

    @pytest.mark.asyncio
    async def test_lista_e_boolean_entram_com_id_validado(self):
        from app.services.category_service import CategoryService

        db = _db()

        await CategoryService(db)._save_attributes(
            _listing(), _categoria_mlb6284(), ean="7908647604106", discovered=_DISCOVERY_T38
        )

        s = _salvos(db)
        assert (s["GENDER"].value_id, s["GENDER"].value_name) == ("339665", "Feminino")
        assert (s["IS_REFILLABLE"].value_id, s["IS_REFILLABLE"].value_name) == ("242084", "Não")

    @pytest.mark.asyncio
    async def test_sem_discovery_comportamento_de_antes(self):
        from app.services.category_service import CategoryService

        db = _db()
        listing = _listing()

        await CategoryService(db)._save_attributes(listing, _categoria_mlb6284(), ean="7908647604106")

        s = _salvos(db)
        assert s["PERFUME_NAME"].value_name is None
        assert listing.status == "pending_seller_attributes"


class TestDiscoveryNaoVazaNemSobrescreve:
    @pytest.mark.asyncio
    async def test_lista_com_id_fora_dos_allowed_values_e_ignorada(self):
        """O caso PERFUME_TYPE: o discovery sugere 'Body splash' id 19463164,
        que NAO esta na lista desta categoria. Entrar sem id valido e' 400 na
        publicacao, depois de imagem e descricao pagas."""
        from app.services.category_service import CategoryService

        db = _db()
        listing = _listing()
        sugerido = [{"id": "PERFUME_TYPE", "name": "Tipo", "value_id": "19463164", "value_name": "Body splash"}]

        await CategoryService(db)._save_attributes(
            listing, _categoria_mlb6284(), ean="7908647604106", discovered=sugerido
        )

        t = _salvos(db)["PERFUME_TYPE"]
        assert t.value_name is None and t.value_id is None

    @pytest.mark.asyncio
    async def test_lista_obrigatoria_sem_id_valido_continua_pendente(self):
        from app.services.category_service import CategoryService

        db = _db()
        listing = _listing()
        attrs = [_attr_ml("PERFUME_TYPE", "Tipo", "list", _TIPOS, tags={"required": True})]
        sugerido = [{"id": "PERFUME_TYPE", "name": "Tipo", "value_id": "19463164", "value_name": "Body splash"}]

        await CategoryService(db)._save_attributes(listing, attrs, discovered=sugerido)

        assert listing.status == "pending_seller_attributes"

    @pytest.mark.asyncio
    async def test_nome_de_lista_que_casa_mas_id_diferente_nao_entra(self):
        """Validacao e' pelo ID, nao pelo nome: id errado com nome certo e'
        exatamente o dado inconsistente que nao queremos gravar."""
        from app.services.category_service import CategoryService

        db = _db()
        attrs = [_attr_ml("GENDER", "Gênero", "list", _GENEROS)]
        sugerido = [{"id": "GENDER", "name": "Gênero", "value_id": "999", "value_name": "Feminino"}]

        await CategoryService(db)._save_attributes(_listing(), attrs, discovered=sugerido)

        g = _salvos(db)["GENDER"]
        assert g.value_name is None and g.value_id is None

    @pytest.mark.asyncio
    async def test_prefill_existente_vence_o_discovery(self):
        from app.services.category_service import CategoryService

        db = _db()
        sugerido = [
            {"id": "BRAND", "name": "Marca", "value_id": "1", "value_name": "Outra Marca"},
            {"id": "GTIN", "name": "GTIN", "value_id": "2", "value_name": "0000000000000"},
        ]

        await CategoryService(db)._save_attributes(
            _listing(brand="Wepink"), _categoria_mlb6284(), ean="7908647604106", discovered=sugerido
        )

        s = _salvos(db)
        assert s["BRAND"].value_name == "Wepink" and s["BRAND"].source == "seller"
        assert s["GTIN"].value_name == "7908647604106" and s["GTIN"].source == "seller"

    @pytest.mark.asyncio
    async def test_atributo_previsto_que_a_categoria_nao_tem_e_ignorado(self):
        """So gravamos atributos da categoria real; o discovery nao cria linha."""
        from app.services.category_service import CategoryService

        db = _db()
        attrs = [_attr_ml("PERFUME_NAME", "Nome", "string")]
        sugerido = [{"id": "ALIEN", "name": "X", "value_id": "1", "value_name": "y"}]

        await CategoryService(db)._save_attributes(_listing(), attrs, discovered=sugerido)

        assert set(_salvos(db)) == {"PERFUME_NAME"}


class TestPredictAndSaveRepassaODiscovery:
    @pytest.mark.asyncio
    async def test_atributos_do_primeiro_candidato_chegam_ao_save(self):
        from app.services.category_service import CategoryService

        db = _db()
        db.execute.return_value = MagicMock(scalar_one=MagicMock(return_value=MagicMock()))
        svc = CategoryService(db)
        candidato = {"category_id": "MLB6284", "category_name": "Perfumes", "attributes": _DISCOVERY_T38}
        svc._discover = AsyncMock(return_value=candidato)
        svc._get_attributes = AsyncMock(return_value=_categoria_mlb6284())
        svc._save_attributes = AsyncMock()
        listing = _listing()

        with patch("app.services.publish_service.get_valid_access_token", new_callable=AsyncMock, return_value="tok"):
            await svc.predict_and_save(listing, ean="7908647604106")

        assert listing.ml_category_id == "MLB6284"
        assert svc._save_attributes.await_args.kwargs["discovered"] == _DISCOVERY_T38
