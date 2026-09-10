"""Placeholder "Sem marca" nunca chega a prompt nem a imagem.

Caso real (SKU 45, farol, 2026-09-10): o produto nao tem marca no catalogo,
`batch_tasks` gravava `sku_brand="Sem marca"` no listing, e a posicao 1
(apresentacao) imprimiu "Sem marca" como segunda linha do card. O
`category_service` ja filtrava o placeholder antes de mandar BRAND ao ML — o
conhecimento existia, mas so num consumidor.

Correcao na fonte + defesa no uso: o lote grava vazio quando nao ha marca, e
UM helper (`real_brand`) decide o que e' marca real para todos os
consumidores — prefill de atributo, prompt de apresentacao e prompts de
texto (titulo, descricao, copy dos cards), que omitem a linha inteira em vez
de imprimir "Marca:" vazio ou placeholder.
"""
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestRealBrand:
    def test_placeholders_e_vazios_viram_none(self):
        from app.services.brand_field import real_brand

        for v in (None, "", "   ", "Sem marca", "SEM MARCA", " sem marca ", "N/A", "-"):
            assert real_brand(v) is None, repr(v)

    def test_marca_real_passa_limpa(self):
        from app.services.brand_field import real_brand

        assert real_brand("  Wepink ") == "Wepink"
        assert real_brand("Arteb") == "Arteb"


@asynccontextmanager
async def _sessao(db):
    yield db


class TestLoteNaoGravaPlaceholder:
    @pytest.mark.asyncio
    async def test_produto_sem_marca_vira_sku_brand_vazio(self):
        from app.workers.tasks.batch_tasks import _process_batch_async

        batch = MagicMock(); batch.id = "bid"; batch.seller_id = "sid"; batch.created_by = "uid"
        row = MagicMock(); row.raw_data = {"sku": "45", "preco": "289,90"}; row.status = "pending"
        product = MagicMock(); product.id = "pid"; product.sku = "45"; product.description = "Farol"
        product.brand = None; product.model = None; product.ean = None
        product.weight_kg = product.length_cm = product.width_cm = product.height_cm = None

        n = [0]

        async def execute(stmt):
            n[0] += 1
            r = MagicMock()
            r.scalar_one = MagicMock(return_value=batch)
            r.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=[row])))
            r.scalar_one_or_none = MagicMock(return_value=product)
            return r

        db = AsyncMock(); db.execute = execute; db.add = MagicMock(); db.commit = AsyncMock(); db.flush = AsyncMock()

        with patch("app.database.worker_session", lambda: _sessao(db)), \
             patch("app.workers.tasks.ai_tasks.generate_title") as gt:
            await _process_batch_async("bid")

        listings = [c.args[0] for c in db.add.call_args_list if c.args[0].__class__.__name__ == "Listing"]
        assert len(listings) == 1
        assert listings[0].sku_brand == ""
        gt.apply_async.assert_called_once()

    @pytest.mark.asyncio
    async def test_marca_placeholder_no_catalogo_tambem_vira_vazio(self):
        """Quem digitou "Sem marca" no catalogo nao pode reintroduzir o bug."""
        from app.workers.tasks.batch_tasks import _process_batch_async

        batch = MagicMock(); batch.id = "bid"; batch.seller_id = "sid"; batch.created_by = "uid"
        row = MagicMock(); row.raw_data = {"sku": "45", "preco": "10"}; row.status = "pending"
        product = MagicMock(); product.id = "pid"; product.sku = "45"; product.description = "x"
        product.brand = "Sem marca"; product.model = None; product.ean = None
        product.weight_kg = product.length_cm = product.width_cm = product.height_cm = None

        async def execute(stmt):
            r = MagicMock()
            r.scalar_one = MagicMock(return_value=batch)
            r.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=[row])))
            r.scalar_one_or_none = MagicMock(return_value=product)
            return r

        db = AsyncMock(); db.execute = execute; db.add = MagicMock(); db.commit = AsyncMock(); db.flush = AsyncMock()
        with patch("app.database.worker_session", lambda: _sessao(db)), \
             patch("app.workers.tasks.ai_tasks.generate_title"):
            await _process_batch_async("bid")

        listings = [c.args[0] for c in db.add.call_args_list if c.args[0].__class__.__name__ == "Listing"]
        assert listings[0].sku_brand == ""


class TestApresentacaoSemMarca:
    @pytest.mark.asyncio
    async def test_campos_da_posicao_2_omitem_marca_placeholder(self):
        """Defesa no ponto de uso: mesmo que um listing antigo ainda carregue
        "Sem marca", a apresentacao nao imprime."""
        from app.workers.tasks.image_tasks import _campos_das_posicoes

        listing = MagicMock(); listing.id = "lid"; listing.sku_brand = "Sem marca"
        listing.sku_model = "Farol Polo"; listing.sku_description = "Farol Polo Classic"
        db = AsyncMock()
        db.execute = AsyncMock(return_value=MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))))

        with patch("app.services.image_card_copy_service.generate_card_copy", new_callable=AsyncMock, return_value=[]), \
             patch("app.services.image_card_copy_service.build_specs_card", return_value=None):
            campos = await _campos_das_posicoes(db, listing)

        assert campos["marca"] is None

    def test_prompt_de_apresentacao_sem_linha_2(self):
        from app.services.image_position_prompts import build_presentation_prompt

        p = build_presentation_prompt("Farol Polo Classic", None, None)
        assert "Line 2" not in p and "Sem marca" not in p and '""' not in p


class TestPromptsDeTextoOmitemMarca:
    def test_titulo_sem_marca_nao_tem_linha_marca_nem_placeholder(self):
        from app.services.ai.prompts import build_title_prompt

        for marca in ("", "Sem marca"):
            p = build_title_prompt("Farol Polo Classic", marca, "new", batch_mode=True)
            bloco = p.split("PRODUTO:")[1]
            assert "Marca:" not in bloco, marca
            assert "Sem marca" not in p, marca

    def test_titulo_com_marca_mantem_a_linha(self):
        from app.services.ai.prompts import build_title_prompt

        p = build_title_prompt("Martin Colônia", "Wepink", "new", batch_mode=True)
        assert "Marca: Wepink" in p

    def test_descricao_e_copy_omitem_marca_vazia(self):
        from app.services.ai.prompts import build_card_copy_prompt, build_description_prompt

        d = build_description_prompt({"selected_title": "t", "sku_brand": "", "sku_description": "d", "condition": "new", "attributes": []})
        c = build_card_copy_prompt({"selected_title": "t", "sku_brand": "Sem marca", "sku_description": "d", "attributes": []})
        assert "Marca:" not in d and "Marca:" not in c and "Sem marca" not in c


class TestPrefillUsaOMesmoHelper:
    @pytest.mark.asyncio
    async def test_brand_placeholder_nao_e_prefilled(self):
        from app.services.category_service import CategoryService

        listing = MagicMock(); listing.id = "lid"; listing.ml_category_id = "MLB7863"; listing.condition = "new"
        listing.sku_brand = "Sem marca"; listing.sku_model = None; listing.sku_external_id = "45"
        listing.package_weight_kg = listing.package_length_cm = listing.package_width_cm = listing.package_height_cm = None
        db = AsyncMock(); db.add = MagicMock(); db.execute = AsyncMock(); db.commit = AsyncMock()

        with patch("app.services.category_service.real_brand", return_value=None) as rb:
            await CategoryService(db)._save_attributes(
                listing, [{"id": "BRAND", "name": "Marca", "value_type": "string", "tags": {"required": True}}]
            )

        rb.assert_called_once_with("Sem marca")
        brand = [c.args[0] for c in db.add.call_args_list][0]
        assert brand.value_name is None and listing.status == "pending_seller_attributes"
