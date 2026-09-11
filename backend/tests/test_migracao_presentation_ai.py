"""Migracao de dados 9f4c2b7e1d63: kind `presentation` -> `presentation_ai`.

O worker passou a gravar a posicao 1 como `presentation_ai` (sufixo `_ai`
marca origem por IA, como as outras posicoes). As linhas antigas em
`listing_images` precisam acompanhar, senao o mesmo conceito fica com dois
nomes no banco. Sem mudanca de schema: `kind` e' String(20) e
`presentation_ai` tem 15 caracteres.

Roda contra Postgres REAL, so no banco dedicado `publicar_test`
(`_pg_dedicado` trava qualquer outro nome antes de abrir conexao). As funcoes
`upgrade()`/`downgrade()` da revisao sao executadas de verdade, com o `op`
do Alembic ligado a uma conexao sincrona do engine de teste — sem passar pelo
`env.py`, que le a URL do `settings` do app.
"""
import importlib.util
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest

TEST_DB = os.environ.get("TEST_DATABASE_URL")
_precisa_db = pytest.mark.skipif(
    not TEST_DB, reason="TEST_DATABASE_URL nao definido: teste real so roda com Postgres dedicado"
)

REVISION = "9f4c2b7e1d63"
VERSIONS_DIR = Path(__file__).resolve().parents[1] / "alembic" / "versions"


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


async def _semear(session_maker):
    """1 listing com 4 imagens: presentation/1 (a que migra), cover_ai/0,
    specs_ai/4 e uma candidata cover_ai/90 (as que NAO podem mudar).
    Devolve {id_da_imagem: kind_original}."""
    from app.models.listing import Listing
    from app.models.listing_image import ListingImage
    from app.models.seller import Seller
    from app.models.user import User

    linhas = [("cover_ai", 0), ("presentation", 1), ("specs_ai", 4), ("cover_ai", 90)]
    async with session_maker() as s:
        user = User(email=f"{uuid4()}@t.local", password_hash="x", full_name="t")
        seller = Seller(
            ml_user_id=int(str(uuid4().int)[:9]), ml_nickname="t", access_token_enc="x",
            refresh_token_enc="x", token_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        s.add_all([user, seller]); await s.flush()
        listing = Listing(
            seller_id=seller.id, created_by=user.id, sku_description="d", sku_brand="b",
            price=10, stock_quantity=1, condition="new", listing_type_id="gold_special",
            status="pending_image_approval", created_via="batch",
        )
        s.add(listing); await s.flush()
        imgs = [
            ListingImage(listing_id=listing.id, ml_picture_id=f"p{i}", status="uploaded",
                         approved=False, sort_order=so, kind=kind)
            for i, (kind, so) in enumerate(linhas)
        ]
        s.add_all(imgs); await s.commit()
        return {img.id: kind for img, (kind, _) in zip(imgs, linhas)}


async def _kinds(session_maker, ids):
    from sqlalchemy import select

    from app.models.listing_image import ListingImage

    async with session_maker() as s:
        rows = (await s.execute(select(ListingImage.id, ListingImage.kind)
                                .where(ListingImage.id.in_(list(ids))))).all()
    return {i: k for i, k in rows}


@_precisa_db
class TestMigracaoPresentationAi:
    @pytest.mark.asyncio
    async def test_upgrade_renomeia_so_presentation_e_downgrade_desfaz(self):
        mig = _carregar_migracao()
        engine, sm = await _preparar_banco()
        try:
            originais = await _semear(sm)
            alvo = next(i for i, k in originais.items() if k == "presentation")
            intactas = {i: k for i, k in originais.items() if k != "presentation"}

            async with engine.begin() as conn:
                await conn.run_sync(_rodar_op, mig.upgrade)
            depois = await _kinds(sm, originais)
            assert depois[alvo] == "presentation_ai", depois
            assert {i: depois[i] for i in intactas} == intactas, "upgrade tocou em linha que nao era presentation"

            async with engine.begin() as conn:
                await conn.run_sync(_rodar_op, mig.downgrade)
            de_volta = await _kinds(sm, originais)
            assert de_volta == originais, de_volta
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_upgrade_e_idempotente(self):
        """Rodar o upgrade duas vezes nao muda nada alem da primeira."""
        mig = _carregar_migracao()
        engine, sm = await _preparar_banco()
        try:
            originais = await _semear(sm)
            async with engine.begin() as conn:
                await conn.run_sync(_rodar_op, mig.upgrade)
            primeira = await _kinds(sm, originais)
            async with engine.begin() as conn:
                await conn.run_sync(_rodar_op, mig.upgrade)
            assert await _kinds(sm, originais) == primeira
            assert sorted(primeira.values()) == sorted(["cover_ai", "cover_ai", "presentation_ai", "specs_ai"])
        finally:
            await engine.dispose()
