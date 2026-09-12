"""Migracao d4e8b2a6f9c1: indice composto `ix_listings_seller_status_created`.

A tela de fila de trabalho consulta sempre no mesmo formato:
`WHERE seller_id = ? [AND status IN (...)] ORDER BY created_at DESC LIMIT n`,
e a contagem por status e' `GROUP BY status` por seller. UM indice composto
`(seller_id, status, created_at DESC)` atende os dois: com um status so, a
ordenacao ja sai pronta (sem Sort); com varios, o planner faz scans por
valor e um sort pequeno sobre o recorte; a contagem e' index-only scan no
prefixo `(seller_id, status)`. `seller_id` sozinho fica coberto pelo prefixo
do composto — nao precisa de indice proprio. Tres indices separados nao
ajudariam o ORDER BY (precisariam de BitmapAnd) e duplicariam escrita.

Ruling do teste (mesmo padrao de `test_migracao_eventos_de_revisao.py`):
`Base.metadata.create_all` ja cria o indice no estado do model atual (via
`__table_args__` em `Listing`), entao o "antes" da migracao e' o proprio
create_all; `mig.downgrade()` remove o indice, `mig.upgrade()` recria,
`mig.downgrade()` remove de novo — prova que e' reversivel de verdade.

Roda contra Postgres REAL, so no banco dedicado `publicar_test`
(`_pg_dedicado` trava qualquer outro nome antes de abrir conexao).
"""
import importlib.util
import os
from pathlib import Path

import pytest

TEST_DB = os.environ.get("TEST_DATABASE_URL")
_precisa_db = pytest.mark.skipif(
    not TEST_DB, reason="TEST_DATABASE_URL nao definido: teste real so roda com Postgres dedicado"
)

REVISION = "d4e8b2a6f9c1"
INDICE = "ix_listings_seller_status_created"
VERSIONS_DIR = Path(__file__).resolve().parents[1] / "alembic" / "versions"


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


def _indices(sync_conn, tabela):
    from sqlalchemy import inspect

    return {i["name"] for i in inspect(sync_conn).get_indexes(tabela)}


def _indexdef(sync_conn, indice):
    from sqlalchemy import text as sa_text

    row = sync_conn.execute(
        sa_text("SELECT indexdef FROM pg_indexes WHERE indexname = :nome"),
        {"nome": indice},
    ).fetchone()
    return row[0] if row else None


@_precisa_db
class TestIndiceListagemEmEscala:
    @pytest.mark.asyncio
    async def test_downgrade_remove_e_upgrade_recria_o_indice(self):
        mig = _carregar_migracao(REVISION)
        engine, sm = await _preparar_banco()
        try:
            # `create_all` (rodado em `_preparar_banco`) ja deixa o indice no
            # estado do model atual (`__table_args__` em `Listing`).
            async with engine.begin() as conn:
                indices = await conn.run_sync(_indices, "listings")
            assert INDICE in indices, indices

            # "Antes" da migracao: downgrade remove o indice.
            async with engine.begin() as conn:
                await conn.run_sync(_rodar_op, mig.downgrade)
                indices = await conn.run_sync(_indices, "listings")
            assert INDICE not in indices, indices

            # Upgrade recria.
            async with engine.begin() as conn:
                await conn.run_sync(_rodar_op, mig.upgrade)
                indices = await conn.run_sync(_indices, "listings")
            assert INDICE in indices, indices

            # Downgrade de novo: reversivel de verdade, nao so "roda sem erro".
            async with engine.begin() as conn:
                await conn.run_sync(_rodar_op, mig.downgrade)
                indices = await conn.run_sync(_indices, "listings")
            assert INDICE not in indices, indices
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_indice_cobre_seller_status_created_desc(self):
        mig = _carregar_migracao(REVISION)
        engine, sm = await _preparar_banco()
        try:
            async with engine.begin() as conn:
                await conn.run_sync(_rodar_op, mig.downgrade)
                await conn.run_sync(_rodar_op, mig.upgrade)
                indexdef = await conn.run_sync(_indexdef, INDICE)
            assert indexdef is not None, "indice nao encontrado em pg_indexes"
            assert "(seller_id, status, created_at DESC)" in indexdef, indexdef
        finally:
            await engine.dispose()


def test_revisao_encadeia_no_head_atual():
    mod = _carregar_migracao(REVISION)
    assert mod.down_revision == "c8d2f6a4e1b7"
