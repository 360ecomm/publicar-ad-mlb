# Contas, seletor no topo e desconexão — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Dar ao operador uma página de contas alcançável (`/contas`), um seletor de conta sempre visível que recarrega os dados ao trocar, a desconexão de um seller preservando o histórico, e o retorno do OAuth do ML em aba nova voltando para `/contas`.

**Architecture:** Backend: migração tornando os tokens do `Seller` anuláveis, guarda em `get_valid_access_token`, endpoint `POST /sellers/{id}/disconnect` num `SellerService` novo, dashboard incluindo contas desconectadas, callback do OAuth redirecionando para `/contas`. Frontend: painel movido para `(dashboard)/contas`, `SellerContext` corrigido (só contas ativas são selecionáveis; troca faz `resetQueries`), barra de conta fixa no topo do conteúdo em `(dashboard)/layout.tsx` substituindo o seletor do rodapé do menu, página de contas com conectar (nova aba), desconectar (confirmação) e reconectar.

**Tech Stack:** FastAPI + SQLAlchemy 2.0 async + Alembic + pytest (backend); Next.js 14 App Router + TypeScript + TanStack Query 5 + Tailwind + sonner (frontend).

**Spec:** `docs/superpowers/specs/2026-09-13-contas-e-seletor.md`

## Global Constraints

- Branch `feat/contas-e-seletor`, a partir de `master` `d64b067`. **Nunca fazer merge, push para master, nem deploy.** Commits em Conventional Commits, mensagens em PT-BR, terminando com as duas linhas de atribuição da sessão (ver dispatch).
- Backend roda em Docker: `docker compose exec -T backend pytest -q ...` (workdir `/app`, código montado por bind mount em dev — editar em `backend/` no host vale dentro do container). Testes de Postgres real só com `TEST_DATABASE_URL` apontando para `publicar_test`: `docker compose exec -T backend sh -c 'TEST_DATABASE_URL="${DATABASE_URL%/*}/publicar_test" pytest -q -p no:cacheprovider <arquivo>'`. **Uso exclusivo**: nunca duas suítes contra `publicar_test` ao mesmo tempo.
- `conftest.py` bloqueia toda rede: mockar `httpx`/provedores em teste, nunca chamar o ML.
- Frontend: `cd frontend && npx tsc --noEmit && npx next lint && npx next build` têm que passar. Não há jest/vitest; **não instalar nada**.
- Convenções: erros HTTP sempre `{"detail": "mensagem"}`; services `class XService: def __init__(self, db: AsyncSession)`; model novo/alterado continua importado em `app/models/__init__.py`; migração com docstring explicando o porquê, no estilo de `alembic/versions/a1d7c3e9f5b2_indice_slot_regeneracao.py`; head atual do Alembic: `a1d7c3e9f5b2`.
- Regra de domínio: **desconectar apaga só o token e marca `is_active = False`; nunca apaga a linha do seller nem nada ligado a ela** (listings, products, listing_images, listing_review_events, user_seller_access).
- Textos de UI em PT-BR, sentence case.
- Comentários em código explicam o porquê, no estilo do repositório.

---

### Task 1: Migração — tokens do `Seller` anuláveis

**Files:**
- Create: `backend/alembic/versions/b8e2d4f6a1c3_tokens_anulaveis_em_sellers.py`
- Modify: `backend/app/models/seller.py:17-18`
- Test: `backend/tests/test_migracao_tokens_anulaveis.py`

**Interfaces:**
- Produces: `Seller.access_token_enc: str | None`, `Seller.refresh_token_enc: str | None` (Task 2 e 3 gravam `None` e leem checando falsy).

- [ ] **Step 1: Escrever o teste (vermelho)**

```python
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
```

> Se `TimestampMixin` não tiver `created_at`/`updated_at` com esses nomes, ajustar o INSERT lendo `app/models/base.py` — o teste deve inserir uma linha válida.

- [ ] **Step 2: Rodar e ver falhar**

Run: `docker compose exec -T backend pytest -q tests/test_migracao_tokens_anulaveis.py`
Expected: FAIL em `_carregar_migracao` (migração não encontrada) e no teste do model (`nullable is False`).

- [ ] **Step 3: Model**

Em `backend/app/models/seller.py`, trocar as duas linhas:

```python
    # Anulaveis desde b8e2d4f6a1c3: desconectar a conta apaga SO os tokens e
    # mantem a linha (historico de anuncios, produtos e imagens). NULL = sem
    # token; quem le tem que passar por `get_valid_access_token`, que recusa
    # seller inativo ou sem token com erro claro em vez de estourar no Fernet.
    access_token_enc: Mapped[str | None] = mapped_column(String, nullable=True)
    refresh_token_enc: Mapped[str | None] = mapped_column(String, nullable=True)
```

- [ ] **Step 4: Migração**

```python
"""tokens do seller anulaveis: desconectar apaga so o token, mantem o historico

`sellers.access_token_enc` e `sellers.refresh_token_enc` passam de NOT NULL
para NULL permitido. Decisao do Daniel (2026-09-13): desconectar uma conta do
Mercado Livre limpa os dois tokens e marca `is_active = False`; a linha do
seller e tudo que aponta pra ela (listings, products, listing_images,
listing_review_events, user_seller_access) ficam intactos. Gravar string
vazia dispensaria esta migracao, mas codificaria "sem token" num campo que
declara nao aceitar ausencia — regra nao escrita que o proximo leitor nao
tem como saber.

O downgrade preenche '' nas linhas com NULL antes de devolver o NOT NULL:
reverter a migracao nao pode falhar por causa de uma conta desconectada nem
apagar seller nenhum.

Revision ID: b8e2d4f6a1c3
Revises: a1d7c3e9f5b2
Create Date: 2026-09-13 21:00:00.000000
"""
import sqlalchemy as sa
from alembic import op

revision = 'b8e2d4f6a1c3'
down_revision = 'a1d7c3e9f5b2'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column('sellers', 'access_token_enc', existing_type=sa.String(), nullable=True)
    op.alter_column('sellers', 'refresh_token_enc', existing_type=sa.String(), nullable=True)


def downgrade() -> None:
    op.execute("UPDATE sellers SET access_token_enc = '' WHERE access_token_enc IS NULL")
    op.execute("UPDATE sellers SET refresh_token_enc = '' WHERE refresh_token_enc IS NULL")
    op.alter_column('sellers', 'access_token_enc', existing_type=sa.String(), nullable=False)
    op.alter_column('sellers', 'refresh_token_enc', existing_type=sa.String(), nullable=False)
```

- [ ] **Step 5: Rodar (verde), sem e com banco**

Run: `docker compose exec -T backend pytest -q tests/test_migracao_tokens_anulaveis.py`
Expected: 2 passed, 2 skipped.
Run: `docker compose exec -T backend sh -c 'TEST_DATABASE_URL="${DATABASE_URL%/*}/publicar_test" pytest -q -p no:cacheprovider tests/test_migracao_tokens_anulaveis.py'`
Expected: 4 passed.

- [ ] **Step 6: Aplicar a migração no banco de dev e conferir**

Run: `docker compose exec -T backend alembic upgrade head && docker compose exec -T backend alembic current`
Expected: `b8e2d4f6a1c3 (head)`.

- [ ] **Step 7: Commit**

```bash
git add backend/alembic/versions/b8e2d4f6a1c3_tokens_anulaveis_em_sellers.py backend/app/models/seller.py backend/tests/test_migracao_tokens_anulaveis.py
git commit -m "feat(sellers): tokens anuláveis para desconexão sem apagar histórico"
```

---

### Task 2: Guarda em `get_valid_access_token`

**Files:**
- Modify: `backend/app/services/publish_service.py:43-50`
- Test: `backend/tests/test_token_seller_desconectado.py`

**Interfaces:**
- Produces: `class SellerDisconnectedError(RuntimeError)` em `app.services.publish_service`; `get_valid_access_token(seller, db)` levanta essa exceção quando `not seller.is_active` ou algum dos dois tokens é falsy, ANTES de qualquer `decrypt_value`.

- [ ] **Step 1: Teste (vermelho)**

```python
"""Guarda de `get_valid_access_token`: conta desconectada nunca chega ao Fernet.

Sem a guarda, um worker que tocasse num anuncio de uma conta desconectada
(token NULL) morreria com erro de criptografia — sintoma longe da causa. A
guarda recusa seller inativo OU sem token com `SellerDisconnectedError`,
antes de qualquer `decrypt_value`.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest


def _seller(**kw):
    base = dict(
        ml_nickname="LOJA",
        is_active=True,
        access_token_enc="cifrado-a",
        refresh_token_enc="cifrado-r",
        token_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    base.update(kw)
    return SimpleNamespace(**base)


class TestGuardaDeContaDesconectada:
    @pytest.mark.asyncio
    async def test_inativo_recusa_antes_do_fernet(self):
        from app.services.publish_service import SellerDisconnectedError, get_valid_access_token

        with patch("app.services.publish_service.decrypt_value") as decrypt:
            with pytest.raises(SellerDisconnectedError) as exc:
                await get_valid_access_token(_seller(is_active=False), db=AsyncMock())
        decrypt.assert_not_called()
        assert "LOJA" in str(exc.value)
        assert "desconectada" in str(exc.value)

    @pytest.mark.asyncio
    async def test_ativo_sem_access_token_recusa_antes_do_fernet(self):
        from app.services.publish_service import SellerDisconnectedError, get_valid_access_token

        with patch("app.services.publish_service.decrypt_value") as decrypt:
            with pytest.raises(SellerDisconnectedError):
                await get_valid_access_token(_seller(access_token_enc=None), db=AsyncMock())
        decrypt.assert_not_called()

    @pytest.mark.asyncio
    async def test_ativo_sem_refresh_token_recusa_mesmo_com_access_valido(self):
        """O access expira em horas; sem refresh a conta esta' morta em breve
        e a renovacao explodiria no Fernet. Recusar ja."""
        from app.services.publish_service import SellerDisconnectedError, get_valid_access_token

        with patch("app.services.publish_service.decrypt_value") as decrypt:
            with pytest.raises(SellerDisconnectedError):
                await get_valid_access_token(_seller(refresh_token_enc=None), db=AsyncMock())
        decrypt.assert_not_called()

    @pytest.mark.asyncio
    async def test_ativo_com_token_valido_devolve_o_token(self):
        from app.services.publish_service import get_valid_access_token

        with patch("app.services.publish_service.decrypt_value", return_value="tok-claro") as decrypt:
            token = await get_valid_access_token(_seller(), db=AsyncMock())
        assert token == "tok-claro"
        decrypt.assert_called_once_with("cifrado-a")

    def test_erro_e_runtimeerror_para_os_workers_tratarem_como_falha(self):
        """Os workers ja tratam RuntimeError como falha do listing; a guarda
        nao pode introduzir um tipo que escape desse tratamento."""
        from app.services.publish_service import SellerDisconnectedError

        assert issubclass(SellerDisconnectedError, RuntimeError)
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `docker compose exec -T backend pytest -q tests/test_token_seller_desconectado.py`
Expected: FAIL com `ImportError: cannot import name 'SellerDisconnectedError'`.

- [ ] **Step 3: Implementar**

Em `backend/app/services/publish_service.py`, antes de `get_valid_access_token`:

```python
class SellerDisconnectedError(RuntimeError):
    """Conta ML desconectada ou sem token: nao ha o que usar nem renovar.

    RuntimeError de proposito: os workers ja tratam RuntimeError como falha
    do listing (sem retry infinito), e a mensagem nomeia a conta para o
    operador saber onde reconectar.
    """
```

E no início da função, antes de ler `token_expires_at`:

```python
async def get_valid_access_token(seller, db) -> str:
    # Guarda ANTES de qualquer decrypt: desde b8e2d4f6a1c3 os tokens sao
    # anulaveis (conta desconectada). Sem isto o Fernet estouraria com
    # InvalidToken dentro de um worker — sintoma longe da causa.
    if not seller.is_active or not seller.access_token_enc or not seller.refresh_token_enc:
        raise SellerDisconnectedError(
            f"Conta ML {seller.ml_nickname!r} desconectada: reconecte em Contas antes de continuar."
        )
```

- [ ] **Step 4: Rodar (verde) + os testes que já mockam a função**

Run: `docker compose exec -T backend pytest -q tests/test_token_seller_desconectado.py tests/test_image_tasks.py tests/test_cover_variant.py tests/test_prefill_domain_discovery.py`
Expected: todos passed.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/publish_service.py backend/tests/test_token_seller_desconectado.py
git commit -m "feat(sellers): get_valid_access_token recusa conta desconectada antes do Fernet"
```

---

### Task 3: Endpoint de desconexão, `SellerService` e dashboard com contas desconectadas

**Files:**
- Create: `backend/app/services/seller_service.py`
- Modify: `backend/app/api/v1/endpoints/sellers.py` (endpoint novo + `get_dashboard` sem o filtro `is_active`)
- Modify: `backend/app/schemas/seller.py` (`SellerDashboardEntry.is_active: bool`)
- Test: `backend/tests/test_desconectar_seller.py`

**Interfaces:**
- Consumes: Task 1 (tokens anuláveis), Task 2 (`SellerDisconnectedError`, só citada na docstring).
- Produces: `POST /api/v1/sellers/{seller_id}/disconnect` → 200 `SellerOut` (com `is_active=false`), 404 `{"detail": "Conta não encontrada ou sem acesso."}`; idempotente (segunda chamada → 200 igual). `SellerService(db).disconnect(seller_id: UUID, user_id: UUID) -> tuple[Seller, datetime]` (seller e `granted_at`). `GET /api/v1/dashboard` passa a listar contas desconectadas com `is_active: false` e as contagens delas.

- [ ] **Step 1: Teste (vermelho)**

```python
"""Desconectar um seller: apaga SO os tokens, marca `is_active = False` e
mantem TODO o historico (listings, products, listing_images,
listing_review_events, user_seller_access). Reconectar pelo OAuth devolve a
conta a ativa com token novo. Postgres REAL (`publicar_test`).

Os endpoints sao exercitados pelo app de verdade (ASGITransport) com
`get_db` e `get_current_user` sobrescritos; nada de rede (conftest bloqueia).
"""
import os
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

TEST_DB = os.environ.get("TEST_DATABASE_URL")
_precisa_db = pytest.mark.skipif(
    not TEST_DB, reason="TEST_DATABASE_URL nao definido: teste real so roda com Postgres dedicado"
)


async def _semear_conta_com_historico(sm):
    """1 user admin, 1 seller ATIVO com acesso, 2 listings, 1 product,
    1 listing_image e 1 listing_review_event. Devolve (user_id, seller_id).
    Le os models para os campos obrigatorios: `tests/test_listagem_em_escala._semear`
    mostra como criar User/Seller/Listing validos."""
    from app.core.security import encrypt_value
    from app.models.listing import Listing
    from app.models.listing_image import ListingImage
    from app.models.listing_review_event import ListingReviewEvent
    from app.models.product import Product
    from app.models.seller import Seller
    from app.models.user import User
    from app.models.user_seller_access import UserSellerAccess

    async with sm() as s:
        user = User(email=f"{uuid4()}@t.local", password_hash="x", full_name="t", role="admin")
        s.add(user)
        await s.flush()
        seller = Seller(
            ml_user_id=int(str(uuid4().int)[:9]), ml_nickname="LOJA-T",
            access_token_enc=encrypt_value("acesso-antigo"),
            refresh_token_enc=encrypt_value("refresh-antigo"),
            token_expires_at=datetime.now(timezone.utc), is_active=True,
        )
        s.add(seller)
        await s.flush()
        s.add(UserSellerAccess(user_id=user.id, seller_id=seller.id, role="admin"))
        # Listing/Product/ListingImage/ListingReviewEvent: preencher os campos
        # obrigatorios conforme os models (ver `_semear` citado acima).
        l1 = Listing(seller_id=seller.id, created_by=user.id, sku_external_id="T1", sku_description="d",
                     sku_brand="b", price=10, status="draft")
        l2 = Listing(seller_id=seller.id, created_by=user.id, sku_external_id="T2", sku_description="d",
                     sku_brand="b", price=10, status="published", mlb_id=f"MLB{uuid4().int % 10**9}")
        s.add_all([l1, l2])
        await s.flush()
        s.add(Product(seller_id=seller.id, sku="T1", description="d"))
        s.add(ListingImage(listing_id=l1.id, sort_order=0, kind="cover_ai", status="uploaded", approved=False))
        s.add(ListingReviewEvent(listing_id=l1.id, user_id=user.id, action="approve", mode="individual",
                                 approved_count=0, review_seconds=None))
        await s.commit()
        return user.id, seller.id


async def _contagens(sm, seller_id):
    from sqlalchemy import func, select

    from app.models.listing import Listing
    from app.models.listing_image import ListingImage
    from app.models.listing_review_event import ListingReviewEvent
    from app.models.product import Product
    from app.models.user_seller_access import UserSellerAccess

    async with sm() as s:
        listings = (await s.execute(select(func.count()).select_from(Listing).where(Listing.seller_id == seller_id))).scalar_one()
        products = (await s.execute(select(func.count()).select_from(Product).where(Product.seller_id == seller_id))).scalar_one()
        images = (await s.execute(
            select(func.count()).select_from(ListingImage).join(Listing, Listing.id == ListingImage.listing_id)
            .where(Listing.seller_id == seller_id))).scalar_one()
        events = (await s.execute(
            select(func.count()).select_from(ListingReviewEvent).join(Listing, Listing.id == ListingReviewEvent.listing_id)
            .where(Listing.seller_id == seller_id))).scalar_one()
        acessos = (await s.execute(select(func.count()).select_from(UserSellerAccess).where(UserSellerAccess.seller_id == seller_id))).scalar_one()
    return {"listings": listings, "products": products, "images": images, "events": events, "acessos": acessos}


def _client_como(app, sm, user_id):
    from httpx import ASGITransport, AsyncClient

    from app.core.dependencies import get_current_user, get_db

    async def _override_get_db():
        async with sm() as session:
            yield session

    async def _override_user():
        return SimpleNamespace(id=user_id, role="admin", is_active=True)

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_current_user] = _override_user
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def test_rota_de_desconexao_existe():
    """Sem banco: a rota e' POST /api/v1/sellers/{seller_id}/disconnect."""
    from app.main import app

    rotas = {(r.path, tuple(sorted(r.methods))) for r in app.routes if getattr(r, "methods", None)}
    assert ("/api/v1/sellers/{seller_id}/disconnect", ("POST",)) in rotas


@_precisa_db
class TestDesconectar:
    @pytest.mark.asyncio
    async def test_apaga_tokens_marca_inativo_e_preserva_o_historico(self):
        from sqlalchemy import select

        from app.main import app
        from app.models.seller import Seller
        from tests.test_listagem_em_escala import _preparar_banco

        engine, sm = await _preparar_banco()
        try:
            user_id, seller_id = await _semear_conta_com_historico(sm)
            antes = await _contagens(sm, seller_id)
            assert antes == {"listings": 2, "products": 1, "images": 1, "events": 1, "acessos": 1}

            async with _client_como(app, sm, user_id) as client:
                resp = await client.post(f"/api/v1/sellers/{seller_id}/disconnect")
            assert resp.status_code == 200, resp.text
            corpo = resp.json()
            assert corpo["id"] == str(seller_id)
            assert corpo["is_active"] is False
            assert corpo["ml_nickname"] == "LOJA-T"

            async with sm() as s:
                seller = (await s.execute(select(Seller).where(Seller.id == seller_id))).scalar_one()
            assert seller.access_token_enc is None
            assert seller.refresh_token_enc is None
            assert seller.is_active is False
            assert await _contagens(sm, seller_id) == antes
        finally:
            app.dependency_overrides.clear()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_segunda_chamada_e_idempotente(self):
        from app.main import app
        from tests.test_listagem_em_escala import _preparar_banco

        engine, sm = await _preparar_banco()
        try:
            user_id, seller_id = await _semear_conta_com_historico(sm)
            async with _client_como(app, sm, user_id) as client:
                r1 = await client.post(f"/api/v1/sellers/{seller_id}/disconnect")
                r2 = await client.post(f"/api/v1/sellers/{seller_id}/disconnect")
            assert (r1.status_code, r2.status_code) == (200, 200)
            assert r2.json()["is_active"] is False
        finally:
            app.dependency_overrides.clear()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_sem_acesso_ao_seller_devolve_404_e_nao_mexe(self):
        from sqlalchemy import select

        from app.main import app
        from app.models.seller import Seller
        from tests.test_listagem_em_escala import _preparar_banco

        engine, sm = await _preparar_banco()
        try:
            _, seller_id = await _semear_conta_com_historico(sm)
            outro_user, _ = await _semear_conta_com_historico(sm)  # user sem acesso ao 1o seller
            async with _client_como(app, sm, outro_user) as client:
                resp = await client.post(f"/api/v1/sellers/{seller_id}/disconnect")
            assert resp.status_code == 404
            assert resp.json() == {"detail": "Conta não encontrada ou sem acesso."}
            async with sm() as s:
                seller = (await s.execute(select(Seller).where(Seller.id == seller_id))).scalar_one()
            assert seller.is_active is True and seller.access_token_enc is not None
        finally:
            app.dependency_overrides.clear()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_desconectada_continua_na_lista_e_no_dashboard_com_is_active_false(self):
        from app.main import app
        from tests.test_listagem_em_escala import _preparar_banco

        engine, sm = await _preparar_banco()
        try:
            user_id, seller_id = await _semear_conta_com_historico(sm)
            async with _client_como(app, sm, user_id) as client:
                await client.post(f"/api/v1/sellers/{seller_id}/disconnect")
                lista = (await client.get("/api/v1/sellers")).json()
                dash = (await client.get("/api/v1/dashboard")).json()
            assert [s["is_active"] for s in lista if s["id"] == str(seller_id)] == [False]
            entrada = [e for e in dash["sellers"] if e["seller_id"] == str(seller_id)]
            assert len(entrada) == 1
            assert entrada[0]["is_active"] is False
            assert entrada[0]["total_listings"] == 2
        finally:
            app.dependency_overrides.clear()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_desconectada_e_recusada_como_x_seller_id(self):
        """`get_active_seller` ja filtra `is_active`; a desconexao tem que
        cair nessa recusa (403), e' o que faz os anuncios pararem."""
        from app.main import app
        from tests.test_listagem_em_escala import _preparar_banco

        engine, sm = await _preparar_banco()
        try:
            user_id, seller_id = await _semear_conta_com_historico(sm)
            async with _client_como(app, sm, user_id) as client:
                await client.post(f"/api/v1/sellers/{seller_id}/disconnect")
                resp = await client.get("/api/v1/listings", headers={"X-Seller-ID": str(seller_id)})
            assert resp.status_code == 403
        finally:
            app.dependency_overrides.clear()
            await engine.dispose()


@_precisa_db
class TestReconectar:
    @pytest.mark.asyncio
    async def test_callback_do_oauth_reativa_com_token_novo(self):
        from unittest.mock import AsyncMock, patch

        from sqlalchemy import select

        from app.core.security import decrypt_value
        from app.main import app
        from app.models.seller import Seller
        from app.services.ml_oauth_service import MLOAuthService
        from tests.test_listagem_em_escala import _preparar_banco

        engine, sm = await _preparar_banco()
        try:
            user_id, seller_id = await _semear_conta_com_historico(sm)
            async with _client_como(app, sm, user_id) as client:
                await client.post(f"/api/v1/sellers/{seller_id}/disconnect")
            async with sm() as s:
                ml_user_id = (await s.execute(select(Seller.ml_user_id).where(Seller.id == seller_id))).scalar_one()

            token_data = {"access_token": "acesso-novo", "refresh_token": "refresh-novo", "expires_in": 21600}
            with patch("app.services.ml_oauth_service._pop_state", new=AsyncMock(return_value=str(user_id))), \
                 patch.object(MLOAuthService, "_exchange_code", new=AsyncMock(return_value=token_data)), \
                 patch.object(MLOAuthService, "_get_ml_user", new=AsyncMock(return_value={"id": ml_user_id, "nickname": "LOJA-T"})):
                async with sm() as s:
                    await MLOAuthService().handle_callback("code", "state", s)

            async with sm() as s:
                seller = (await s.execute(select(Seller).where(Seller.id == seller_id))).scalar_one()
            assert seller.is_active is True
            assert decrypt_value(seller.access_token_enc) == "acesso-novo"
            assert decrypt_value(seller.refresh_token_enc) == "refresh-novo"
            assert seller.token_expires_at > datetime.now(timezone.utc)
        finally:
            app.dependency_overrides.clear()
            await engine.dispose()
```

> Os construtores de `Listing`, `Product`, `ListingImage` e `ListingReviewEvent` no seed são a intenção; conferir os campos obrigatórios nos models e ajustar (é seed, não asserção). O que o teste afirma são as contagens e o estado do seller.

- [ ] **Step 2: Rodar e ver falhar**

Run: `docker compose exec -T backend pytest -q tests/test_desconectar_seller.py`
Expected: `test_rota_de_desconexao_existe` FAIL (rota inexistente); os demais skipped.

- [ ] **Step 3: Service**

`backend/app/services/seller_service.py`:

```python
"""Operacoes sobre a conta do Mercado Livre (Seller) fora do OAuth."""
from datetime import datetime, timezone
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.seller import Seller
from app.models.user_seller_access import UserSellerAccess


class SellerService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def disconnect(self, seller_id: UUID, user_id: UUID) -> tuple[Seller, datetime]:
        """Apaga SO os tokens e marca a conta como desconectada.

        A linha do seller e tudo que aponta pra ela (listings, products,
        listing_images, listing_review_events, user_seller_access) ficam:
        decisao de dominio, o historico e' do negocio, o token e' do ML.
        Efeitos: `get_active_seller` passa a recusar a conta (403) — os
        anuncios param —, `get_valid_access_token` recusa nos workers, e o
        callback do OAuth (`handle_callback`) devolve `is_active = True` com
        token novo quando o operador reconectar.

        Idempotente: desconectar uma conta ja desconectada devolve o mesmo
        estado, sem erro. Devolve tambem o `granted_at` do acesso, que o
        `SellerOut` exige.
        """
        result = await self.db.execute(
            select(Seller, UserSellerAccess.granted_at)
            .join(UserSellerAccess, UserSellerAccess.seller_id == Seller.id)
            .where(UserSellerAccess.user_id == user_id, Seller.id == seller_id)
        )
        row = result.one_or_none()
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Conta não encontrada ou sem acesso.",
            )
        seller, granted_at = row
        seller.access_token_enc = None
        seller.refresh_token_enc = None
        seller.token_expires_at = datetime.now(timezone.utc)
        seller.is_active = False
        await self.db.commit()
        await self.db.refresh(seller)
        return seller, granted_at
```

- [ ] **Step 4: Endpoint e dashboard**

Em `backend/app/api/v1/endpoints/sellers.py`:
- importar `from uuid import UUID` e `from app.services.seller_service import SellerService`;
- acrescentar, depois de `list_sellers`:

```python
@router.post("/sellers/{seller_id}/disconnect", response_model=SellerOut)
async def disconnect_seller(
    seller_id: UUID,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Desconecta a conta ML: apaga so os tokens, mantem o historico.

    Nao passa por `get_active_seller` de proposito: aquela dependencia
    recusa conta inativa, e desconectar tem que ser idempotente.
    """
    seller, granted_at = await SellerService(db).disconnect(seller_id, current_user.id)
    return SellerOut(
        id=seller.id,
        ml_user_id=seller.ml_user_id,
        ml_nickname=seller.ml_nickname,
        ml_site_id=seller.ml_site_id,
        token_expires_at=seller.token_expires_at,
        is_active=seller.is_active,
        granted_at=granted_at,
    )
```

- em `get_dashboard`, remover `Seller.is_active == True` do `.where(...)` (a página de contas mostra as desconectadas com o histórico delas) e passar `is_active=seller.is_active` ao construir `SellerDashboardEntry`. Comentário no lugar do filtro removido: `# Inclui contas desconectadas de proposito: /contas mostra o historico delas com "Reconectar".`

Em `backend/app/schemas/seller.py`, `SellerDashboardEntry` ganha `is_active: bool` (depois de `ml_nickname`).

- [ ] **Step 5: Rodar (verde), sem e com banco**

Run: `docker compose exec -T backend pytest -q tests/test_desconectar_seller.py`
Expected: 1 passed, 6 skipped.
Run: `docker compose exec -T backend sh -c 'TEST_DATABASE_URL="${DATABASE_URL%/*}/publicar_test" pytest -q -p no:cacheprovider tests/test_desconectar_seller.py'`
Expected: 7 passed.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/seller_service.py backend/app/api/v1/endpoints/sellers.py backend/app/schemas/seller.py backend/tests/test_desconectar_seller.py
git commit -m "feat(sellers): POST /sellers/{id}/disconnect apaga só o token; dashboard lista desconectadas"
```

---

### Task 4: Retorno do OAuth para `/contas`

**Files:**
- Modify: `backend/app/api/v1/endpoints/auth.py:29-45`
- Modify: `backend/tests/test_ml_oauth_state.py:148-170` (as asserções de destino)

**Interfaces:**
- Produces: com `FRONTEND_URL` definida, `GET /api/v1/auth/ml/callback` redireciona para `{FRONTEND_URL}/contas?ml_connected=true` (Task 7 lê esse parâmetro).

- [ ] **Step 1: Ajustar os testes (vermelho)**

Em `backend/tests/test_ml_oauth_state.py`, trocar toda ocorrência de `"/settings?ml_connected=true"` por `"/contas?ml_connected=true"` nas asserções de `location` (há uma em `test_com_frontend_url_redireciona` e a de `test_barra_final_no_frontend_url_nao_duplica`). Atualizar a docstring do módulo se ela citar `/settings`.

Run: `docker compose exec -T backend pytest -q tests/test_ml_oauth_state.py`
Expected: 2 FAIL (location ainda `/settings...`).

- [ ] **Step 2: Implementar**

Em `auth.py`, o `RedirectResponse` passa a `url=f"{frontend_url.rstrip('/')}/contas?ml_connected=true"`, e a docstring ganha: `A tela de contas (/contas) e' quem conecta e desconecta; e' para la que o operador volta.`

- [ ] **Step 3: Verde**

Run: `docker compose exec -T backend pytest -q tests/test_ml_oauth_state.py`
Expected: todos passed.

- [ ] **Step 4: Commit**

```bash
git add backend/app/api/v1/endpoints/auth.py backend/tests/test_ml_oauth_state.py
git commit -m "feat(oauth): callback volta para /contas?ml_connected=true"
```

---

### Task 5: Rota `/contas`, menu lateral e link da grade

**Files:**
- Move: `frontend/src/app/(dashboard)/page.tsx` → `frontend/src/app/(dashboard)/contas/page.tsx` (via `git mv`)
- Modify: `frontend/src/components/layout/Sidebar.tsx:15-20` (`NAV_ITEMS`)
- Modify: `frontend/src/app/(dashboard)/listings/attributes/page.tsx:19`
- Modify: `frontend/src/lib/api/sellers.ts` (`SellerDashboardEntry.is_active: boolean`)

**Interfaces:**
- Consumes: `GET /api/v1/dashboard` com `is_active` por entrada (Task 3).
- Produces: rota `/contas` (Task 7 a reescreve por dentro); `NAV_ITEMS` com "Anúncios" → `/listings` e "Contas" → `/contas`.

- [ ] **Step 1: Mover a página e ajustar o cabeçalho**

`git mv "frontend/src/app/(dashboard)/page.tsx" "frontend/src/app/(dashboard)/contas/page.tsx"`. Dentro dela: o `<h1>` passa de `Anúncios` para `Contas`, o ícone de `LayoutDashboard` para `ShoppingBag` (já importado; remover `LayoutDashboard` do import). Nada mais muda nesta task.

Em `frontend/src/lib/api/sellers.ts`, `SellerDashboardEntry` ganha `is_active: boolean` depois de `ml_nickname`.

- [ ] **Step 2: Menu lateral**

```ts
const NAV_ITEMS = [
  // A fila e' a porta de entrada do trabalho; `/` so redireciona pra ca.
  // Sem `exact`: /listings/attributes e /listings/{id}/... tambem destacam.
  { href: "/listings", label: "Anúncios", icon: LayoutDashboard },
  { href: "/contas", label: "Contas", icon: ShoppingBag },
  { href: "/products", label: "Produtos", icon: Package },
  { href: "/import", label: "Importar anúncios", icon: Upload },
  { href: "/settings", label: "Configurações", icon: Settings },
]
```

O cálculo de `isActive` continua o mesmo (`exact` fica opcional no tipo; nenhum item usa mais).

- [ ] **Step 3: Link da grade**

Em `listings/attributes/page.tsx`: `<Link href="/listings" ...>← Voltar à fila</Link>`.

- [ ] **Step 4: Verificar — e o aviso do build tem que sumir**

Run: `cd frontend && npx tsc --noEmit && npx next lint && npx next build 2>&1 | tee /tmp/build.log; grep -c "Failed to copy traced files" /tmp/build.log`
Expected: tsc e lint limpos, build OK, e o `grep -c` imprime **0** (antes desta task imprimia 1). Registrar a saída no relatório. `ls frontend/src/app/(dashboard)` não pode mais ter `page.tsx`.

- [ ] **Step 5: Commit**

```bash
git add -A frontend/src
git commit -m "feat(contas): painel de contas em /contas; menu aponta para a fila"
```

---

### Task 6: Contexto de seller corrigido, barra de conta no topo, seletor do rodapé removido

**Files:**
- Modify: `frontend/src/contexts/SellerContext.tsx`
- Create: `frontend/src/components/layout/AccountBar.tsx`
- Modify: `frontend/src/app/(dashboard)/layout.tsx`
- Modify: `frontend/src/components/layout/Sidebar.tsx` (remover `SellerSelector` e imports que sobrarem)
- Modify: `frontend/src/lib/api/sellers.ts` (`disconnectSeller`)

**Interfaces:**
- Produces (Task 7 consome):
  ```ts
  interface SellerContextValue {
    sellers: SellerOut[]           // todas, inclusive desconectadas (para /contas)
    connectedSellers: SellerOut[]  // is_active === true — as únicas selecionáveis
    activeSeller: SellerOut | null
    setActiveSeller: (seller: SellerOut) => void   // ignora seller inativo; reseta as queries
    clearActiveSeller: () => void
    isLoading: boolean
    reload: () => Promise<void>    // re-lê a lista e corrige a ativa se ela deixou de ser válida
  }
  export async function disconnectSeller(id: string): Promise<SellerOut>  // lib/api/sellers.ts
  ```

- [ ] **Step 1: `SellerContext.tsx`**

```tsx
"use client"

import { createContext, useContext, useEffect, useState, useCallback, useMemo } from "react"
import { useQueryClient } from "@tanstack/react-query"
import { listSellers, type SellerOut } from "@/lib/api/sellers"

interface SellerContextValue {
  /** Todas as contas que o usuário acessa, inclusive desconectadas (para /contas). */
  sellers: SellerOut[]
  /** Só as ativas: as únicas que podem ser a conta ativa (o backend recusa X-Seller-ID inativo com 403). */
  connectedSellers: SellerOut[]
  activeSeller: SellerOut | null
  setActiveSeller: (seller: SellerOut) => void
  clearActiveSeller: () => void
  isLoading: boolean
  reload: () => Promise<void>
}

const SellerContext = createContext<SellerContextValue | null>(null)

const STORAGE_KEY = "active_seller_id"

function persist(id: string | null) {
  if (typeof window === "undefined") return
  if (id) localStorage.setItem(STORAGE_KEY, id)
  else localStorage.removeItem(STORAGE_KEY)
}

export function SellerProvider({ children }: { children: React.ReactNode }) {
  const queryClient = useQueryClient()
  const [sellers, setSellers] = useState<SellerOut[]>([])
  const [activeSeller, setActiveSellerState] = useState<SellerOut | null>(null)
  const [isLoading, setIsLoading] = useState(true)

  const connectedSellers = useMemo(() => sellers.filter((s) => s.is_active), [sellers])

  const load = useCallback(async () => {
    try {
      const data = await listSellers()
      setSellers(data)

      // Só uma conta ATIVA pode ser a ativa: `get_active_seller` recusa
      // X-Seller-ID de conta desconectada com 403. Antes o contexto pegava
      // data[0] sem olhar is_active — com uma desconectada em primeiro
      // lugar, toda chamada falhava.
      const connected = data.filter((s) => s.is_active)
      const savedId = typeof window !== "undefined" ? localStorage.getItem(STORAGE_KEY) : null
      const found = savedId ? connected.find((s) => s.id === savedId) : null
      const active = found ?? connected[0] ?? null
      // localStorage ANTES do setState: o re-render dispara queries e o
      // apiFetch lê o X-Seller-ID de lá.
      persist(active?.id ?? null)
      setActiveSellerState(active)
    } catch {
      // Sem token o layout já redireciona para /login; não propaga.
    } finally {
      setIsLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const setActiveSeller = useCallback(
    (seller: SellerOut) => {
      if (!seller.is_active) return
      if (activeSeller?.id === seller.id) return
      // Ordem importa: storage primeiro (o refetch lê o header de lá), depois
      // o estado, depois o reset. `resetQueries` (não `invalidateQueries`)
      // apaga o cache e refaz as ativas: a tela nunca mostra dados da conta
      // anterior enquanto os novos chegam — trocar de conta e agir sobre o
      // anúncio errado seria um erro invisível.
      persist(seller.id)
      setActiveSellerState(seller)
      void queryClient.resetQueries()
    },
    [activeSeller, queryClient]
  )

  const clearActiveSeller = useCallback(() => {
    persist(null)
    setActiveSellerState(null)
    void queryClient.resetQueries()
  }, [queryClient])

  return (
    <SellerContext.Provider
      value={{ sellers, connectedSellers, activeSeller, setActiveSeller, clearActiveSeller, isLoading, reload: load }}
    >
      {children}
    </SellerContext.Provider>
  )
}

export function useSeller(): SellerContextValue {
  const ctx = useContext(SellerContext)
  if (!ctx) throw new Error("useSeller deve ser usado dentro de <SellerProvider>")
  return ctx
}
```

> `reload` (o `load`) já corrige a ativa: se a conta ativa foi desconectada, `found` não a encontra entre as ativas e cai em `connected[0] ?? null`. É isso que a Task 7 usa depois de desconectar. Se a ativa mudar por esse caminho, `load` NÃO reseta as queries; a Task 7 faz `queryClient.resetQueries()` explicitamente após o `reload()` quando a desconectada era a ativa.

- [ ] **Step 2: `lib/api/sellers.ts`**

```ts
export async function disconnectSeller(id: string): Promise<SellerOut> {
  return apiFetch<SellerOut>(`/api/v1/sellers/${id}/disconnect`, { method: "POST" })
}
```

- [ ] **Step 3: `AccountBar.tsx`**

```tsx
"use client"

import Link from "next/link"
import { ShoppingBag } from "lucide-react"
import { useSeller } from "@/contexts/SellerContext"

/**
 * Barra fixa no topo do conteúdo: a conta ativa, sempre visível.
 *
 * Fica no conteúdo, não no menu lateral, porque o menu abre recolhido
 * (só ícones) — o seletor que vivia no rodapé dele sumia com o nome da
 * conta. Aqui não depende do menu estar aberto e ainda diz em que conta
 * o operador está agindo, em qualquer tela.
 */
export function AccountBar() {
  const { connectedSellers, activeSeller, setActiveSeller, isLoading } = useSeller()

  return (
    <div
      data-testid="account-bar"
      className="h-12 flex-shrink-0 border-b border-border bg-background/95 backdrop-blur px-6 flex items-center gap-3 text-sm"
    >
      <ShoppingBag className={`w-4 h-4 flex-shrink-0 ${activeSeller ? "text-yellow-500" : "text-slate-400"}`} />
      <span className="text-slate-500">Conta ativa:</span>

      {isLoading ? (
        <span className="text-slate-400">carregando…</span>
      ) : !activeSeller ? (
        <span className="text-amber-600">
          Nenhuma conta conectada.{" "}
          <Link href="/contas" className="underline hover:text-amber-700">Conectar conta</Link>
        </span>
      ) : connectedSellers.length === 1 ? (
        // Uma conta só: mostra, sem menu de troca (não há para onde trocar).
        <span className="font-medium text-foreground">{activeSeller.ml_nickname}</span>
      ) : (
        <select
          aria-label="Conta ativa"
          value={activeSeller.id}
          onChange={(e) => {
            const next = connectedSellers.find((s) => s.id === e.target.value)
            if (next) setActiveSeller(next)
          }}
          className="h-8 rounded-md border border-border bg-card px-2 font-medium text-foreground"
        >
          {connectedSellers.map((s) => (
            <option key={s.id} value={s.id}>{s.ml_nickname}</option>
          ))}
        </select>
      )}

      {activeSeller && (
        <Link href="/contas" className="ml-auto text-xs text-slate-500 hover:text-foreground">
          Gerenciar contas
        </Link>
      )}
    </div>
  )
}
```

- [ ] **Step 4: Layout**

Em `(dashboard)/layout.tsx`, importar `AccountBar` e trocar o `<main>` por:

```tsx
        <main className="flex-1 min-w-0 flex flex-col overflow-hidden">
          <AccountBar />
          <div className="flex-1 overflow-y-auto p-6">
            <div key={pathname} className="animate-in fade-in duration-200">
              {children}
            </div>
          </div>
        </main>
```

(A barra fica fora da área que rola: fixa de verdade, não `sticky`.)

- [ ] **Step 5: Sidebar sem o seletor**

Remover a função `SellerSelector` inteira e a linha `<SellerSelector collapsed={collapsed} />` do rodapé; remover os imports que ficarem sem uso (`useRef`, `useEffect`, `ChevronDown`, `Check`, `useSeller`; atenção: `ShoppingBag` continua em uso em `NAV_ITEMS` desde a Task 5). O `npx next lint` acusa import sem uso.

- [ ] **Step 6: Verificar**

Run: `cd frontend && npx tsc --noEmit && npx next lint && npx next build`
Expected: limpo. Conferir com `grep -rn "SellerSelector" frontend/src` → nenhuma ocorrência.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/contexts/SellerContext.tsx frontend/src/components/layout/AccountBar.tsx "frontend/src/app/(dashboard)/layout.tsx" frontend/src/components/layout/Sidebar.tsx frontend/src/lib/api/sellers.ts
git commit -m "feat(contas): barra de conta fixa no topo; contexto só ativa contas conectadas e reseta queries ao trocar"
```

---

### Task 7: Página de contas — conectar em nova aba, desconectar com confirmação, reconectar, retorno do OAuth

**Files:**
- Modify: `frontend/src/app/(dashboard)/contas/page.tsx` (reescrever)
- Modify: `frontend/src/app/(dashboard)/settings/page.tsx:185-203` (`handleConnect` em nova aba; remover o bloco `ml_connected`)

**Interfaces:**
- Consumes: `useSeller()` (Task 6), `disconnectSeller` (Task 6), `getDashboard` com `is_active` (Task 3/5), `getMLConnectUrl` (`lib/api/auth.ts`), callback voltando em `/contas?ml_connected=true` (Task 4).

- [ ] **Step 1: Reescrever `contas/page.tsx`**

```tsx
"use client"

import { useEffect, useState } from "react"
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import Link from "next/link"
import { toast } from "sonner"
import { formatDistanceToNow } from "date-fns"
import { ptBR } from "date-fns/locale"
import { Loader2, ShoppingBag, Check, ExternalLink, Plus, Unplug } from "lucide-react"
import { getDashboard, disconnectSeller, type SellerDashboardEntry } from "@/lib/api/sellers"
import { getMLConnectUrl } from "@/lib/api/auth"
import { useSeller } from "@/contexts/SellerContext"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"

const STATUS_LABELS: Record<string, string> = {
  draft: "Rascunho",
  generating_title: "Gerando título",
  pending_title_approval: "Aguard. título",
  predicting_category: "Prevendo cat.",
  pending_seller_attributes: "Aguard. atributos",
  pending_description: "Aguard. descrição",
  generating_images: "Gerando imagens",
  pending_raw_photos: "Aguard. fotos",
  pending_ai_engine: "Aguard. motor de IA",
  pending_image_approval: "Aguard. imagens",
  generating_description: "Gerando descrição",
  ready_to_publish: "Pronto p/ publicar",
  publishing: "Publicando",
  published: "Publicado",
  published_paused: "Pausado",
  failed: "Falhou",
}

const STATUS_COLORS: Record<string, string> = {
  published: "bg-green-100 text-green-700",
  failed: "bg-red-100 text-red-700",
  ready_to_publish: "bg-blue-100 text-blue-700",
  draft: "bg-slate-100 text-slate-600",
}

/**
 * Abre a autorização do ML em aba nova: o operador não perde a aplicação.
 * O callback do backend manda a aba nova para /contas?ml_connected=true;
 * esta aba recarrega a lista ao receber foco de novo (ver o efeito abaixo).
 */
async function openMLAuthorization(): Promise<void> {
  const url = await getMLConnectUrl()
  window.open(url, "_blank", "noopener")
}

function SellerCard({ entry, onDisconnect }: { entry: SellerDashboardEntry; onDisconnect: (e: SellerDashboardEntry) => void }) {
  const { activeSeller, setActiveSeller, sellers } = useSeller()
  const [connecting, setConnecting] = useState(false)
  const isActive = activeSeller?.id === entry.seller_id
  const seller = sellers.find((s) => s.id === entry.seller_id)
  const statusEntries = Object.entries(entry.listings_by_status).sort((a, b) => b[1] - a[1])

  const reconnect = async () => {
    setConnecting(true)
    try {
      await openMLAuthorization()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Erro ao obter URL de conexão")
    } finally {
      setConnecting(false)
    }
  }

  return (
    <Card className={isActive ? "border-green-300 ring-1 ring-green-200" : !entry.is_active ? "opacity-80" : ""}>
      <CardHeader className="pb-3">
        <div className="flex items-start justify-between gap-2">
          <div className="flex items-center gap-2 min-w-0">
            <ShoppingBag className={`w-4 h-4 flex-shrink-0 ${entry.is_active ? "text-yellow-500" : "text-slate-400"}`} />
            <CardTitle className="text-base truncate">{entry.ml_nickname}</CardTitle>
            {isActive && (
              <span className="text-xs font-medium text-green-700 bg-green-100 px-2 py-0.5 rounded-full">Ativa</span>
            )}
            {!entry.is_active && (
              <span className="text-xs font-medium text-slate-600 bg-slate-100 px-2 py-0.5 rounded-full">Desconectada</span>
            )}
          </div>
          {entry.is_active && !isActive && seller && (
            <Button variant="outline" size="sm" onClick={() => setActiveSeller(seller)}>
              <Check className="w-3.5 h-3.5 mr-1" />
              Usar
            </Button>
          )}
        </div>
        <p className="text-sm text-slate-500">
          {entry.total_listings} anúncio{entry.total_listings !== 1 ? "s" : ""}
          {entry.last_activity_at && (
            <> · última atividade {formatDistanceToNow(new Date(entry.last_activity_at), { addSuffix: true, locale: ptBR })}</>
          )}
        </p>
      </CardHeader>

      <CardContent className="space-y-3">
        {statusEntries.length === 0 ? (
          <p className="text-sm text-slate-400 italic">Nenhum anúncio ainda.</p>
        ) : (
          <div className="flex flex-wrap gap-1.5">
            {statusEntries.map(([status, count]) => (
              <span
                key={status}
                className={`inline-flex items-center gap-1 text-xs font-medium px-2 py-1 rounded-full ${STATUS_COLORS[status] ?? "bg-slate-100 text-slate-600"}`}
              >
                <span>{count}</span>
                <span>{STATUS_LABELS[status] ?? status}</span>
              </span>
            ))}
          </div>
        )}

        <div className="flex flex-wrap items-center gap-2 pt-1">
          {isActive && (
            <Button asChild variant="ghost" size="sm" className="px-0 text-slate-500 hover:text-foreground">
              <Link href="/listings">Ver anúncios desta conta →</Link>
            </Button>
          )}
          {entry.is_active ? (
            <Button variant="ghost" size="sm" className="ml-auto text-red-600 hover:text-red-700" onClick={() => onDisconnect(entry)}>
              <Unplug className="w-3.5 h-3.5 mr-1.5" />
              Desconectar
            </Button>
          ) : (
            <Button variant="outline" size="sm" className="ml-auto" onClick={reconnect} disabled={connecting}>
              {connecting ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <ExternalLink className="w-3.5 h-3.5 mr-1.5" />}
              Reconectar
            </Button>
          )}
        </div>
      </CardContent>
    </Card>
  )
}

export default function ContasPage() {
  const queryClient = useQueryClient()
  const { activeSeller, reload } = useSeller()
  const [connecting, setConnecting] = useState(false)
  const [confirming, setConfirming] = useState<SellerDashboardEntry | null>(null)

  const { data, isLoading } = useQuery({
    queryKey: ["dashboard"],
    queryFn: getDashboard,
    refetchInterval: 15_000,
  })

  // Volta do OAuth (a aba nova cai aqui com ?ml_connected=true) e foco na aba
  // original: recarrega a lista de contas nos dois casos.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    if (params.get("ml_connected") === "true") {
      window.history.replaceState({}, "", "/contas")
      toast.success("Conta do Mercado Livre conectada.")
      void reload().then(() => queryClient.invalidateQueries({ queryKey: ["dashboard"] }))
    }
    const onFocus = () => {
      void reload().then(() => queryClient.invalidateQueries({ queryKey: ["dashboard"] }))
    }
    window.addEventListener("focus", onFocus)
    return () => window.removeEventListener("focus", onFocus)
  }, [reload, queryClient])

  const connect = async () => {
    setConnecting(true)
    try {
      await openMLAuthorization()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Erro ao obter URL de conexão")
    } finally {
      setConnecting(false)
    }
  }

  const disconnectMutation = useMutation({
    mutationFn: (id: string) => disconnectSeller(id),
    onSuccess: async (_seller, id) => {
      const eraAtiva = activeSeller?.id === id
      setConfirming(null)
      toast.success("Conta desconectada. O histórico foi mantido.")
      // `reload` troca a ativa (ou limpa) quando a desconectada era a ativa;
      // aí as queries precisam ser refeitas com o X-Seller-ID novo.
      await reload()
      if (eraAtiva) await queryClient.resetQueries()
      else await queryClient.invalidateQueries({ queryKey: ["dashboard"] })
    },
    onError: (err) => {
      toast.error(err instanceof Error ? err.message : "Erro ao desconectar")
    },
  })

  return (
    <div>
      <div className="mb-6 flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <ShoppingBag className="w-5 h-5 text-slate-600" />
          <h1 className="text-2xl font-bold text-foreground">Contas</h1>
        </div>
        <Button size="sm" onClick={connect} disabled={connecting}>
          {connecting ? <Loader2 className="w-4 h-4 animate-spin" /> : <><Plus className="w-4 h-4 mr-1" />Conectar conta</>}
        </Button>
      </div>

      {isLoading ? (
        <div className="flex items-center gap-2 text-slate-500">
          <Loader2 className="w-4 h-4 animate-spin" />
          <span className="text-sm">Carregando...</span>
        </div>
      ) : !data || data.sellers.length === 0 ? (
        <div className="text-center py-16">
          <ShoppingBag className="w-10 h-10 text-slate-300 mx-auto mb-3" />
          <p className="text-slate-500">Nenhuma conta conectada.</p>
          <Button className="mt-4" onClick={connect} disabled={connecting}>
            <ExternalLink className="w-4 h-4 mr-2" />
            Conectar conta Mercado Livre
          </Button>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
          {data.sellers.map((entry) => (
            <SellerCard key={entry.seller_id} entry={entry} onDisconnect={setConfirming} />
          ))}
        </div>
      )}

      {confirming && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
          role="dialog"
          aria-modal="true"
          aria-labelledby="disconnect-title"
        >
          <div className="w-full max-w-md rounded-lg border border-border bg-card p-5 shadow-xl space-y-4">
            <h2 id="disconnect-title" className="text-base font-semibold">
              Desconectar {confirming.ml_nickname}?
            </h2>
            <ul className="text-sm text-slate-600 dark:text-slate-300 list-disc pl-5 space-y-1">
              <li>O histórico fica: anúncios, produtos e imagens desta conta continuam no sistema.</li>
              <li>Os anúncios desta conta param de ser publicados até reconectar.</li>
              <li>Reconectar exige autorizar de novo no Mercado Livre.</li>
            </ul>
            <div className="flex justify-end gap-2">
              <Button variant="outline" size="sm" onClick={() => setConfirming(null)} disabled={disconnectMutation.isPending}>
                Cancelar
              </Button>
              <Button
                size="sm"
                className="bg-red-600 hover:bg-red-700 text-white"
                onClick={() => disconnectMutation.mutate(confirming.seller_id)}
                disabled={disconnectMutation.isPending}
              >
                {disconnectMutation.isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : "Desconectar"}
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
```

> Se `Unplug` não existir na versão instalada do `lucide-react`, usar `PlugZap` ou `LogOut`; conferir com `grep -c "Unplug" frontend/node_modules/lucide-react/dist/lucide-react.d.ts`.

- [ ] **Step 2: `settings/page.tsx`**

`handleConnect` passa a abrir em nova aba e liberar o botão:

```ts
  const handleConnect = async () => {
    setConnecting(true)
    try {
      const url = await getMLConnectUrl()
      // Aba nova: o operador não perde a aplicação. O callback devolve a aba
      // nova em /contas?ml_connected=true; a lista daqui refaz no foco
      // (refetchOnWindowFocus já está ligado na query "sellers").
      window.open(url, "_blank", "noopener")
    } catch (err) {
      const message = err instanceof Error ? err.message : "Erro ao obter URL de conexão"
      toast.error(message)
    } finally {
      setConnecting(false)
    }
  }
```

Remover o bloco `if (typeof window !== "undefined") { ... ml_connected ... }` (o callback não volta mais para `/settings`). Se `reload` ficar sem uso no destructuring de `useSeller()`, retirar.

- [ ] **Step 3: Verificar**

Run: `cd frontend && npx tsc --noEmit && npx next lint && npx next build 2>&1 | tail -30`
Expected: limpo; a lista de rotas do build inclui `/contas` e não inclui uma segunda entrada para `/`.

- [ ] **Step 4: Commit**

```bash
git add "frontend/src/app/(dashboard)/contas/page.tsx" "frontend/src/app/(dashboard)/settings/page.tsx"
git commit -m "feat(contas): conectar em nova aba, desconectar com confirmação, reconectar e retorno do OAuth"
```

---

### Task 8: Documentação

**Files:**
- Modify: `CLAUDE.md` (tabela de endpoints: `POST /sellers/{id}/disconnect`; tabela de rotas do frontend: `/contas` no lugar de `/` como painel; `/` = redireciona para `/listings`; migração `b8e2d4f6a1c3` na lista; `FRONTEND_URL` agora volta para `/contas`; a linha da porta 8011 deixa de dizer "Ainda sem vhost" — o vhost está no ar desde 2026-09-13, `/api` → 8010 e `/` → 8011)

- [ ] **Step 1: Editar** as cinco menções acima, no estilo do arquivo (uma linha cada, sem reescrever seções).

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: contas, desconexão, retorno do OAuth em /contas e vhost do frontend no ar"
```
