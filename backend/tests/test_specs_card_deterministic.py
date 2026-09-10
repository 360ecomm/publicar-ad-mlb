"""Frente B: a ficha tecnica sai dos atributos, nao da redacao do LLM.

O defeito que originou estes testes: o bullet de tipo saiu como "Desodorante
colonia" numa execucao e "Agua de colonia" — o `value_name` real — em outra.
Mesmo prompt, mesmo produto, resultado diferente. Era sorteio, e o card
Pillow que ja vai ao ar em todo anuncio corria o mesmo risco, porque os dois
caminhos consomem `generate_card_copy`.

Ficha tecnica e dado estruturado. O LLM segue escrevendo `card_benefits` e
`card_usage`, que sao narrativa.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# R2 em memoria: o service le/grava bytes no bucket de ativos; aqui, `_bytes`
# no proprio mock da capa faz o papel do objeto no R2.
_R2 = {}


@pytest.fixture(autouse=True)
def _r2_em_memoria():
    async def _load(img):
        return getattr(img, "_bytes", None)

    store = AsyncMock(return_value="asset-key-teste")
    with patch("app.services.r2_asset_service.load_candidate_bytes", side_effect=_load), \
         patch("app.services.r2_asset_service.store_candidate_bytes", store):
        _R2["store"] = store
        yield


def _attr(attribute_id, attribute_name, value_name, value_id=None):
    a = MagicMock()
    a.attribute_id = attribute_id
    a.attribute_name = attribute_name
    a.value_name = value_name
    a.value_id = value_id
    return a


def _sku37_attributes():
    """Os atributos reais do anuncio que expos o defeito (SKU 37)."""
    return [
        _attr("BRAND", "Marca", "Wepink"),
        _attr("GTIN", "Código universal de produto", "7908981505459"),
        _attr("IS_FLAMMABLE", "É inflamável", "Sim", value_id="242085"),
        _attr("ITEM_CONDITION", "Condição do item", "Novo", value_id="2230284"),
        _attr("MODEL", "Modelo", "Martin"),
        _attr("PERFUME_NAME", "Nome do perfume", "Martin"),
        _attr("PERFUME_TYPE", "Tipo de perfume", "Água de colônia", value_id="111075"),
        _attr("SELLER_PACKAGE_HEIGHT", "Altura da embalagem do vendor", "10 cm"),
        _attr("SELLER_PACKAGE_WEIGHT", "Peso da embalagem do vendor", "200 g"),
        _attr("SELLER_SKU", "SKU", "37"),
        _attr("UNIT_VOLUME", "Volume da unidade", "100 ml"),
    ]


class TestBuildSpecsCard:
    def test_reproduces_the_exact_value_name_of_the_list_attribute(self):
        """O caso que motivou a correcao: o value_name real, nunca uma
        parafrase como "Desodorante colonia"."""
        from app.services.image_card_copy_service import build_specs_card

        card = build_specs_card(_sku37_attributes())

        assert card is not None
        assert any("Água de colônia" in b for b in card.bullets)
        assert not any("Desodorante" in b for b in card.bullets)

    def test_is_stable_across_repeated_calls(self):
        """"Em qualquer execucao" e o requisito — nao pode mais ser sorteio."""
        from app.services.image_card_copy_service import build_specs_card

        primeiro = build_specs_card(_sku37_attributes())
        for _ in range(20):
            outro = build_specs_card(_sku37_attributes())
            assert outro.title == primeiro.title
            assert outro.bullets == primeiro.bullets

    def test_kind_and_fixed_title(self):
        from app.services.image_card_copy_service import (
            SPECS_CARD_TITLE,
            build_specs_card,
        )

        card = build_specs_card(_sku37_attributes())
        assert card.kind == "card_specs"
        assert card.title == SPECS_CARD_TITLE

    def test_brand_and_model_come_first(self):
        from app.services.image_card_copy_service import build_specs_card

        card = build_specs_card(_sku37_attributes())
        assert card.bullets[0] == "Marca: Wepink"
        assert card.bullets[1] == "Modelo: Martin"

    def test_excludes_attributes_that_do_not_describe_the_product(self):
        """SKU, GTIN, condicao, inflamabilidade e medidas de embalagem nao sao
        ficha tecnica de vitrine — ocupariam o lugar do que importa."""
        from app.services.image_card_copy_service import build_specs_card

        texto = " | ".join(build_specs_card(_sku37_attributes()).bullets)
        for proibido in ("7908981505459", "SKU", "Condição", "inflamável",
                         "200 g", "10 cm"):
            assert proibido not in texto

    def test_skips_a_value_already_shown_by_another_attribute(self):
        """PERFUME_NAME repete o MODEL ("Martin"). Gastar um dos 3 bullets
        para dizer a mesma coisa duas vezes empurraria o tipo para fora."""
        from app.services.image_card_copy_service import build_specs_card

        bullets = build_specs_card(_sku37_attributes()).bullets
        assert sum(1 for b in bullets if "Martin" in b) == 1

    def test_respects_the_bullet_cap(self):
        from app.services.image_card_copy_service import (
            MAX_BULLETS,
            build_specs_card,
        )

        assert len(build_specs_card(_sku37_attributes()).bullets) <= MAX_BULLETS

    def test_drops_an_oversized_bullet_instead_of_truncating_the_value(self):
        """Truncar quebraria a garantia: meio valor nao e o value_name.
        Melhor um bullet a menos do que um valor mutilado."""
        from app.services.image_card_copy_service import (
            MAX_BULLET_CHARS,
            build_specs_card,
        )

        gigante = "x" * (MAX_BULLET_CHARS + 20)
        attrs = [
            _attr("BRAND", "Marca", "Wepink"),
            _attr("MODEL", "Modelo", "Martin"),
            _attr("PERFUME_TYPE", "Tipo de perfume", gigante, value_id="1"),
        ]
        card = build_specs_card(attrs)

        assert all(len(b) <= MAX_BULLET_CHARS for b in card.bullets)
        assert not any(gigante[:20] in b for b in card.bullets)

    def test_returns_none_when_there_are_not_enough_usable_attributes(self):
        from app.services.image_card_copy_service import build_specs_card

        assert build_specs_card([_attr("BRAND", "Marca", "Wepink")]) is None
        assert build_specs_card([]) is None
        assert build_specs_card(None) is None

    def test_ignores_attributes_without_value_name(self):
        from app.services.image_card_copy_service import build_specs_card

        attrs = _sku37_attributes() + [_attr("VAZIO", "Vazio", None)]
        texto = " | ".join(build_specs_card(attrs).bullets)
        assert "Vazio" not in texto


def _listing_for_copy():
    listing = MagicMock()
    listing.id = "lid"
    listing.selected_title = "Perfume Martin"
    listing.sku_description = "desc"
    listing.sku_brand = "Wepink"
    listing.sku_model = "Martin"
    return listing


def _raw_angles(specs_bullets):
    return {
        "benefits": {"title": "Benefícios", "bullets": ["Fixação longa", "Amadeirado"]},
        "usage": {"title": "Modo de uso", "bullets": ["Aplique nos pulsos", "Evite sol"]},
        "specs": {"title": "Ficha", "bullets": specs_bullets},
    }


class TestGenerateCardCopyUsesDeterministicSpecs:
    @pytest.mark.asyncio
    async def test_specs_angle_from_the_llm_is_ignored(self):
        """Mesmo que o LLM devolva um angulo de specs bem formado, ele nao
        entra: quem manda no card_specs sao os atributos."""
        from app.services.image_card_copy_service import generate_card_copy

        raw = _raw_angles(["Tipo: Desodorante colônia", "Marca: Wepink"])
        provider = MagicMock()
        provider.generate_card_copy = AsyncMock(return_value=raw)
        with patch("app.services.ai.service.get_ai_provider", return_value=provider):
            cards = await generate_card_copy(_listing_for_copy(), _sku37_attributes())

        specs = next(c for c in cards if c.kind == "card_specs")
        assert not any("Desodorante" in b for b in specs.bullets)
        assert any("Água de colônia" in b for b in specs.bullets)

    @pytest.mark.asyncio
    async def test_benefits_and_usage_still_come_from_the_llm(self):
        from app.services.image_card_copy_service import generate_card_copy

        provider = MagicMock()
        provider.generate_card_copy = AsyncMock(return_value=_raw_angles(["x", "y"]))
        with patch("app.services.ai.service.get_ai_provider", return_value=provider):
            cards = await generate_card_copy(_listing_for_copy(), _sku37_attributes())

        benefits = next(c for c in cards if c.kind == "card_benefits")
        assert benefits.bullets == ["Fixação longa", "Amadeirado"]
        usage = next(c for c in cards if c.kind == "card_usage")
        assert usage.title == "Modo de uso"

    @pytest.mark.asyncio
    async def test_specs_card_survives_a_missing_specs_angle(self):
        """O angulo de specs some da resposta do LLM: antes isso derrubava o
        card. Agora ele nao depende mais do LLM para existir."""
        from app.services.image_card_copy_service import generate_card_copy

        raw = _raw_angles(["x", "y"])
        del raw["specs"]
        provider = MagicMock()
        provider.generate_card_copy = AsyncMock(return_value=raw)
        with patch("app.services.ai.service.get_ai_provider", return_value=provider):
            cards = await generate_card_copy(_listing_for_copy(), _sku37_attributes())

        assert any(c.kind == "card_specs" for c in cards)

    @pytest.mark.asyncio
    async def test_card_order_is_preserved(self):
        from app.services.image_card_copy_service import CARD_KINDS, generate_card_copy

        provider = MagicMock()
        provider.generate_card_copy = AsyncMock(return_value=_raw_angles(["x", "y"]))
        with patch("app.services.ai.service.get_ai_provider", return_value=provider):
            cards = await generate_card_copy(_listing_for_copy(), _sku37_attributes())

        assert [c.kind for c in cards] == list(CARD_KINDS)


class TestSpecsVariantUsesDeterministicBullets:
    @pytest.mark.asyncio
    async def test_specs_variant_does_not_call_the_copy_llm(self):
        """A Frente B usa o motor de imagem so para compor foto + layout.
        O texto e fixo, entao nao ha razao para gastar chamada de LLM — nem
        para herdar o modo de falha "a copy nao veio, tente de novo"."""
        from app.services.image_service import ImageValidationResult
        from app.services.specs_variant_service import generate_specs_variant

        cover = MagicMock()
        cover._bytes = b"cover-bytes"
        cover.asset_key = "asset-key-capa"
        cover.source_sku = "37"

        listing = MagicMock()
        listing.id = "lid"
        listing.ml_category_id = "MLB6284"

        mock_db = AsyncMock()
        attrs_result = MagicMock()
        attrs_result.scalars.return_value.all.return_value = _sku37_attributes()
        mock_db.execute = AsyncMock(return_value=attrs_result)
        mock_db.add = MagicMock()
        mock_db.commit = AsyncMock()

        with patch(
            "app.services.specs_variant_service._load_latest_deterministic_cover",
            new_callable=AsyncMock,
            return_value=cover,
        ), patch(
            "app.services.image_engines.openai_edit_engine.OpenAIEditEngine"
        ) as mock_engine_cls, patch(
            "app.workers.tasks.image_tasks._prepare_image_for_upload",
            return_value=(b"prepared", ImageValidationResult(is_valid=True)),
        ), patch(
            "app.services.image_service.MLPictureService"
        ) as mock_ml_cls, patch(
            "app.services.image_card_copy_service.generate_card_copy",
            new_callable=AsyncMock,
        ) as mock_copy:
            mock_engine_cls.return_value.edit = AsyncMock(return_value=[b"variant"])
            mock_ml_cls.return_value.upload = AsyncMock(return_value="pic-specs")

            await generate_specs_variant(mock_db, listing, "token")

            mock_copy.assert_not_awaited()

        prompt = mock_engine_cls.return_value.edit.await_args.kwargs["prompt"]
        assert "Água de colônia" in prompt
        assert "Desodorante colônia" not in prompt

    @pytest.mark.asyncio
    async def test_raises_when_attributes_cannot_make_a_specs_card(self):
        """Sem atributos suficientes nao ha ficha a compor — falhar antes do
        motor pago, mesma regra ja aplicada a capa sem bytes."""
        from app.services.specs_variant_service import (
            SpecsVariantError,
            generate_specs_variant,
        )

        cover = MagicMock()
        cover._bytes = b"cover-bytes"
        cover.asset_key = "asset-key-capa"
        cover.source_sku = "37"

        mock_db = AsyncMock()
        attrs_result = MagicMock()
        attrs_result.scalars.return_value.all.return_value = []
        mock_db.execute = AsyncMock(return_value=attrs_result)

        with patch(
            "app.services.specs_variant_service._load_latest_deterministic_cover",
            new_callable=AsyncMock,
            return_value=cover,
        ), patch(
            "app.services.image_engines.openai_edit_engine.OpenAIEditEngine"
        ) as mock_engine_cls:
            mock_engine_cls.return_value.edit = AsyncMock()

            with pytest.raises(SpecsVariantError):
                await generate_specs_variant(mock_db, MagicMock(), "token")

            mock_engine_cls.return_value.edit.assert_not_awaited()


class TestSpecsPromptLinguagemUnificada:
    """O prompt da ficha usa a MESMA linguagem visual das posicoes 2, 3 e 4.

    O Gabriel notou que as posicoes 2 e 5 destoavam da 3: fundo neutro com
    painel colado na borda, em vez de cena atmosferica de fundo inteiro com
    cartao flutuando dentro dela. A causa era a redacao do prompt, nao o
    modelo — o texto antigo pedia literalmente "Lower ~40%: a clean panel".

    E a ficha nao tem mais titulo: sai so com os bullets.
    """

    @staticmethod
    def _prompt(bullets=None):
        """Espaco em branco normalizado: a quebra de linha do prompt e
        cosmetica, e sem isso a assercao mediria ONDE a linha quebrou em vez
        do que o prompt pede."""
        import re

        from app.services.specs_variant_service import _build_specs_prompt

        bruto = _build_specs_prompt(
            bullets or ["Marca: Wepink", "Modelo: Martin", "Tipo de perfume: Água de colônia"]
        )
        return re.sub(r"\s+", " ", bruto).lower()

    def test_pede_cena_de_fundo_inteiro_amostrada_do_produto(self):
        p = self._prompt()
        assert "full-bleed backdrop" in p
        assert "sampled from the product's own dominant color" in p
        assert "not flat white" in p

    def test_pede_cartao_flutuante_com_margem_das_quatro_bordas(self):
        """O ponto da correcao: cartao integrado com margem, nunca painel
        colado na borda cobrindo a largura toda."""
        p = self._prompt()
        assert "integrated rounded card" in p
        assert "visible margin from all four edges" in p
        assert "never a flat panel spanning the full width" in p

    def test_nao_pede_mais_o_painel_colado_na_borda(self):
        """A redacao que causava a quebra de estilo nao pode voltar."""
        p = self._prompt()
        assert "lower ~40%" not in p
        assert "clean panel in a light neutral tone" not in p

    def test_pede_textura_de_papel_e_luz_direcional(self):
        p = self._prompt()
        assert "paper-like texture" in p
        assert "directional lighting" in p

    def test_pede_icone_por_linha(self):
        p = self._prompt()
        assert "illustrated line icon" in p

    def test_proibe_cabecalho_explicitamente(self):
        """Tirar a linha do titulo sem PROIBIR cabecalho convida o modelo a
        inventar um para preencher o topo do cartao."""
        p = self._prompt()
        assert "do not render a heading, title or caption" in p

    def test_nao_renderiza_titulo_fixo(self):
        from app.services.image_card_copy_service import SPECS_CARD_TITLE

        assert SPECS_CARD_TITLE.lower() not in self._prompt()

    def test_card_pillow_mantem_o_titulo(self):
        """A remocao vale so para o prompt da IA. `build_specs_card` continua
        devolvendo `title`, que e o cabecalho do card Pillow."""
        from app.services.image_card_copy_service import (
            SPECS_CARD_TITLE,
            build_specs_card,
        )

        assert build_specs_card(_sku37_attributes()).title == SPECS_CARD_TITLE

    def test_mantem_os_bullets_verbatim(self):
        p = self._prompt()
        assert "verbatim" in p
        assert "do not translate" in p
        assert "marca: wepink" in p

    def test_mantem_a_clausula_critical_do_texto_impresso(self):
        from app.services.specs_variant_service import _build_specs_prompt

        p = _build_specs_prompt(["a", "b"])
        assert "CRITICAL" in p
        assert "character for" in p
        assert "Never change a number or a unit." in p

    def test_proibe_alterar_a_identidade_do_produto(self):
        p = self._prompt()
        assert "do not reshape" in p
        assert "watermark" in p or "badge" in p

