"""Migracao f7b3e9c1d2a5: indices nas tres FKs `listing_id` que tinham
consulta real e nenhum indice util — `listing_images`, `listing_titles` e
`listing_jobs`.

`listing_images` ja tinha dois indices em `listing_id`, mas PARCIAIS (slots
de capa e ficha), que nao servem para "todas as imagens deste anuncio": a
subconsulta de `approved_image_count` fazia Seq Scan de 16k linhas 200 vezes
por pagina (153 ms; com o indice, 1,2 ms). `listing_titles` e lida em 5
pontos (detalhe, select_title, aprovacao em massa, retry). `listing_jobs` so
no detalhe, mas so cresce (nada apaga job) e recebe INSERT num unico ponto.

Ruling do teste (mesmo padrao de `test_migracao_indice_listagem.py`):
`Base.metadata.create_all` ja cria os indices no estado atual dos models
(`__table_args__`), entao `downgrade` remove os tres, `upgrade` recria e
`downgrade` remove de novo — reversivel de verdade. Os dois indices parciais
de `listing_images` NAO podem ser tocados pela migracao.

Roda contra Postgres REAL, so no banco dedicado `publicar_test`
(`_pg_dedicado` trava qualquer outro nome antes de abrir conexao).
"""
import os

import pytest

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

REVISION = "f7b3e9c1d2a5"
INDICES = {
    "listing_images": "ix_listing_images_listing_id",
    "listing_titles": "ix_listing_titles_listing_id",
    "listing_jobs": "ix_listing_jobs_listing_id",
}
PARCIAIS_INTOCADOS = {"uq_listing_images_cover_slot", "uq_listing_images_specs_slot"}


async def _indices_das_tres(engine):
    async with engine.begin() as conn:
        return {tabela: await conn.run_sync(_indices, tabela) for tabela in INDICES}


@_precisa_db
class TestIndicesDeChaveEstrangeira:
    @pytest.mark.asyncio
    async def test_downgrade_remove_os_tres_e_upgrade_recria(self):
        mig = _carregar_migracao(REVISION)
        engine, _ = await _preparar_banco()
        try:
            antes = await _indices_das_tres(engine)
            for tabela, nome in INDICES.items():
                assert nome in antes[tabela], (tabela, antes[tabela])

            async with engine.begin() as conn:
                await conn.run_sync(_rodar_op, mig.downgrade)
            depois_down = await _indices_das_tres(engine)
            for tabela, nome in INDICES.items():
                assert nome not in depois_down[tabela], (tabela, depois_down[tabela])
            assert PARCIAIS_INTOCADOS <= depois_down["listing_images"]

            async with engine.begin() as conn:
                await conn.run_sync(_rodar_op, mig.upgrade)
            depois_up = await _indices_das_tres(engine)
            for tabela, nome in INDICES.items():
                assert nome in depois_up[tabela], (tabela, depois_up[tabela])
            assert PARCIAIS_INTOCADOS <= depois_up["listing_images"]

            async with engine.begin() as conn:
                await conn.run_sync(_rodar_op, mig.downgrade)
            de_novo = await _indices_das_tres(engine)
            for tabela, nome in INDICES.items():
                assert nome not in de_novo[tabela], (tabela, de_novo[tabela])
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_os_tres_sao_btree_simples_em_listing_id_sem_where(self):
        """Nao podem nascer parciais nem compostos: o objetivo e' cobrir
        "todas as linhas deste anuncio"."""
        mig = _carregar_migracao(REVISION)
        engine, _ = await _preparar_banco()
        try:
            async with engine.begin() as conn:
                # DDL do `create_all` (fonte: __table_args__ dos models)...
                defs_models = {nome: await conn.run_sync(_indexdef, nome) for nome in INDICES.values()}
                await conn.run_sync(_rodar_op, mig.downgrade)
                await conn.run_sync(_rodar_op, mig.upgrade)
                # ...tem que ser identico ao DDL da migracao.
                defs = {nome: await conn.run_sync(_indexdef, nome) for nome in INDICES.values()}
            for tabela, nome in INDICES.items():
                assert defs[nome] is not None, nome
                assert defs[nome].endswith(f"ON public.{tabela} USING btree (listing_id)"), defs[nome]
                assert " WHERE " not in defs[nome], defs[nome]
                assert defs[nome] == defs_models[nome], (defs[nome], defs_models[nome])
        finally:
            await engine.dispose()


def test_revisao_encadeia_no_head_atual():
    mod = _carregar_migracao(REVISION)
    assert mod.down_revision == "e5f9c3b7a2d4"
