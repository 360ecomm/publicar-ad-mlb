"""PERFIL_PADRAO: categoria sem perfil proprio cai no esquema de 5 posicoes.

Antes, `profile_for_category` devolvia None para categoria sem perfil e isso
acionava o pipeline antigo (individuais + cards Pillow), que em LOTE
auto-aprovava e publicava. Decisao de 2026-09-10: nunca mais None. Categoria
sem perfil especifico usa um perfil generico — mesmo canvas 1200x1200
(recomendacao do ML para a maioria das categorias) e legendas de detalhe
neutras, sem linguagem de perfume. O caminho antigo foi removido.

`PROFILES_BY_LEAF_CATEGORY` continua existindo para perfis lapidados por
vertical (MLB6284 → Perfumaria); o generico e' o fallback, nao o teto.
"""
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@asynccontextmanager
async def _sessao(db):
    yield db


class TestPerfilPadrao:
    def test_categoria_desconhecida_recebe_o_perfil_padrao(self):
        from app.services.image_position_profiles import PERFIL_PADRAO, profile_for_category

        assert profile_for_category("MLB7863") is PERFIL_PADRAO       # Farois Dianteiros
        assert profile_for_category("MLB40588") is PERFIL_PADRAO      # Colonias (Bebes)
        assert profile_for_category("MLB1246") is PERFIL_PADRAO       # raiz de Beleza

    def test_nunca_devolve_none(self):
        from app.services.image_position_profiles import PERFIL_PADRAO, profile_for_category

        assert profile_for_category(None) is PERFIL_PADRAO
        assert profile_for_category("") is PERFIL_PADRAO

    def test_mlb6284_continua_com_perfumaria(self):
        from app.services.image_position_profiles import (
            PERFIL_PADRAO, PERFIL_PERFUMARIA, PROFILES_BY_LEAF_CATEGORY, profile_for_category,
        )

        assert profile_for_category("MLB6284") is PERFIL_PERFUMARIA
        assert PROFILES_BY_LEAF_CATEGORY == {"MLB6284": PERFIL_PERFUMARIA}
        assert PERFIL_PADRAO is not PERFIL_PERFUMARIA

    def test_canvas_quadrado_e_legendas_neutras(self):
        from app.services.image_position_profiles import CANVAS_QUADRADO, PERFIL_PADRAO, PERFIL_PERFUMARIA

        assert PERFIL_PADRAO.canvas == CANVAS_QUADRADO == "1200x1200"
        assert len(PERFIL_PADRAO.detail_captions) >= 3
        for legenda in PERFIL_PADRAO.detail_captions:
            assert legenda not in PERFIL_PERFUMARIA.detail_captions
            for termo in ("frasco", "perfum", "fragr", "colônia"):
                assert termo not in legenda.lower(), legenda

    def test_legenda_de_detalhe_estavel_por_sku(self):
        from app.services.image_position_profiles import PERFIL_PADRAO, detail_caption_for

        assert {detail_caption_for(PERFIL_PADRAO, "FAROL-01") for _ in range(50)} == {detail_caption_for(PERFIL_PADRAO, "FAROL-01")}
        assert detail_caption_for(PERFIL_PADRAO, "FAROL-01") in PERFIL_PADRAO.detail_captions


class TestRoteamentoSemPerfilEspecifico:
    @pytest.mark.asyncio
    async def test_categoria_desconhecida_gera_as_cinco_posicoes_com_o_padrao(self):
        from app.services.image_position_profiles import PERFIL_PADRAO
        from app.workers.tasks.image_tasks import _try_i2i_generation

        listing = MagicMock()
        listing.id = "lid"; listing.seller_id = "sid"; listing.sku_external_id = "FAROL-01"
        listing.ml_category_id = "MLB7863"
        cfg = MagicMock(); cfg.raw_base_url = "https://b/x"
        db = AsyncMock()
        db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=cfg)))

        with patch("app.services.seller_image_source_service.fetch_all_raw_photos",
                   new_callable=AsyncMock, return_value={"FAROL-01": [b"1", b"2"]}), \
             patch("app.workers.tasks.image_tasks._gerar_cinco_posicoes",
                   new_callable=AsyncMock, return_value=5) as cinco:
            saved = await _try_i2i_generation(db, listing, MagicMock(), "tok")

        assert saved == 5
        cinco.assert_awaited_once()
        assert cinco.await_args.args[3] is PERFIL_PADRAO
        assert cinco.await_args.args[4] == [b"1", b"2"] and cinco.await_args.args[5] == "FAROL-01"

    @pytest.mark.asyncio
    async def test_lote_em_categoria_desconhecida_nunca_auto_aprova(self):
        """O bloco de auto-aprovacao em lote do caminho antigo nao existe mais:
        qualquer categoria para em pending_image_approval."""
        from app.workers.tasks.image_tasks import _generate_images_async

        listing = MagicMock()
        listing.id = "lid"; listing.status = "generating_images"; listing.sku_external_id = "FAROL-01"
        listing.seller_id = "sid"; listing.created_via = "batch"; listing.ml_category_id = "MLB7863"
        db = AsyncMock()
        db.execute = AsyncMock(return_value=MagicMock(scalar_one=MagicMock(return_value=listing)))
        db.add = MagicMock(); db.commit = AsyncMock()

        with patch("app.database.worker_session", lambda: _sessao(db)), \
             patch("app.workers.tasks.image_tasks._fetch_upload_token", new_callable=AsyncMock, return_value="tok"), \
             patch("app.workers.tasks.image_tasks._try_i2i_generation", new_callable=AsyncMock, return_value=5):
            result = await _generate_images_async("lid")

        assert listing.status == "pending_image_approval"
        assert result == {"listing_id": "lid", "images_saved": 5, "source": "i2i"}
        # Nenhuma consulta de aprovacao em massa: so SELECT Listing + SELECT Seller.
        assert db.execute.await_count == 2
