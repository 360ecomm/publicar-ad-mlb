"""Migracao b3e7a1c9d5f2: cria `listing_review_events`.

Cada aprovacao humana de imagens (individual ou em lote) grava UM evento
nesta tabela, na MESMA transacao da aprovacao — a tabela existir e' pre-
requisito pra isso ser possivel. Roda contra Postgres REAL, so no banco
dedicado `publicar_test` (`_pg_dedicado` trava qualquer outro nome antes de
abrir conexao).

Ruling do teste: `Base.metadata.create_all` ja cria a tabela no estado do
model atual, entao o "antes" da migracao e' produzido com `mig.downgrade()`
(tabela some), depois `mig.upgrade()` (tabela volta, com as colunas e o
indice `ix_listing_review_events_listing_id`, e a FK de `listing_id` com
`ON DELETE CASCADE`), depois `mig.downgrade()` de
novo (some outra vez) — prova que upgrade/downgrade sao reversiveis de
verdade, nao so "roda sem erro".

A Rev 2 (`c8d2f6a4e1b7`, drop de `listing_images.review_seconds`) esta neste
mesmo arquivo, em `TestRev2DropReviewSeconds`: o tempo de revisao passou a
viver so no evento (`listing_review_events.review_seconds`, Rev 1); a coluna
homonima em `listing_images` fica redundante e sai.
"""
import importlib.util
import os
from pathlib import Path

import pytest

TEST_DB = os.environ.get("TEST_DATABASE_URL")
_precisa_db = pytest.mark.skipif(
    not TEST_DB, reason="TEST_DATABASE_URL nao definido: teste real so roda com Postgres dedicado"
)

REVISION = "b3e7a1c9d5f2"
REV2 = "c8d2f6a4e1b7"
VERSIONS_DIR = Path(__file__).resolve().parents[1] / "alembic" / "versions"

COLUNAS_ESPERADAS = {
    "id", "listing_id", "user_id", "action", "mode",
    "approved_count", "review_seconds", "created_at",
}


def _carregar_migracao(revision):
    """Importa o modulo da revisao pelo arquivo `alembic/versions/<revision>_*.py`.
    Antes de a revisao existir, falha aqui — e' o vermelho do TDD."""
    candidatos = sorted(VERSIONS_DIR.glob(f"{revision}_*.py"))
    assert len(candidatos) == 1, f"migracao {revision} nao encontrada em {VERSIONS_DIR}: {candidatos}"
    spec = importlib.util.spec_from_file_location(f"migracao_{revision}", candidatos[0])
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.revision == revision
    return mod


def _rodar_op(sync_conn, fn):
    """Executa `fn()` (upgrade/downgrade) com `alembic.op` ligado a `sync_conn`."""
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    ctx = MigrationContext.configure(sync_conn)
    with Operations.context(ctx):
        fn()


async def _preparar_banco():
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from tests._pg_dedicado import _engine_dedicado

    import app.models  # noqa: F401 — registra todas as tabelas
    from app.models.base import Base

    engine = _engine_dedicado()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def _tabelas(sync_conn):
    from sqlalchemy import inspect

    return set(inspect(sync_conn).get_table_names())


def _colunas(sync_conn, tabela):
    from sqlalchemy import inspect

    return {c["name"] for c in inspect(sync_conn).get_columns(tabela)}


def _indices(sync_conn, tabela):
    from sqlalchemy import inspect

    return {i["name"] for i in inspect(sync_conn).get_indexes(tabela)}


def _ondelete_por_coluna(sync_conn, tabela):
    """`{coluna_restrita: ondelete}` de cada FK de UMA coluna da tabela
    (`None` quando a FK nao declara `ON DELETE`)."""
    from sqlalchemy import inspect

    return {
        fk["constrained_columns"][0]: (fk.get("options") or {}).get("ondelete")
        for fk in inspect(sync_conn).get_foreign_keys(tabela)
        if len(fk["constrained_columns"]) == 1
    }


# `listing_id` apaga junto com o listing (mesmo padrao dos outros filhos de
# `Listing`); `user_id` NAO: apagar um usuario nao pode sumir com auditoria.
ONDELETE_ESPERADO = {"listing_id": "CASCADE", "user_id": None}


@_precisa_db
class TestRev1ListingReviewEvents:
    @pytest.mark.asyncio
    async def test_downgrade_dropa_e_upgrade_recria_a_tabela(self):
        mig = _carregar_migracao(REVISION)
        engine, sm = await _preparar_banco()
        try:
            # `create_all` (rodado em `_preparar_banco`) ja deixa a tabela no
            # estado do model atual.
            async with engine.begin() as conn:
                tabelas = await conn.run_sync(_tabelas)
                fks_model = await conn.run_sync(_ondelete_por_coluna, "listing_review_events")
            assert "listing_review_events" in tabelas, tabelas
            assert fks_model == ONDELETE_ESPERADO, fks_model

            # "Antes" da migracao: downgrade dropa a tabela.
            async with engine.begin() as conn:
                await conn.run_sync(_rodar_op, mig.downgrade)
                tabelas = await conn.run_sync(_tabelas)
            assert "listing_review_events" not in tabelas, tabelas

            # Upgrade recria — colunas e indice conferidos.
            async with engine.begin() as conn:
                await conn.run_sync(_rodar_op, mig.upgrade)
                tabelas = await conn.run_sync(_tabelas)
                colunas = await conn.run_sync(_colunas, "listing_review_events")
                indices = await conn.run_sync(_indices, "listing_review_events")
                fks_mig = await conn.run_sync(_ondelete_por_coluna, "listing_review_events")
            assert "listing_review_events" in tabelas, tabelas
            assert colunas == COLUNAS_ESPERADAS, colunas
            assert "ix_listing_review_events_listing_id" in indices, indices
            assert fks_mig == ONDELETE_ESPERADO, fks_mig

            # Downgrade de novo: reversivel de verdade, nao so "roda sem erro".
            async with engine.begin() as conn:
                await conn.run_sync(_rodar_op, mig.downgrade)
                tabelas = await conn.run_sync(_tabelas)
            assert "listing_review_events" not in tabelas, tabelas
        finally:
            await engine.dispose()


def _coluna_info(sync_conn, tabela, nome):
    """Info completa (inclusive `nullable`) de UMA coluna — `_colunas` acima
    so devolve o conjunto de nomes, e aqui a asserção precisa de `nullable`."""
    from sqlalchemy import inspect

    for c in inspect(sync_conn).get_columns(tabela):
        if c["name"] == nome:
            return c
    return None


@_precisa_db
class TestRev2DropReviewSeconds:
    @pytest.mark.asyncio
    async def test_downgrade_recria_a_coluna_e_upgrade_dropa(self):
        """`Base.metadata.create_all` (rodado em `_preparar_banco`) constroi o
        estado do MODEL atual — sem `review_seconds`, depois da coluna sair do
        model nesta mesma rodada. Entao aqui o "antes" da migracao e'
        produzido com `mig.downgrade()` (recria a coluna, nullable), e o
        "depois" com `mig.upgrade()` (dropa de novo)."""
        mig = _carregar_migracao(REV2)
        engine, sm = await _preparar_banco()
        try:
            # Estado do model atual: sem a coluna.
            async with engine.begin() as conn:
                colunas = await conn.run_sync(_colunas, "listing_images")
            assert "review_seconds" not in colunas, colunas

            # Downgrade recria a coluna (nullable, SMALLINT) — simula o "antes".
            async with engine.begin() as conn:
                await conn.run_sync(_rodar_op, mig.downgrade)
                info = await conn.run_sync(_coluna_info, "listing_images", "review_seconds")
            assert info is not None, "downgrade nao recriou review_seconds"
            assert info["nullable"] is True, info

            # Upgrade dropa de novo — reversivel de verdade.
            async with engine.begin() as conn:
                await conn.run_sync(_rodar_op, mig.upgrade)
                colunas = await conn.run_sync(_colunas, "listing_images")
            assert "review_seconds" not in colunas, colunas
        finally:
            await engine.dispose()
