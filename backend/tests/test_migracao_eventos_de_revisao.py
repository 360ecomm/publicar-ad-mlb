"""Migracao b3e7a1c9d5f2: cria `listing_review_events`.

Cada aprovacao humana de imagens (individual ou em lote) grava UM evento
nesta tabela, na MESMA transacao da aprovacao — a tabela existir e' pre-
requisito pra isso ser possivel. Roda contra Postgres REAL, so no banco
dedicado `publicar_test` (`_pg_dedicado` trava qualquer outro nome antes de
abrir conexao).

Ruling do teste: `Base.metadata.create_all` ja cria a tabela no estado do
model atual, entao o "antes" da migracao e' produzido com `mig.downgrade()`
(tabela some), depois `mig.upgrade()` (tabela volta, com as colunas e o
indice `ix_listing_review_events_listing_id`), depois `mig.downgrade()` de
novo (some outra vez) — prova que upgrade/downgrade sao reversiveis de
verdade, nao so "roda sem erro".

A Rev 2 (drop de `listing_images.review_seconds`) entra neste mesmo arquivo
na Task 2 — nao aqui.
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
VERSIONS_DIR = Path(__file__).resolve().parents[1] / "alembic" / "versions"

COLUNAS_ESPERADAS = {
    "id", "listing_id", "user_id", "action", "mode",
    "approved_count", "review_seconds", "created_at",
}


def _carregar_migracao():
    """Importa o modulo da revisao pelo arquivo `alembic/versions/<REVISION>_*.py`.
    Antes de a revisao existir, falha aqui — e' o vermelho do TDD."""
    candidatos = sorted(VERSIONS_DIR.glob(f"{REVISION}_*.py"))
    assert len(candidatos) == 1, f"migracao {REVISION} nao encontrada em {VERSIONS_DIR}: {candidatos}"
    spec = importlib.util.spec_from_file_location(f"migracao_{REVISION}", candidatos[0])
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.revision == REVISION
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


@_precisa_db
class TestRev1ListingReviewEvents:
    @pytest.mark.asyncio
    async def test_downgrade_dropa_e_upgrade_recria_a_tabela(self):
        mig = _carregar_migracao()
        engine, sm = await _preparar_banco()
        try:
            # `create_all` (rodado em `_preparar_banco`) ja deixa a tabela no
            # estado do model atual.
            async with engine.begin() as conn:
                tabelas = await conn.run_sync(_tabelas)
            assert "listing_review_events" in tabelas, tabelas

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
            assert "listing_review_events" in tabelas, tabelas
            assert colunas == COLUNAS_ESPERADAS, colunas
            assert "ix_listing_review_events_listing_id" in indices, indices

            # Downgrade de novo: reversivel de verdade, nao so "roda sem erro".
            async with engine.begin() as conn:
                await conn.run_sync(_rodar_op, mig.downgrade)
                tabelas = await conn.run_sync(_tabelas)
            assert "listing_review_events" not in tabelas, tabelas
        finally:
            await engine.dispose()
