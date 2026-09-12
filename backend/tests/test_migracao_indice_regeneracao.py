"""Migracao a1d7c3e9f5b2: indice unico parcial
`uq_listing_images_generating_slot (listing_id, sort_order) WHERE status = 'generating'`.

E' a trava da regeneracao de UMA posicao (spec 2026-09-12-regenerar-posicao,
decisao 2): o endpoint insere um placeholder `generating` e faz commit ANTES
de enfileirar; o segundo clique falha no commit (IntegrityError) e vira 409.
Mesma tecnica dos slots de capa/ficha (3d8f1b2c9e47), provada em producao.

Padrao de `test_migracao_indices_fk.py`: `create_all` ja cria o indice pelo
model, entao downgrade remove, upgrade recria, e o DDL dos dois lados tem de
ser identico. Postgres REAL, so no banco dedicado `publicar_test`.
"""
import os

import pytest
from sqlalchemy.exc import IntegrityError

from tests.test_migracao_indice_listagem import (
    _carregar_migracao,
    _indexdef,
    _indices,
    _preparar_banco,
    _rodar_op,
)

TEST_DB = os.environ.get("TEST_DATABASE_URL")
_precisa_db = pytest.mark.skipif(
    not TEST_DB, reason="TEST_DATABASE_URL nao definido: teste real so roda com Postgres dedicado"
)

REVISION = "a1d7c3e9f5b2"
INDICE = "uq_listing_images_generating_slot"
INTOCADOS = {
    "uq_listing_images_cover_slot",
    "uq_listing_images_specs_slot",
    "ix_listing_images_listing_id",
}


def test_revisao_encadeia_no_head_atual():
    mod = _carregar_migracao(REVISION)
    assert mod.down_revision == "f7b3e9c1d2a5"


def test_vocabulario_do_model():
    from app.models.listing_image import (
        COVER_AI_KIND,
        GENERATING_STATUS,
        GENERATION_FAILED_STATUS,
        POSITION_KINDS,
        SPECS_AI_KIND,
    )
    assert GENERATING_STATUS == "generating"
    assert GENERATION_FAILED_STATUS == "generation_failed"
    assert POSITION_KINDS == {
        0: COVER_AI_KIND, 1: "presentation_ai", 2: "benefits_ai", 3: "detail_ai", 4: SPECS_AI_KIND,
    }


@_precisa_db
class TestIndiceDoPlaceholder:
    @pytest.mark.asyncio
    async def test_downgrade_remove_e_upgrade_recria_sem_tocar_nos_outros(self):
        mig = _carregar_migracao(REVISION)
        engine, _ = await _preparar_banco()
        try:
            async with engine.begin() as conn:
                antes = await conn.run_sync(_indices, "listing_images")
            assert INDICE in antes and INTOCADOS <= antes

            async with engine.begin() as conn:
                await conn.run_sync(_rodar_op, mig.downgrade)
                depois_down = await conn.run_sync(_indices, "listing_images")
            assert INDICE not in depois_down and INTOCADOS <= depois_down

            async with engine.begin() as conn:
                await conn.run_sync(_rodar_op, mig.upgrade)
                depois_up = await conn.run_sync(_indices, "listing_images")
            assert INDICE in depois_up and INTOCADOS <= depois_up
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_ddl_unico_parcial_e_identico_ao_do_model(self):
        mig = _carregar_migracao(REVISION)
        engine, _ = await _preparar_banco()
        try:
            async with engine.begin() as conn:
                do_model = await conn.run_sync(_indexdef, INDICE)
                await conn.run_sync(_rodar_op, mig.downgrade)
                await conn.run_sync(_rodar_op, mig.upgrade)
                da_migracao = await conn.run_sync(_indexdef, INDICE)
            assert da_migracao is not None
            assert da_migracao.startswith("CREATE UNIQUE INDEX"), da_migracao
            assert "(listing_id, sort_order)" in da_migracao, da_migracao
            assert " WHERE " in da_migracao and "'generating'" in da_migracao, da_migracao
            assert da_migracao == do_model, (da_migracao, do_model)
        finally:
            await engine.dispose()


@_precisa_db
class TestUnicidadeReal:
    """`_semear` de test_bulk_approve_por_posicao: 1 listing em
    pending_image_approval + as linhas `(kind, sort_order, ml_picture_id, status)`."""

    @pytest.mark.asyncio
    async def test_dois_placeholders_na_mesma_posicao_colidem(self):
        from tests.test_bulk_approve_por_posicao import _preparar_banco as _banco, _semear

        engine, sm = await _banco()
        try:
            with pytest.raises(IntegrityError):
                await _semear(sm, [
                    ("benefits_ai", 2, None, "generating"),
                    ("benefits_ai", 2, None, "generating"),
                ])
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_placeholder_convive_com_linha_uploaded_e_com_outra_posicao(self):
        from sqlalchemy import select

        from app.models.listing_image import ListingImage
        from tests.test_bulk_approve_por_posicao import _preparar_banco as _banco, _semear

        engine, sm = await _banco()
        try:
            listing_id, _, _ = await _semear(sm, [
                ("benefits_ai", 2, "p2", "uploaded"),
                ("benefits_ai", 2, None, "generating"),
                ("detail_ai", 3, None, "generating"),
            ])
            async with sm() as s:
                total = len((await s.execute(
                    select(ListingImage).where(ListingImage.listing_id == listing_id)
                )).scalars().all())
            assert total == 3
        finally:
            await engine.dispose()
