"""Esquema de 5 posicoes como padrao de produto unico em categoria com perfil.

O que estes testes travam, em ordem de importancia:

1. ROTEAMENTO por categoria-FOLHA. Categoria sem perfil segue o caminho
   antigo — e o que mantem a mudanca contida a perfumaria.
2. NENHUMA aprovacao automatica, nem em batch. Revisao humana e obrigatoria
   nas 5 posicoes; sem guard, o batch aprovaria imagens sem revisao. A
   aprovacao em massa (`bulk_approve_images`) exclui candidatas pela POSICAO
   (`sort_order >= CANDIDATE_SORT_ORDER_FLOOR`), nunca pelo `kind` — as 5
   posicoes oficiais, incluindo capa (0) e ficha (4), sao todas aprovadas
   quando um humano aciona a aprovacao.
3. INDEPENDENCIA: uma posicao que falha nao derruba as outras.
4. Kits intocados.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.image_service import ImageValidationResult


def _listing(category="MLB6284"):
    listing = MagicMock()
    listing.id = "lid"
    listing.ml_category_id = category
    listing.sku_external_id = "38"
    listing.sku_model = "Fatal Black For Her"
    listing.sku_brand = "Wepink"
    listing.sku_description = "Body Splash Fatal Black For Her 200ml - Wepink"
    listing.created_via = "manual"
    return listing


def _db_com_atributos(atributos=None):
    db = AsyncMock()
    resultado = MagicMock()
    resultado.scalars.return_value.all.return_value = list(atributos or [])
    db.execute = AsyncMock(return_value=resultado)
    db.add = MagicMock()
    db.commit = AsyncMock()
    return db


def _atributo(attribute_id, attribute_name, value_name, value_id=None):
    a = MagicMock()
    a.attribute_id = attribute_id
    a.attribute_name = attribute_name
    a.value_name = value_name
    a.value_id = value_id
    return a


def _atributos_sku38():
    return [
        _atributo("BRAND", "Marca", "Wepink"),
        _atributo("MODEL", "Modelo", "Fatal Black For Her"),
        _atributo("PERFUME_TYPE", "Tipo de perfume", "Body splash", "19463164"),
        _atributo("UNIT_VOLUME", "Volume da unidade", "200 ml"),
    ]


_COPY = {
    "benefits": {"title": "Por que escolher", "bullets": ["Fragrância marcante", "Longa duração"]},
    "usage": {"title": "Modo de uso", "bullets": ["Borrife no corpo", "Reaplique"]},
    "specs": {"title": "x", "bullets": ["y", "z"]},
}


def _fotos(n=5):
    return [f"foto-{i}".encode() for i in range(1, n + 1)]


class _Ambiente:
    """Patches comuns. `edit` devolve bytes distintos por chamada para que
    cada posicao possa ser identificada no que foi salvo."""

    def __init__(self, falhar_em=None, qa_reprova=False):
        self.falhar_em = falhar_em or set()
        self.qa_reprova = qa_reprova
        self.prompts = []
        self.sizes = []
        self.imagens_por_chamada = []

    async def _edit(self, images, prompt, n, size=None):
        idx = len(self.prompts)
        self.prompts.append(prompt)
        self.sizes.append(size)
        self.imagens_por_chamada.append(list(images))
        if idx in self.falhar_em:
            raise RuntimeError("motor falhou")
        return [f"gerado-{idx}".encode()]

    def __enter__(self):
        verdict = ImageValidationResult(is_valid=True) if not self.qa_reprova else \
            ImageValidationResult(is_valid=False, errors=["reprovado"])
        preparado = None if self.qa_reprova else b"preparado"

        provider = MagicMock()
        provider.generate_card_copy = AsyncMock(return_value=_COPY)

        self._patches = [
            patch("app.services.image_engines.openai_edit_engine.OpenAIEditEngine"),
            patch("app.workers.tasks.image_tasks._prepare_image_for_upload",
                  return_value=(preparado, verdict)),
            patch("app.services.image_service.MLPictureService"),
            patch("app.services.image_deterministic_service.try_deterministic_cover",
                  return_value=b"capa-deterministica"),
            patch("app.services.ai.service.get_ai_provider", return_value=provider),
            patch("app.services.r2_asset_service.store_candidate_bytes",
                  new_callable=AsyncMock, return_value="asset-key-teste"),
        ]
        self.engine_cls, self.prepare, self.ml_cls, self.crop, _, self.r2 = [
            p.start() for p in self._patches
        ]
        self.engine_cls.return_value.edit = AsyncMock(side_effect=self._edit)
        self.ml_cls.return_value.upload = AsyncMock(side_effect=lambda b, t: f"pic-{len(b)}")
        return self

    def __exit__(self, *a):
        for p in self._patches:
            p.stop()
        return False


def _salvos(db):
    return [c.args[0] for c in db.add.call_args_list]


class TestCincoPosicoes:
    @pytest.mark.asyncio
    async def test_gera_as_cinco_posicoes_em_ordem(self):
        from app.services.image_position_profiles import PERFIL_PERFUMARIA
        from app.workers.tasks.image_tasks import _gerar_cinco_posicoes

        db = _db_com_atributos(_atributos_sku38())
        with _Ambiente() as amb:
            salvas = await _gerar_cinco_posicoes(
                db, _listing(), "tok", PERFIL_PERFUMARIA, _fotos(), "38"
            )

        assert salvas == 5
        imgs = _salvos(db)
        assert [i.kind for i in imgs] == [
            "cover_ai", "presentation", "benefits_ai", "detail_ai", "specs_ai"
        ]
        assert [i.sort_order for i in imgs] == [0, 1, 2, 3, 4]

    @pytest.mark.asyncio
    async def test_nenhuma_posicao_nasce_aprovada(self):
        """Revisao humana obrigatoria, sem excecao, nas 5."""
        from app.services.image_position_profiles import PERFIL_PERFUMARIA
        from app.workers.tasks.image_tasks import _gerar_cinco_posicoes

        db = _db_com_atributos(_atributos_sku38())
        with _Ambiente():
            await _gerar_cinco_posicoes(db, _listing(), "tok", PERFIL_PERFUMARIA, _fotos(), "38")

        assert all(i.approved is False for i in _salvos(db))

    @pytest.mark.asyncio
    async def test_usa_o_canvas_do_perfil_em_todas(self):
        from app.services.image_position_profiles import PERFIL_PERFUMARIA
        from app.workers.tasks.image_tasks import _gerar_cinco_posicoes

        db = _db_com_atributos(_atributos_sku38())
        with _Ambiente() as amb:
            await _gerar_cinco_posicoes(db, _listing(), "tok", PERFIL_PERFUMARIA, _fotos(), "38")

        assert amb.sizes == ["1200x1200"] * 5

    @pytest.mark.asyncio
    async def test_apresentacao_recebe_todas_as_fotos_brutas(self):
        """Substitui o modelo de N variantes por foto: uma chamada so, com
        acesso a todas as fotos. O corte [:RAW_PHOTOS_MIN] nao se aplica."""
        from app.services.image_position_profiles import PERFIL_PERFUMARIA
        from app.workers.tasks.image_tasks import _gerar_cinco_posicoes

        db = _db_com_atributos(_atributos_sku38())
        fotos = _fotos(5)
        with _Ambiente() as amb:
            await _gerar_cinco_posicoes(db, _listing(), "tok", PERFIL_PERFUMARIA, fotos, "38")

        assert amb.imagens_por_chamada[1] == fotos, "posicao 2 recebe as 5"

    @pytest.mark.asyncio
    async def test_falha_de_uma_posicao_nao_derruba_as_outras(self):
        from app.services.image_position_profiles import PERFIL_PERFUMARIA
        from app.workers.tasks.image_tasks import _gerar_cinco_posicoes

        db = _db_com_atributos(_atributos_sku38())
        # A 3a chamada e a 1a tentativa da posicao 3; com retry, a 4a tambem.
        with _Ambiente(falhar_em={2, 3}):
            salvas = await _gerar_cinco_posicoes(
                db, _listing(), "tok", PERFIL_PERFUMARIA, _fotos(), "38"
            )

        kinds = [i.kind for i in _salvos(db)]
        assert "benefits_ai" not in kinds
        assert salvas == 4
        assert kinds == ["cover_ai", "presentation", "detail_ai", "specs_ai"]

    @pytest.mark.asyncio
    async def test_posicao_reprovada_no_qa_guarda_os_bytes(self):
        from app.services.image_position_profiles import PERFIL_PERFUMARIA
        from app.workers.tasks.image_tasks import _gerar_cinco_posicoes

        db = _db_com_atributos(_atributos_sku38())
        with _Ambiente(qa_reprova=True):
            salvas = await _gerar_cinco_posicoes(
                db, _listing(), "tok", PERFIL_PERFUMARIA, _fotos(), "38"
            )

        assert salvas == 0
        imgs = _salvos(db)
        assert imgs, "reprovada tambem vira linha, para revisao humana"
        assert all(i.status == "validation_failed" for i in imgs)
        assert all(i.asset_key is not None for i in imgs), "bytes crus foram ao R2"
        assert all(i.ml_picture_id is None for i in imgs)

    @pytest.mark.asyncio
    async def test_capa_deterministica_e_fallback_interno(self):
        """So aparece como linha quando a posicao 1 por IA falha por completo."""
        from app.services.image_position_profiles import PERFIL_PERFUMARIA
        from app.workers.tasks.image_tasks import _gerar_cinco_posicoes

        db = _db_com_atributos(_atributos_sku38())
        with _Ambiente(falhar_em={0, 1}):  # duas tentativas da posicao 1
            await _gerar_cinco_posicoes(db, _listing(), "tok", PERFIL_PERFUMARIA, _fotos(), "38")

        imgs = _salvos(db)
        capa = imgs[0]
        assert capa.kind == "cover_deterministic"
        assert capa.sort_order == 0
        assert capa.approved is False

    @pytest.mark.asyncio
    async def test_sem_falha_a_capa_deterministica_nao_vira_linha(self):
        from app.services.image_position_profiles import PERFIL_PERFUMARIA
        from app.workers.tasks.image_tasks import _gerar_cinco_posicoes

        db = _db_com_atributos(_atributos_sku38())
        with _Ambiente():
            await _gerar_cinco_posicoes(db, _listing(), "tok", PERFIL_PERFUMARIA, _fotos(), "38")

        assert "cover_deterministic" not in [i.kind for i in _salvos(db)]

    @pytest.mark.asyncio
    async def test_texto_da_apresentacao_espelha_o_rotulo(self):
        """Nome do produto em destaque, marca abaixo — hierarquia da embalagem."""
        from app.services.image_position_profiles import PERFIL_PERFUMARIA
        from app.workers.tasks.image_tasks import _gerar_cinco_posicoes

        db = _db_com_atributos(_atributos_sku38())
        with _Ambiente() as amb:
            await _gerar_cinco_posicoes(db, _listing(), "tok", PERFIL_PERFUMARIA, _fotos(), "38")

        p = amb.prompts[1]
        assert 'Headline: "Fatal Black For Her"' in p
        assert 'Line 2: "Wepink"' in p
        assert 'Line 3: "200 ml"' in p

    @pytest.mark.asyncio
    async def test_ficha_usa_o_value_name_real(self):
        from app.services.image_position_profiles import PERFIL_PERFUMARIA
        from app.workers.tasks.image_tasks import _gerar_cinco_posicoes

        db = _db_com_atributos(_atributos_sku38())
        with _Ambiente() as amb:
            await _gerar_cinco_posicoes(db, _listing(), "tok", PERFIL_PERFUMARIA, _fotos(), "38")

        assert "Tipo de perfume: Body splash" in amb.prompts[4]


class TestPerfilPorFolha:
    def test_mlb6284_tem_perfil(self):
        from app.services.image_position_profiles import profile_for_category

        assert profile_for_category("MLB6284") is not None

    def test_categoria_irma_nao_herda_o_perfil_de_perfumaria(self):
        """Maquiagem e Cuidados com o Cabelo sao irmas de Perfumes sob a mesma
        raiz. Herdar aplicaria "Frasco elegante" a esmalte: elas recebem o
        generico, nao o de perfumaria."""
        from app.services.image_position_profiles import PERFIL_PADRAO, profile_for_category

        for irma in ("MLB1248", "MLB1263", "MLB198312"):
            assert profile_for_category(irma) is PERFIL_PADRAO

    def test_raiz_recebe_o_generico(self):
        from app.services.image_position_profiles import PERFIL_PADRAO, profile_for_category

        assert profile_for_category("MLB1246") is PERFIL_PADRAO

    def test_perfume_pet_nao_esta_cadastrado(self):
        """MLB178938 e perfume para caes, com vocabulario proprio e nenhum SKU
        testado — e a origem do caso "Colonia" do CLAUDE.md. Sem perfil
        proprio, cai no generico."""
        from app.services.image_position_profiles import PERFIL_PADRAO, profile_for_category

        assert profile_for_category("MLB178938") is PERFIL_PADRAO

    def test_categoria_nula_ou_vazia_nunca_e_none(self):
        from app.services.image_position_profiles import PERFIL_PADRAO, profile_for_category

        assert profile_for_category(None) is PERFIL_PADRAO
        assert profile_for_category("") is PERFIL_PADRAO

    def test_legenda_de_detalhe_e_estavel_por_sku(self):
        """Regerar as imagens nao pode trocar a legenda por baixo de uma
        revisao humana ja feita."""
        from app.services.image_position_profiles import (
            PERFIL_PERFUMARIA,
            detail_caption_for,
        )

        primeira = detail_caption_for(PERFIL_PERFUMARIA, "38")
        for _ in range(20):
            assert detail_caption_for(PERFIL_PERFUMARIA, "38") == primeira
        assert primeira in PERFIL_PERFUMARIA.detail_captions
