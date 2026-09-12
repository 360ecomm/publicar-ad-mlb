"""Migracao e5f9c3b7a2d4: coluna `listing_attributes.tags` (JSONB, nullable).

Guarda o dicionario de tags do ML inteiro, como ele devolve — `hidden`,
`read_only`, `fixed`, `required`... — pra `ListingAttribute.is_editable`
poder dizer o que o operador nao deve nem ver. Nada e' preenchido
retroativamente: linhas antigas ficam com NULL, e NULL e' editavel.

Roda contra Postgres REAL, so no banco dedicado `publicar_test`
(`_pg_dedicado` trava qualquer outro nome antes de abrir conexao). Mesma
infraestrutura de `test_migracao_eventos_de_revisao.py`: `create_all` ja cria
a coluna no estado do model atual, entao o "antes" e' produzido com
`downgrade()`, depois `upgrade()` (coluna volta), depois `downgrade()` de
novo — reversibilidade de verdade, nao so "roda sem erro".
"""
import importlib.util
import os
from pathlib import Path

import pytest

TEST_DB = os.environ.get("TEST_DATABASE_URL")
_precisa_db = pytest.mark.skipif(
    not TEST_DB, reason="TEST_DATABASE_URL nao definido: teste real so roda com Postgres dedicado"
)

REVISION = "e5f9c3b7a2d4"
HEAD_ANTERIOR = "d4e8b2a6f9c1"
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
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    ctx = MigrationContext.configure(sync_conn)
    with Operations.context(ctx):
        fn()


def _colunas(sync_conn, tabela):
    from sqlalchemy import inspect

    return {c["name"]: c for c in inspect(sync_conn).get_columns(tabela)}


async def _preparar_banco():
    from tests._pg_dedicado import _engine_dedicado

    import app.models  # noqa: F401 — registra todas as tabelas
    from app.models.base import Base

    engine = _engine_dedicado()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    return engine


def test_revisao_encadeia_no_head_atual():
    """Sem banco: so importa o modulo e confere a cadeia. Roda sempre."""
    mig = _carregar_migracao(REVISION)
    assert mig.down_revision == HEAD_ANTERIOR


@_precisa_db
class TestColunaTags:
    @pytest.mark.asyncio
    async def test_downgrade_remove_e_upgrade_recria_a_coluna(self):
        mig = _carregar_migracao(REVISION)
        engine = await _preparar_banco()
        try:
            async with engine.begin() as conn:
                assert "tags" in await conn.run_sync(_colunas, "listing_attributes")
                await conn.run_sync(_rodar_op, mig.downgrade)
                assert "tags" not in await conn.run_sync(_colunas, "listing_attributes")
                await conn.run_sync(_rodar_op, mig.upgrade)
                cols = await conn.run_sync(_colunas, "listing_attributes")
                assert "tags" in cols
                assert cols["tags"]["nullable"] is True
                assert "JSON" in str(cols["tags"]["type"]).upper()
                await conn.run_sync(_rodar_op, mig.downgrade)
                assert "tags" not in await conn.run_sync(_colunas, "listing_attributes")
        finally:
            await engine.dispose()
