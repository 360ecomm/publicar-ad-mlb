"""Imagem aprovada de outro anuncio do mesmo SKU NUNCA e' reusada.

Antes, `_generate_images_async` olhava `ProductImage.is_approved` para o
(seller, sku) e, se achasse algo, copiava os `ml_picture_id` para o listing
novo, marcava tudo `approved=True` e pulava direto para
`generating_description` (lote) — sem gerar nada, sem passar pelo esquema
de 5 posicoes, sem o guard de revisao humana. Um segundo anuncio de um SKU
ja processado seria publicado com fotos de outro anuncio e sem os cards.
Decisao de 2026-09-10: remover o caminho por completo, nao desligar por flag.

`ProductImage.is_approved` continua existindo e sendo ESCRITO (criacao com
False, auto-aprovacao em lote, `approve_images`): so a leitura que causava o
reuso saiu. O indice SKU→imagem segue como registro, nao como atalho.
"""
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@asynccontextmanager
async def _sessao(db):
    yield db


def _listing(created_via="batch"):
    listing = MagicMock()
    listing.id = "lid"
    listing.status = "generating_images"
    listing.sku_external_id = "38"          # SKU que JA tem ProductImage aprovada
    listing.seller_id = "sid"
    listing.created_via = created_via
    listing.ml_category_id = "MLB6284"
    return listing


def _db_com_product_image_aprovada(listing):
    """Toda consulta que devolva lista traz 1 ProductImage aprovada — se o
    codigo ainda perguntar por ela, vai encontra-la."""
    aprovada = MagicMock()
    aprovada.ml_picture_id = "pic-de-outro-anuncio"
    aprovada.is_approved = True

    db = AsyncMock()
    n = [0]

    async def execute(stmt):
        n[0] += 1
        r = MagicMock()
        r.scalar_one = MagicMock(return_value=listing if n[0] == 1 else MagicMock())
        r.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=[aprovada])))
        return r

    db.execute = execute
    db.add = MagicMock()
    db.commit = AsyncMock()
    return db


class TestSemReusoDeImagemAprovada:
    @pytest.mark.asyncio
    async def test_sku_ja_processado_gera_de_novo_em_vez_de_reusar(self):
        from app.workers.tasks.image_tasks import _generate_images_async

        listing = _listing()
        db = _db_com_product_image_aprovada(listing)

        with patch("app.database.worker_session", lambda: _sessao(db)), \
             patch("app.workers.tasks.image_tasks._fetch_upload_token", new_callable=AsyncMock, return_value="tok"), \
             patch("app.workers.tasks.image_tasks._try_i2i_generation", new_callable=AsyncMock, return_value=4) as i2i:
            result = await _generate_images_async("lid")

        i2i.assert_awaited_once()
        assert "images_reused" not in result
        assert result["source"] == "i2i" and result["images_saved"] == 4

    @pytest.mark.asyncio
    async def test_nenhuma_listing_image_copiada_do_indice(self):
        """Nada e' gravado a partir do ml_picture_id de outro anuncio."""
        from app.workers.tasks.image_tasks import _generate_images_async

        listing = _listing()
        db = _db_com_product_image_aprovada(listing)

        with patch("app.database.worker_session", lambda: _sessao(db)), \
             patch("app.workers.tasks.image_tasks._fetch_upload_token", new_callable=AsyncMock, return_value="tok"), \
             patch("app.workers.tasks.image_tasks._try_i2i_generation", new_callable=AsyncMock, return_value=4):
            await _generate_images_async("lid")

        copiadas = [c for c in db.add.call_args_list
                    if getattr(c.args[0], "ml_picture_id", None) == "pic-de-outro-anuncio"]
        assert copiadas == []

    @pytest.mark.asyncio
    async def test_lote_com_perfil_continua_parando_em_revisao(self):
        """O caminho novo herda o guard das 5 posicoes: nada aprovado sozinho."""
        from app.workers.tasks.image_tasks import _generate_images_async

        listing = _listing(created_via="batch")
        db = _db_com_product_image_aprovada(listing)

        with patch("app.database.worker_session", lambda: _sessao(db)), \
             patch("app.workers.tasks.image_tasks._fetch_upload_token", new_callable=AsyncMock, return_value="tok"), \
             patch("app.workers.tasks.image_tasks._try_i2i_generation", new_callable=AsyncMock, return_value=4):
            await _generate_images_async("lid")

        assert listing.status == "pending_image_approval"
