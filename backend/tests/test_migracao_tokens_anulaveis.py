"""Migracao b8e2d4f6a1c3: `sellers.access_token_enc` e `sellers.refresh_token_enc`
passam a aceitar NULL.

Desconectar uma conta (decisao do Daniel, 2026-09-13) apaga SO o token e
mantem o historico: a linha do seller fica, com `is_active = False` e os dois
tokens NULL. Gravar string vazia funcionaria sem migracao, mas codificaria
"sem token" num campo que declara nao aceitar ausencia — regra nao escrita.

Roda contra Postgres REAL, so no banco dedicado `publicar_test`
(`_pg_dedicado` trava qualquer outro nome antes de abrir conexao). `create_all`
ja cria as colunas anulaveis (estado do model atual), entao o "antes" e'
produzido com `downgrade()`, depois `upgrade()`, depois `downgrade()` de novo.
O downgrade com uma linha desconectada (token NULL) preenche '' antes de
devolver o NOT NULL — reverter a migracao nao pode apagar seller nenhum.
"""
import importlib.util
import os
from pathlib import Path

import pytest

TEST_DB = os.environ.get("TEST_DATABASE_URL")
_precisa_db = pytest.mark.skipif(
    not TEST_DB, reason="TEST_DATABASE_URL nao definido: teste real so roda com Postgres dedicado"
)

REVISION = "b8e2d4f6a1c3"
HEAD_ANTERIOR = "a1d7c3e9f5b2"
VERSIONS_DIR = Path(__file__).resolve().parents[1] / "alembic" / "versions"


def _carregar_migracao(revision):
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


def test_model_declara_tokens_anulaveis():
    """Sem banco: o model tem que concordar com a migracao, senao o
    `create_all` dos testes cria um schema diferente do de producao."""
    from app.models.seller import Seller

    assert Seller.__table__.c.access_token_enc.nullable is True
    assert Seller.__table__.c.refresh_token_enc.nullable is True


@_precisa_db
class TestTokensAnulaveis:
    @pytest.mark.asyncio
    async def test_downgrade_devolve_not_null_e_upgrade_libera(self):
        mig = _carregar_migracao(REVISION)
        engine = await _preparar_banco()
        try:
            async with engine.begin() as conn:
                cols = await conn.run_sync(_colunas, "sellers")
                assert cols["access_token_enc"]["nullable"] is True
                assert cols["refresh_token_enc"]["nullable"] is True

                await conn.run_sync(_rodar_op, mig.downgrade)
                cols = await conn.run_sync(_colunas, "sellers")
                assert cols["access_token_enc"]["nullable"] is False
                assert cols["refresh_token_enc"]["nullable"] is False

                await conn.run_sync(_rodar_op, mig.upgrade)
                cols = await conn.run_sync(_colunas, "sellers")
                assert cols["access_token_enc"]["nullable"] is True
                assert cols["refresh_token_enc"]["nullable"] is True
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_downgrade_com_seller_desconectado_preenche_vazio_em_vez_de_falhar(self):
        """Uma linha com token NULL (conta desconectada) impediria o NOT NULL.
        O downgrade preenche '' nessas linhas e preserva o seller."""
        from datetime import datetime, timezone

        from sqlalchemy import text

        mig = _carregar_migracao(REVISION)
        engine = await _preparar_banco()
        try:
            async with engine.begin() as conn:
                await conn.execute(text(
                    "INSERT INTO sellers (id, ml_user_id, ml_nickname, ml_site_id, access_token_enc, "
                    "refresh_token_enc, token_expires_at, is_active, created_at, updated_at) "
                    "VALUES (gen_random_uuid(), 1, 'desconectada', 'MLB', NULL, NULL, :agora, false, :agora, :agora)"
                ), {"agora": datetime.now(timezone.utc)})
                await conn.run_sync(_rodar_op, mig.downgrade)
                linha = (await conn.execute(text(
                    "SELECT access_token_enc, refresh_token_enc, is_active FROM sellers WHERE ml_user_id = 1"
                ))).one()
                assert linha == ("", "", False)
                await conn.run_sync(_rodar_op, mig.upgrade)
        finally:
            await engine.dispose()
