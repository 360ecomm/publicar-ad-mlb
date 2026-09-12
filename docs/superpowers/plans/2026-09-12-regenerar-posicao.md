# Regenerar UMA posição de imagem — plano de implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Um endpoint que regenera UMA posição (0..4) do esquema de 5 posições de um anúncio em `pending_image_approval`, com trava contra duplo clique, sem tocar nas outras posições e sem reativar o fluxo automático.

**Architecture:** O corpo de cada posição sai de `_gerar_cinco_posicoes` para `_gerar_posicao(db, listing, ctx, numero, alvo=None)` sobre um `_ContextoGeracao` montado uma vez; a geração completa vira um laço de 0 a 4 sobre a mesma função (comportamento idêntico, provado pelos testes existentes intocados). A regeneração insere um placeholder `ListingImage(status="generating")` protegido por índice único parcial, enfileira uma task Celery nova que preenche o placeholder no lugar e apaga as linhas não aprovadas que ocupavam a posição. O anúncio permanece em `pending_image_approval`; aprovações são bloqueadas com 409 enquanto houver placeholder.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.0 async, Alembic, Celery 5, pytest + pytest-asyncio, Postgres 16 (testes reais no banco dedicado `publicar_test`).

**Spec:** `docs/superpowers/specs/2026-09-12-regenerar-posicao.md`

## Global Constraints

- Branch `feat/regenerar-posicao` (já criada a partir de `a199b56`). **Sem merge, sem deploy.**
- **Os testes existentes de geração de imagens passam sem uma linha alterada.** Se algum precisar mudar, PARE e reporte ao Daniel antes de alterá-lo. Linhas de base: `482 passed, 56 skipped` (sem `TEST_DATABASE_URL`) e `538 passed` (com ela).
- Numeração da posição na API: **0 a 4** (= `sort_order`).
- Status novos de `ListingImage`: `generating` (placeholder) e `generation_failed` (não produziu imagem). `validation_failed` continua sendo "saiu e reprovou no QA". Nunca usar `failed` numa imagem.
- O worker de regeneração **nunca** escreve `listing.status` nem `listing.error_message`. Nunca `generating_images`, `pending_ai_engine` ou `pending_raw_photos`.
- Linha anterior da posição: apagada só no **sucesso** (`status="uploaded"` na nova), e só as **não aprovadas** que existiam **antes** da regeneração começar. `asset_key` de cada apagada no log.
- Custo: chamada de regeneração sai com `task=image_edit_regen` no log `ai_cost`.
- Copy do card (LLM) só é pedida quando a posição regenerada é a **2**. Não persistir copy.
- Mensagens de bloqueio próprias, nunca "estado inválido": `"Regeneração em andamento na posição {N}; aguarde."` (endpoint) e `"Regeneração em andamento na posição {N}; aguarde a conclusão antes de aprovar."` (aprovações).
- Imports de serviços dentro do worker ficam **em nível de função** (os testes fazem patch no atributo do módulo de origem: `app.services.image_engines.openai_edit_engine.OpenAIEditEngine`, `app.services.image_deterministic_service.try_deterministic_cover`, `app.services.r2_asset_service.store_candidate_bytes`, `app.services.image_service.MLPictureService`, `app.services.ai.service.get_ai_provider`).
- Comandos rodam dentro do container: `docker compose exec -T backend pytest -q -p no:cacheprovider <arquivo>`. Testes de Postgres real: `docker exec publicaradmlb-backend-1 sh -c 'TEST_DATABASE_URL="${DATABASE_URL%/*}/publicar_test" pytest -q -p no:cacheprovider tests/<arquivo>'` — **nunca duas suítes ao mesmo tempo** contra `publicar_test`.
- Commits em Conventional Commits, PT-BR, terminando com:
  ```
  Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01SH3e8RRtcuMAh84XhvfKy8
  ```

---

## Mapa de arquivos

| Arquivo | Responsabilidade nesta feature |
|---|---|
| `backend/app/services/ai/cost_log.py` | `ContextVar` separada com o rótulo `task=` do motor de imagem (`image_edit` / `image_edit_regen`) |
| `backend/app/services/image_engines/openai_edit_engine.py` | Lê o rótulo em vez do literal `"image_edit"` |
| `backend/app/models/listing_image.py` | Vocabulário (`GENERATING_STATUS`, `GENERATION_FAILED_STATUS`, `POSITION_KINDS`) + índice `uq_listing_images_generating_slot` |
| `backend/alembic/versions/a1d7c3e9f5b2_indice_slot_regeneracao.py` | Migração do índice, sobre `f7b3e9c1d2a5` |
| `backend/app/workers/tasks/image_tasks.py` | Extração (`_ContextoGeracao`, `_montar_contexto`, `_gerar_posicao`, `_persistir_linha`, `_carregar_fotos_brutas`) + task `regenerate_position` |
| `backend/app/services/listing_service.py` | `regenerate_position` (placeholder + 409s + enfileira) e bloqueio nas duas aprovações |
| `backend/app/api/v1/endpoints/listings.py` | `POST /{listing_id}/images/positions/{posicao}/regenerate` → 202 `ImageOut` |
| `backend/tests/test_regenerar_posicao_custo.py` | Rótulo de custo |
| `backend/tests/test_migracao_indice_regeneracao.py` | Migração + unicidade real do placeholder |
| `backend/tests/test_regenerar_posicao_worker.py` | `_gerar_posicao` isolada (com e sem `alvo`) + orquestração do worker com mocks |
| `backend/tests/test_regenerar_posicao_service.py` | Service, bloqueio das aprovações e rota (sem banco) |
| `backend/tests/test_regenerar_posicao_pg.py` | Ponta a ponta em Postgres real |
| `CLAUDE.md` | Endpoint, migração, vocabulário, testes, pendências |

---

### Task 1: Rótulo de custo `image_edit_regen`

**Files:**
- Modify: `backend/app/services/ai/cost_log.py`
- Modify: `backend/app/services/image_engines/openai_edit_engine.py:133-134`
- Test: `backend/tests/test_regenerar_posicao_custo.py`

**Interfaces:**
- Produces: `set_image_edit_task(task: str | None) -> None`, `image_edit_task() -> str`, constantes `IMAGE_EDIT_TASK_DEFAULT = "image_edit"` e `IMAGE_EDIT_TASK_REGEN = "image_edit_regen"` em `app.services.ai.cost_log`. O worker (Task 4) chama `set_image_edit_task(IMAGE_EDIT_TASK_REGEN)`.
- **Não** alterar `set_cost_context`/`cost_context`: `tests/test_ai_cost_log.py:217` fixa o dicionário exato `{"listing_id", "sku"}`.

- [ ] **Step 1: Escrever o teste que falha**

```python
# backend/tests/test_regenerar_posicao_custo.py
"""Custo da regeneracao de UMA posicao sai no log `ai_cost` com
`task=image_edit_regen`, separado do `image_edit` do lote — e' o numero que
decide entre melhorar o prompt ou seguir regenerando (decisao 6 da spec
2026-09-12-regenerar-posicao).

O rotulo vive numa ContextVar PROPRIA, nao dentro de `cost_context()`: o
dicionario daquele e' contrato fixado em `test_ai_cost_log.py`.
"""
import logging
from unittest.mock import AsyncMock, patch

import pytest

from tests.test_ai_cost_log import _USAGE_IMG, _registros, _resposta_openai


@pytest.fixture(autouse=True)
def _rotulo_limpo():
    from app.services.ai.cost_log import set_cost_context, set_image_edit_task
    set_cost_context(listing_id=None, sku=None)
    set_image_edit_task(None)
    yield
    set_image_edit_task(None)


class TestRotuloDoCusto:
    def test_padrao_continua_image_edit(self):
        from app.services.ai.cost_log import IMAGE_EDIT_TASK_DEFAULT, image_edit_task
        assert image_edit_task() == "image_edit" == IMAGE_EDIT_TASK_DEFAULT

    def test_rotulo_de_regeneracao(self):
        from app.services.ai.cost_log import IMAGE_EDIT_TASK_REGEN, image_edit_task, set_image_edit_task
        set_image_edit_task(IMAGE_EDIT_TASK_REGEN)
        assert image_edit_task() == "image_edit_regen"

    def test_none_volta_ao_padrao(self):
        from app.services.ai.cost_log import image_edit_task, set_image_edit_task
        set_image_edit_task("image_edit_regen")
        set_image_edit_task(None)
        assert image_edit_task() == "image_edit"

    def test_nao_mexe_no_cost_context(self):
        """`cost_context()` e' contrato (test_ai_cost_log fixa o dicionario)."""
        from app.services.ai.cost_log import cost_context, set_cost_context, set_image_edit_task
        set_cost_context(listing_id="lid", sku="T38")
        set_image_edit_task("image_edit_regen")
        assert cost_context() == {"listing_id": "lid", "sku": "T38"}


class TestMotorUsaORotulo:
    async def _editar(self):
        from app.services.image_engines.openai_edit_engine import OpenAIEditEngine
        mock_post = AsyncMock(return_value=_resposta_openai(_USAGE_IMG))
        with patch("httpx.AsyncClient") as cls:
            cls.return_value.__aenter__.return_value.post = mock_post
            engine = OpenAIEditEngine()
            with patch.object(engine.settings, "openai_image_model", "gpt-image-2"):
                await engine.edit(images=[b"a"], prompt="p", n=1, size="1200x1200")

    @pytest.mark.asyncio
    async def test_com_rotulo_de_regeneracao(self, caplog):
        from app.services.ai.cost_log import IMAGE_EDIT_TASK_REGEN, set_cost_context, set_image_edit_task
        caplog.set_level(logging.INFO, logger="ai_cost")
        set_cost_context(listing_id="lid-r", sku="T38")
        set_image_edit_task(IMAGE_EDIT_TASK_REGEN)

        await self._editar()

        (c,) = _registros(caplog)
        assert c["provider"] == "openai" and c["task"] == "image_edit_regen"
        assert c["listing_id"] == "lid-r" and c["sku"] == "T38"

    @pytest.mark.asyncio
    async def test_sem_rotulo_continua_image_edit(self, caplog):
        caplog.set_level(logging.INFO, logger="ai_cost")
        await self._editar()
        (c,) = _registros(caplog)
        assert c["task"] == "image_edit"
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `docker compose exec -T backend pytest -q -p no:cacheprovider tests/test_regenerar_posicao_custo.py`
Expected: FAIL com `ImportError: cannot import name 'set_image_edit_task'`.

- [ ] **Step 3: Implementar**

Em `backend/app/services/ai/cost_log.py`, depois de `_ctx = ...`:

```python
# Rotulo `task=` que o MOTOR DE IMAGEM usa na linha `ai_cost`. E' uma
# ContextVar separada de `_ctx`, de proposito: `cost_context()` e' contrato
# (o dicionario exato e' fixado em teste) e o motor nao conhece a task Celery
# que o chamou. O worker de regeneracao de UMA posicao fixa
# `IMAGE_EDIT_TASK_REGEN` no inicio; o lote nao fixa nada e sai como sempre.
IMAGE_EDIT_TASK_DEFAULT = "image_edit"
IMAGE_EDIT_TASK_REGEN = "image_edit_regen"

_image_edit_task: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "ai_cost_image_edit_task", default=None
)


def set_image_edit_task(task: str | None) -> None:
    """Fixa o rotulo das proximas chamadas ao motor de imagem nesta task; None volta ao padrao."""
    _image_edit_task.set(task)


def image_edit_task() -> str:
    return _image_edit_task.get() or IMAGE_EDIT_TASK_DEFAULT
```

Em `backend/app/services/image_engines/openai_edit_engine.py`:

```python
from app.services.ai.cost_log import image_edit_task, log_ai_cost
...
        log_ai_cost(
            provider="openai", task=image_edit_task(), model=model,
```

- [ ] **Step 4: Rodar e ver passar**

Run: `docker compose exec -T backend pytest -q -p no:cacheprovider tests/test_regenerar_posicao_custo.py tests/test_ai_cost_log.py`
Expected: todos PASS (os de `test_ai_cost_log.py` continuam verdes, sem alteração).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/ai/cost_log.py backend/app/services/image_engines/openai_edit_engine.py backend/tests/test_regenerar_posicao_custo.py
git commit -m "feat(ai-cost): rótulo image_edit_regen para a chamada de regeneração de posição"
```

---

### Task 2: Vocabulário no model + índice único parcial do placeholder + migração

**Files:**
- Modify: `backend/app/models/listing_image.py` (constantes após `CANDIDATE_SORT_ORDER_FLOOR = 90`; índice em `__table_args__`)
- Create: `backend/alembic/versions/a1d7c3e9f5b2_indice_slot_regeneracao.py`
- Test: `backend/tests/test_migracao_indice_regeneracao.py`

**Interfaces:**
- Produces em `app.models.listing_image`: `GENERATING_STATUS = "generating"`, `GENERATION_FAILED_STATUS = "generation_failed"`, `POSITION_KINDS: dict[int, str] = {0: "cover_ai", 1: "presentation_ai", 2: "benefits_ai", 3: "detail_ai", 4: "specs_ai"}`, índice `uq_listing_images_generating_slot`.
- Consumido por Tasks 3, 4, 5, 6.

- [ ] **Step 1: Escrever os testes que falham**

```python
# backend/tests/test_migracao_indice_regeneracao.py
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
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `docker compose exec -T backend pytest -q -p no:cacheprovider tests/test_migracao_indice_regeneracao.py`
Expected: `test_revisao_encadeia_no_head_atual` FAIL (migração não existe) e `test_vocabulario_do_model` FAIL com `ImportError`; os de banco pulam.

- [ ] **Step 3: Implementar o model**

Em `backend/app/models/listing_image.py`, logo após `CANDIDATE_SORT_ORDER_FLOOR = 90`:

```python
# --------------------------------------------------------------------------
# Regeneracao de UMA posicao (spec docs/superpowers/specs/2026-09-12-regenerar-posicao.md).
# --------------------------------------------------------------------------
# Placeholder inserido pelo endpoint ANTES de enfileirar a task. E' a trava
# contra duplo clique (indice unico parcial `uq_listing_images_generating_slot`
# em `__table_args__`) e o que a tela le para mostrar "gerando". Era o
# default da coluna `status` e nenhum caminho o gravava.
GENERATING_STATUS = "generating"
# Regeneracao que NAO produziu imagem (motor falhou, fotos brutas ausentes).
# Distinto de `validation_failed` (imagem saiu e reprovou no QA) e de
# `failed` (exclusivo de `Listing.status`). Motivo em `validation_error`.
GENERATION_FAILED_STATUS = "generation_failed"
# Kind oficial de cada posicao do esquema de 5 posicoes, pela posicao
# (`sort_order`). Unica definicao: o worker gera com estes kinds e o
# endpoint de regeneracao cria o placeholder com o kind da posicao pedida.
POSITION_KINDS: dict[int, str] = {
    0: COVER_AI_KIND,
    1: "presentation_ai",
    2: "benefits_ai",
    3: "detail_ai",
    4: SPECS_AI_KIND,
}
```

Em `__table_args__`, depois do `Index("ix_listing_images_listing_id", "listing_id")`:

```python
        # Trava da regeneracao de UMA posicao (migration a1d7c3e9f5b2): no
        # maximo UM placeholder `generating` por (anuncio, posicao). O segundo
        # clique falha no commit e `ListingService.regenerate_position`
        # devolve 409. Predicado literal, identico ao da migration.
        Index(
            "uq_listing_images_generating_slot",
            "listing_id",
            "sort_order",
            unique=True,
            postgresql_where=text("status = 'generating'"),
        ),
```

- [ ] **Step 4: Criar a migração**

```python
# backend/alembic/versions/a1d7c3e9f5b2_indice_slot_regeneracao.py
"""indice unico parcial do placeholder de regeneracao de UMA posicao

`uq_listing_images_generating_slot (listing_id, sort_order) WHERE status = 'generating'`.

Trava contra duplo clique em "gerar de novo" numa posicao: o endpoint insere
um `ListingImage(status='generating')` e faz commit ANTES de enfileirar a
task; o segundo insert falha aqui (IntegrityError) e o service devolve 409
"Regeneracao em andamento na posicao N; aguarde." Mesma tecnica dos slots
de capa/ficha (3d8f1b2c9e47). `status='generating'` era o default da coluna
e nenhum caminho o gravava, entao nenhuma linha existente entra no
predicado. Ver docs/superpowers/specs/2026-09-12-regenerar-posicao.md.

DDL identico ao de `ListingImage.__table_args__` (fonte do `create_all` nos
testes).

Revision ID: a1d7c3e9f5b2
Revises: f7b3e9c1d2a5
Create Date: 2026-09-12 20:00:00.000000
"""
from alembic import op

revision = 'a1d7c3e9f5b2'
down_revision = 'f7b3e9c1d2a5'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        'uq_listing_images_generating_slot', 'listing_images', ['listing_id', 'sort_order'],
        unique=True, postgresql_where="status = 'generating'",
    )


def downgrade() -> None:
    op.drop_index('uq_listing_images_generating_slot', table_name='listing_images')
```

- [ ] **Step 5: Rodar (sem e com banco) e ver passar**

Run: `docker compose exec -T backend pytest -q -p no:cacheprovider tests/test_migracao_indice_regeneracao.py`
Expected: 2 passed, 4 skipped.

Run: `docker exec publicaradmlb-backend-1 sh -c 'TEST_DATABASE_URL="${DATABASE_URL%/*}/publicar_test" pytest -q -p no:cacheprovider tests/test_migracao_indice_regeneracao.py tests/test_migracao_indices_fk.py tests/test_promocao_indice_unico.py'`
Expected: todos passed (`test_migracao_indices_fk.py` continua verde: o índice novo não é tocado pela migração dele).

Run: `docker compose exec -T backend alembic heads`
Expected: `a1d7c3e9f5b2 (head)` — head único. **Não** rodar `alembic upgrade` no banco de dev nesta task (o `create_all` dos testes já prova o DDL); aplicar em dev fica para a revisão final.

- [ ] **Step 6: Commit**

```bash
git add backend/app/models/listing_image.py backend/alembic/versions/a1d7c3e9f5b2_indice_slot_regeneracao.py backend/tests/test_migracao_indice_regeneracao.py
git commit -m "feat(listing-images): vocabulário da regeneração e índice único do placeholder generating"
```

---

### Task 3: Extração — `_ContextoGeracao`, `_gerar_posicao`, `_persistir_linha`, `_carregar_fotos_brutas`

**Files:**
- Modify: `backend/app/workers/tasks/image_tasks.py` (`_try_i2i_generation`, `_campos_das_posicoes`, `_salvar_posicao`, `_gerar_cinco_posicoes`; novos: `_ContextoGeracao`, `_carregar_fotos_brutas`, `_montar_contexto`, `_persistir_linha`, `_gerar_posicao`)
- Test: `backend/tests/test_regenerar_posicao_worker.py` (parte 1)

**Interfaces:**
- Produces (em `app.workers.tasks.image_tasks`):
  - `async _carregar_fotos_brutas(db, listing) -> tuple[list[bytes], str] | None`
  - `async _campos_das_posicoes(db, listing, com_copy: bool = True) -> dict`
  - `@dataclass _ContextoGeracao(engine, canvas: str, profile, fotos: list[bytes], sku: str, access_token: str, campos: dict | None, base: bytes | None, base_ia: bytes)`
  - `async _montar_contexto(db, listing, access_token, profile, fotos, sku, *, com_campos: bool = True, com_copy: bool = True) -> _ContextoGeracao`
  - `def _persistir_linha(db, listing, *, alvo, **valores) -> None`
  - `async _salvar_posicao(db, listing, sku, kind, sort_order, gerado, access_token, requires_white_bg: bool, alvo=None) -> bool` (assinatura antiga + `alvo` keyword)
  - `async _gerar_posicao(db, listing, ctx, numero: int, alvo=None) -> bool` — True se subiu ao ML (IA ou fallback). Com `alvo`, preenche o placeholder em vez de `db.add`.
  - `_gerar_cinco_posicoes(db, listing, access_token, profile, fotos, sku) -> int` — **assinatura e comportamento inalterados**.
- Consumes: `GENERATING_STATUS`, `POSITION_KINDS`, `COVER_DETERMINISTIC_KIND` (Task 2).

- [ ] **Step 1: Escrever os testes que falham**

```python
# backend/tests/test_regenerar_posicao_worker.py
"""Regeneracao de UMA posicao — lado do worker.

Parte 1 (esta task): `_gerar_posicao` isolada, extraida de
`_gerar_cinco_posicoes` sem mudar o caminho completo (a prova disso sao os
testes existentes de imagem, intocados). Com `alvo`, preenche o placeholder
em vez de abrir linha nova.

Parte 2 (Task 4): orquestracao de `_regenerate_position_async`.

Reusa o ambiente de mocks de `test_cinco_posicoes.py`.
"""
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from tests.test_cinco_posicoes import (
    _Ambiente,
    _atributos_sku38,
    _db_com_atributos,
    _fotos,
    _listing,
    _salvos,
)


def _placeholder(sort_order: int, kind: str):
    from app.models.listing_image import ListingImage
    return ListingImage(
        id=uuid4(), listing_id=uuid4(), status="generating", approved=False,
        sort_order=sort_order, kind=kind,
    )


async def _contexto(db, **kw):
    from app.services.image_position_profiles import PERFIL_PERFUMARIA
    from app.workers.tasks.image_tasks import _montar_contexto
    return await _montar_contexto(db, _listing(), "tok", PERFIL_PERFUMARIA, _fotos(), "38", **kw)


class TestGerarPosicaoIsolada:
    @pytest.mark.asyncio
    async def test_posicao_2_sozinha_faz_uma_chamada_e_grava_uma_linha(self):
        from app.workers.tasks.image_tasks import _gerar_posicao

        db = _db_com_atributos(_atributos_sku38())
        with _Ambiente() as amb:
            ctx = await _contexto(db)
            ok = await _gerar_posicao(db, _listing(), ctx, 2)

        assert ok is True
        assert len(amb.prompts) == 1
        (img,) = _salvos(db)
        assert (img.kind, img.sort_order, img.approved) == ("benefits_ai", 2, False)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("numero,kind", [(0, "cover_ai"), (1, "presentation_ai"), (3, "detail_ai"), (4, "specs_ai")])
    async def test_cada_posicao_grava_o_kind_da_sua_posicao(self, numero, kind):
        from app.workers.tasks.image_tasks import _gerar_posicao

        db = _db_com_atributos(_atributos_sku38())
        with _Ambiente() as amb:
            ctx = await _contexto(db)
            assert await _gerar_posicao(db, _listing(), ctx, numero) is True

        assert len(amb.prompts) == 1
        (img,) = _salvos(db)
        assert (img.kind, img.sort_order) == (kind, numero)

    @pytest.mark.asyncio
    async def test_sem_copy_nao_chama_o_llm(self):
        """Regenerar 0, 1, 3 ou 4 nao pode pagar a copy dos cards."""
        db = _db_com_atributos(_atributos_sku38())
        with _Ambiente(), patch(
            "app.services.image_card_copy_service.generate_card_copy",
            new_callable=AsyncMock, return_value=[],
        ) as copy:
            ctx = await _contexto(db, com_campos=True, com_copy=False)
        copy.assert_not_awaited()
        assert ctx.campos is not None and ctx.campos["beneficios"] is None
        assert ctx.campos["nome"] == "Fatal Black For Her"

    @pytest.mark.asyncio
    async def test_com_copy_chama_o_llm_uma_vez(self):
        db = _db_com_atributos(_atributos_sku38())
        with _Ambiente(), patch(
            "app.services.image_card_copy_service.generate_card_copy",
            new_callable=AsyncMock, return_value=[],
        ) as copy:
            await _contexto(db, com_campos=True, com_copy=True)
        copy.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_sem_campos_nao_consulta_atributos(self):
        db = _db_com_atributos(_atributos_sku38())
        with _Ambiente():
            ctx = await _contexto(db, com_campos=False)
        assert ctx.campos is None
        db.execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_alvo_e_preenchido_em_vez_de_nova_linha(self):
        from app.workers.tasks.image_tasks import _gerar_posicao

        db = _db_com_atributos(_atributos_sku38())
        alvo = _placeholder(3, "detail_ai")
        with _Ambiente():
            ctx = await _contexto(db, com_campos=False)
            ok = await _gerar_posicao(db, _listing(), ctx, 3, alvo=alvo)

        assert ok is True
        db.add.assert_not_called()
        assert alvo.status == "uploaded"
        assert alvo.ml_picture_id.startswith("pic-")
        assert alvo.asset_key == "asset-key-teste"
        assert (alvo.kind, alvo.sort_order, alvo.approved) == ("detail_ai", 3, False)
        assert alvo.validation_error is None

    @pytest.mark.asyncio
    async def test_alvo_reprovado_no_qa_guarda_evidencia(self):
        from app.workers.tasks.image_tasks import _gerar_posicao

        db = _db_com_atributos(_atributos_sku38())
        alvo = _placeholder(3, "detail_ai")
        with _Ambiente(qa_reprova=True):
            ctx = await _contexto(db, com_campos=False)
            ok = await _gerar_posicao(db, _listing(), ctx, 3, alvo=alvo)

        assert ok is False
        db.add.assert_not_called()
        assert alvo.status == "validation_failed"
        assert alvo.ml_picture_id is None
        assert alvo.asset_key == "asset-key-teste", "bytes crus vao ao R2 mesmo reprovados"
        assert alvo.validation_error

    @pytest.mark.asyncio
    async def test_posicao_0_com_ia_falhando_cai_no_fallback_dentro_do_alvo(self):
        """As 2 tentativas da IA falham (indices 0 e 1) -> capa deterministica
        preenche o PROPRIO placeholder, kind cover_deterministic."""
        from app.workers.tasks.image_tasks import _gerar_posicao

        db = _db_com_atributos(_atributos_sku38())
        alvo = _placeholder(0, "cover_ai")
        with _Ambiente(falhar_em={0, 1}) as amb:
            ctx = await _contexto(db, com_campos=False)
            ok = await _gerar_posicao(db, _listing(), ctx, 0, alvo=alvo)

        assert ok is True
        assert len(amb.prompts) == 2
        db.add.assert_not_called()
        assert (alvo.kind, alvo.status, alvo.sort_order) == ("cover_deterministic", "uploaded", 0)
        assert alvo.ml_picture_id and alvo.approved is False

    @pytest.mark.asyncio
    async def test_posicao_0_com_ia_falhando_sem_alvo_grava_linha_nova(self):
        """Mesmo fallback do caminho completo, sem alvo: linha nova."""
        from app.workers.tasks.image_tasks import _gerar_posicao

        db = _db_com_atributos(_atributos_sku38())
        with _Ambiente(falhar_em={0, 1}):
            ctx = await _contexto(db, com_campos=False)
            assert await _gerar_posicao(db, _listing(), ctx, 0) is True
        (img,) = _salvos(db)
        assert (img.kind, img.sort_order) == ("cover_deterministic", 0)

    @pytest.mark.asyncio
    async def test_posicao_2_sem_copy_devolve_false_sem_chamar_o_motor(self):
        from app.workers.tasks.image_tasks import _gerar_posicao

        db = _db_com_atributos(_atributos_sku38())
        with _Ambiente() as amb:
            ctx = await _contexto(db, com_campos=True, com_copy=False)
            assert await _gerar_posicao(db, _listing(), ctx, 2) is False
        assert amb.prompts == []
        db.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_posicao_fora_de_0_a_4_levanta(self):
        from app.workers.tasks.image_tasks import _gerar_posicao

        db = _db_com_atributos(_atributos_sku38())
        with _Ambiente():
            ctx = await _contexto(db, com_campos=False)
            with pytest.raises(ValueError):
                await _gerar_posicao(db, _listing(), ctx, 5)


class TestCarregarFotosBrutas:
    @pytest.mark.asyncio
    async def test_sem_config_devolve_none(self):
        from app.workers.tasks.image_tasks import _carregar_fotos_brutas

        db = AsyncMock()
        db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))
        assert await _carregar_fotos_brutas(db, _listing()) is None

    @pytest.mark.asyncio
    async def test_devolve_fotos_e_sku(self):
        from app.workers.tasks.image_tasks import _carregar_fotos_brutas

        cfg = MagicMock(); cfg.raw_base_url = "https://b/x"
        db = AsyncMock()
        db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=cfg)))
        with patch("app.services.seller_image_source_service.fetch_all_raw_photos",
                   new_callable=AsyncMock, return_value={"38": [b"1", b"2"]}):
            assert await _carregar_fotos_brutas(db, _listing()) == ([b"1", b"2"], "38")
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `docker compose exec -T backend pytest -q -p no:cacheprovider tests/test_regenerar_posicao_worker.py`
Expected: FAIL com `ImportError: cannot import name '_montar_contexto'` (e similares).

- [ ] **Step 3: Implementar a extração em `image_tasks.py`**

Adicionar no topo do arquivo, junto dos imports:

```python
from dataclasses import dataclass
```

Substituir o corpo de `_try_i2i_generation` (mantendo a docstring atual) por:

```python
    from app.services.image_position_profiles import profile_for_category

    carregado = await _carregar_fotos_brutas(db, listing)
    if carregado is None:
        return None
    fotos, sku = carregado

    profile = profile_for_category(listing.ml_category_id)
    logger.info(
        "roteamento listing_id=%s categoria=%s perfil=%s caminho=cinco_posicoes",
        listing.id, listing.ml_category_id, profile.nome,
    )
    return await _gerar_cinco_posicoes(db, listing, access_token, profile, fotos, sku)
```

Logo antes de `_try_i2i_generation`, a função nova (é o trecho que saiu dela, na mesma ordem de checagens):

```python
async def _carregar_fotos_brutas(db, listing) -> tuple[list[bytes], str] | None:
    """`(fotos, sku)` do unico SKU do anuncio, lidas do bucket do seller agora.

    None quando o seller nao tem `SellerImageConfig`, o anuncio nao resolve
    SKU, ou faltam as fotos minimas — o chamador decide o standby. Extraido
    de `_try_i2i_generation` para a regeneracao de UMA posicao reler as fotos
    do mesmo jeito (foto trocada pelo seller entra na regeneracao).
    """
    from sqlalchemy import select

    from app.models.seller_image_config import SellerImageConfig
    from app.services.seller_image_source_service import (
        fetch_all_raw_photos,
        resolve_listing_skus,
    )

    config = (
        await db.execute(
            select(SellerImageConfig).where(SellerImageConfig.seller_id == listing.seller_id)
        )
    ).scalar_one_or_none()
    if config is None:
        return None

    skus = await resolve_listing_skus(listing)
    if not skus:
        return None

    raw_photos_by_sku = await fetch_all_raw_photos(config.raw_base_url, skus)
    if raw_photos_by_sku is None:
        return None

    if len(skus) != 1:
        raise RuntimeError(
            f"anuncio com {len(skus)} SKUs nao e suportado pelo esquema de 5 posicoes"
        )
    return raw_photos_by_sku[skus[0]], skus[0]
```

Em `_campos_das_posicoes`, assinatura `async def _campos_das_posicoes(db, listing, com_copy: bool = True):` e a linha da copy:

```python
    # `com_copy=False` (regeneracao de posicao != 2): nao paga a chamada ao
    # LLM; `beneficios` sai None e a posicao 2 e' pulada.
    cards = await generate_card_copy(listing, atributos) if com_copy else []
```

Nova função, antes de `_salvar_posicao`:

```python
def _persistir_linha(db, listing, *, alvo, **valores) -> None:
    """Linha nova (`alvo=None`, geracao completa — `db.add` identico ao de
    sempre) ou preenche o placeholder `generating` da regeneracao no lugar,
    para o id que a tela ja recebeu continuar valendo."""
    from app.models.listing_image import ListingImage

    if alvo is None:
        db.add(ListingImage(listing_id=listing.id, **valores))
        return
    alvo.validation_error = None
    for campo, valor in valores.items():
        setattr(alvo, campo, valor)
```

Em `_salvar_posicao`: assinatura ganha `alvo=None` no fim; os dois `db.add(ListingImage(...))` viram `_persistir_linha(db, listing, alvo=alvo, ...)` com **exatamente** os mesmos kwargs de hoje (sem `listing_id`, que o helper põe):

```python
async def _salvar_posicao(db, listing, sku, kind, sort_order, gerado, access_token,
                          requires_white_bg: bool, alvo=None):
    ...
    if preparado is None:
        asset_key = await store_candidate_bytes(
            gerado, db=db, seller_id=listing.seller_id, sku=sku, kind=kind
        )
        _persistir_linha(
            db, listing, alvo=alvo,
            status="validation_failed", validation_error=veredito.reason, approved=False,
            sort_order=sort_order, kind=kind, source_sku=sku, asset_key=asset_key,
        )
        logger.warning(...)  # inalterado
        return False

    ml_picture_id = await MLPictureService().upload(preparado, access_token)
    asset_key = await store_candidate_bytes(
        preparado, db=db, seller_id=listing.seller_id, sku=sku, kind=kind
    )
    _persistir_linha(
        db, listing, alvo=alvo,
        ml_picture_id=ml_picture_id, status="uploaded",
        approved=False, sort_order=sort_order, kind=kind, source_sku=sku,
        asset_key=asset_key,
    )
    return True
```

(O `from app.models.listing_image import ListingImage` de `_salvar_posicao` pode sair, já que o helper importa.)

Substituir `_gerar_cinco_posicoes` inteira por este bloco (a docstring atual de `_gerar_cinco_posicoes` é mantida nela):

```python
@dataclass
class _ContextoGeracao:
    """Tudo que as 5 posicoes compartilham, montado UMA vez por geracao.

    A regeneracao de UMA posicao monta o mesmo contexto e chama
    `_gerar_posicao` uma vez — e' o que garante que a posicao regenerada sai
    do mesmo prompt, mesma base e mesmo canvas da geracao completa.
    """
    engine: object
    canvas: str
    profile: object
    fotos: list
    sku: str
    access_token: str
    campos: dict | None   # None = nao calculado (regeneracao de 0 ou 3)
    base: bytes | None    # capa deterministica preparada (QA ok) ou None
    base_ia: bytes        # `base`, ou a 1a foto bruta se nao houver capa


async def _montar_contexto(db, listing, access_token, profile, fotos, sku, *,
                           com_campos: bool = True, com_copy: bool = True) -> _ContextoGeracao:
    """Motor, canvas, campos e capa deterministica — na mesma ordem de antes.

    `com_campos=False` pula a consulta de atributos e a copy (posicoes 0 e 3
    nao usam nada disso); `com_copy=False` consulta atributos mas nao paga o
    LLM (posicoes 1 e 4). A geracao completa usa os dois padroes.

    Imports em nivel de funcao de proposito: os testes fazem patch no
    atributo do modulo de origem.
    """
    from app.services.image_deterministic_service import try_deterministic_cover
    from app.services.image_engines.openai_edit_engine import OpenAIEditEngine

    engine = OpenAIEditEngine()
    canvas = profile.canvas
    campos = await _campos_das_posicoes(db, listing, com_copy=com_copy) if com_campos else None

    # Base deterministica: recorte do pixel original, sem IA — o rotulo nela e
    # sempre fiel, e e por isso que as posicoes 1 e 5 partem dela.
    cover_bytes = try_deterministic_cover(fotos[0])
    base, _ = (
        _prepare_image_for_upload(cover_bytes, requires_white_bg=True)
        if cover_bytes is not None else (None, None)
    )
    logger.info(
        "cinco_posicoes listing_id=%s sku=%s capa_deterministica=%s",
        listing.id, sku, "hit" if base is not None else "miss",
    )
    return _ContextoGeracao(
        engine=engine, canvas=canvas, profile=profile, fotos=fotos, sku=sku,
        access_token=access_token, campos=campos, base=base,
        base_ia=base if base is not None else fotos[0],
    )


async def _gerar_posicao(db, listing, ctx: _ContextoGeracao, numero: int, alvo=None) -> bool:
    """Gera UMA posicao (0..4) do esquema. Devolve True se subiu ao ML.

    O corpo de cada posicao e' o que estava embutido em `_gerar_cinco_posicoes`,
    inclusive o fallback da capa deterministica na 0 e as condicoes de pulo
    (sem nome -> 1, sem copy -> 2, sem ficha -> 4). `alvo` e' o placeholder
    `generating` da regeneracao: preenchido no lugar, em vez de `db.add`.
    Sem `alvo`, comportamento identico ao anterior.
    """
    from app.models.listing_image import (
        COVER_DETERMINISTIC_KIND,
        GENERATING_STATUS,
        POSITION_KINDS,
    )
    from app.services.cover_variant_service import _pick_prompt
    from app.services.image_position_profiles import detail_caption_for
    from app.services.image_position_prompts import (
        build_benefits_prompt,
        build_detail_prompt,
        build_presentation_prompt,
    )
    from app.services.seller_image_source_service import pick_detail_source
    from app.services.specs_variant_service import _build_specs_prompt

    if numero not in POSITION_KINDS:
        raise ValueError(f"posicao {numero!r} fora do esquema de 5 posicoes (0..4)")

    engine, canvas, fotos, sku = ctx.engine, ctx.canvas, ctx.fotos, ctx.sku
    access_token = ctx.access_token
    campos = ctx.campos or {}

    if numero == 0:
        # Posicao 1 — capa por IA, sempre branca (ver `_pick_prompt`).
        async def _pos1():
            return (await engine.edit(images=[ctx.base_ia], prompt=_pick_prompt(), n=1, size=canvas))[0]

        gerado = await _tentar("1-capa", listing.id, _pos1)
        if gerado is not None and await _salvar_posicao(
            db, listing, sku, "cover_ai", 0, gerado, access_token, requires_white_bg=True, alvo=alvo
        ):
            return True
        if ctx.base is None:
            return False
        # Fallback interno: a capa deterministica so aparece quando a IA falha.
        from app.services.image_service import MLPictureService
        from app.services.r2_asset_service import store_candidate_bytes

        ml_picture_id = await MLPictureService().upload(ctx.base, access_token)
        asset_key = await store_candidate_bytes(
            ctx.base, db=db, seller_id=listing.seller_id, sku=sku, kind=COVER_DETERMINISTIC_KIND
        )
        # Se a IA produziu e o QA reprovou, o placeholder ja virou a linha
        # `validation_failed` (evidencia); o fallback vai numa linha nova,
        # como no caminho completo. Se a IA nem produziu, o placeholder
        # ainda esta `generating` e o fallback o preenche.
        destino = alvo if (alvo is not None and alvo.status == GENERATING_STATUS) else None
        _persistir_linha(
            db, listing, alvo=destino,
            ml_picture_id=ml_picture_id, status="uploaded",
            approved=False, sort_order=0, kind=COVER_DETERMINISTIC_KIND,
            source_sku=sku, asset_key=asset_key,
        )
        logger.warning("cinco_posicoes listing_id=%s posicao=1 usou_fallback_deterministico", listing.id)
        return True

    if numero == 1:
        # Posicao 2 — apresentacao. Unica que recebe TODAS as fotos brutas.
        if not campos.get("nome"):
            return False
        prompt2 = build_presentation_prompt(campos["nome"], campos["marca"], campos["volume"])

        async def _pos2():
            return (await engine.edit(images=fotos, prompt=prompt2, n=1, size=canvas))[0]

        gerado = await _tentar("2-apresentacao", listing.id, _pos2)
        if gerado is None:
            return False
        return await _salvar_posicao(
            db, listing, sku, POSITION_KIND_PRESENTATION, 1, gerado, access_token,
            requires_white_bg=False, alvo=alvo,
        )

    if numero == 2:
        # Posicao 3 — beneficios. Copy do LLM, a mesma ja usada no card Pillow.
        beneficios = campos.get("beneficios")
        if beneficios is None:
            return False
        prompt3 = build_benefits_prompt(beneficios.title, beneficios.bullets)

        async def _pos3():
            return (await engine.edit(images=[fotos[0]], prompt=prompt3, n=1, size=canvas))[0]

        gerado = await _tentar("3-beneficios", listing.id, _pos3)
        if gerado is None:
            return False
        return await _salvar_posicao(
            db, listing, sku, POSITION_KIND_BENEFITS, 2, gerado, access_token,
            requires_white_bg=False, alvo=alvo,
        )

    if numero == 3:
        # Posicao 4 — detalhe. `pick_detail_source` escolhe a 3a foto se existir.
        foto_detalhe, veio_de_extra = pick_detail_source(fotos)
        legenda = detail_caption_for(ctx.profile, sku)
        prompt4 = build_detail_prompt(legenda)
        logger.info(
            "cinco_posicoes listing_id=%s posicao=4 fonte=%s legenda=%r",
            listing.id, "extra" if veio_de_extra else "reuso_do_minimo", legenda,
        )

        async def _pos4():
            return (await engine.edit(images=[foto_detalhe], prompt=prompt4, n=1, size=canvas))[0]

        gerado = await _tentar("4-detalhe", listing.id, _pos4)
        if gerado is None:
            return False
        return await _salvar_posicao(
            db, listing, sku, POSITION_KIND_DETAIL, 3, gerado, access_token,
            requires_white_bg=False, alvo=alvo,
        )

    # numero == 4 — Posicao 5 — ficha tecnica. Bullets ancorados no value_name real.
    ficha = campos.get("ficha")
    if ficha is None:
        return False
    prompt5 = _build_specs_prompt(ficha.bullets)

    async def _pos5():
        return (await engine.edit(images=[ctx.base_ia], prompt=prompt5, n=1, size=canvas))[0]

    gerado = await _tentar("5-ficha", listing.id, _pos5)
    if gerado is None:
        return False
    return await _salvar_posicao(
        db, listing, sku, "specs_ai", 4, gerado, access_token, requires_white_bg=False, alvo=alvo
    )


async def _gerar_cinco_posicoes(db, listing, access_token, profile, fotos, sku) -> int:
    """<docstring atual, inalterada>"""
    ctx = await _montar_contexto(db, listing, access_token, profile, fotos, sku)
    salvas = 0
    for numero in range(5):
        if await _gerar_posicao(db, listing, ctx, numero):
            salvas += 1

    await db.commit()
    logger.info("cinco_posicoes listing_id=%s sku=%s salvas=%s", listing.id, sku, salvas)
    return salvas
```

- [ ] **Step 4: Rodar os novos e ver passar**

Run: `docker compose exec -T backend pytest -q -p no:cacheprovider tests/test_regenerar_posicao_worker.py`
Expected: todos PASS.

- [ ] **Step 5: A prova da extração — suíte de imagens intocada**

Run: `docker compose exec -T backend pytest -q -p no:cacheprovider tests/test_cinco_posicoes.py tests/test_perfil_padrao.py tests/test_image_tasks.py tests/test_pending_ai_engine.py tests/test_pending_raw_photos.py tests/test_sem_reuso_de_imagem.py tests/test_r2_asset_store.py tests/test_cover_variant.py tests/test_specs_variant.py tests/test_specs_card_deterministic.py tests/test_image_bytes_persistence.py`
Expected: todos PASS. `git status` mostra **nenhum** arquivo de `backend/tests/` modificado além dos novos. Se algum teste existente falhar: **NÃO** alterar o teste; parar e reportar ao Daniel com a saída.

Run: `docker compose exec -T backend pytest -q -p no:cacheprovider`
Expected: `>= 482 passed, 56 skipped` (os novos somam ao total; nenhum failed).

- [ ] **Step 6: Commit**

```bash
git add backend/app/workers/tasks/image_tasks.py backend/tests/test_regenerar_posicao_worker.py
git commit -m "refactor(image-tasks): extrai _gerar_posicao e _ContextoGeracao sem mudar a geração completa"
```

---

### Task 4: Task Celery `regenerate_position`

**Files:**
- Modify: `backend/app/workers/tasks/image_tasks.py` (adicionar no fim do arquivo)
- Test: `backend/tests/test_regenerar_posicao_worker.py` (parte 2, mesmo arquivo)

**Interfaces:**
- Produces: task Celery `app.workers.tasks.image_tasks.regenerate_position(listing_id: str, image_id: str) -> dict` (fila `images` pela rota já existente `app.workers.tasks.image_tasks.*`), `async _regenerate_position_async(listing_id, image_id) -> dict`, `async _falhar_regeneracao(db, alvo, motivo) -> dict`, `async _mark_regen_failed(image_id, error) -> None`.
- Consumes: Task 1 (`set_image_edit_task`, `IMAGE_EDIT_TASK_REGEN`), Task 2 (`GENERATING_STATUS`, `GENERATION_FAILED_STATUS`), Task 3 (`_carregar_fotos_brutas`, `_montar_contexto`, `_gerar_posicao`).
- Ordem das consultas em `_regenerate_position_async` (os testes com mock dependem dela): **1** placeholder, **2** listing, **3** anteriores, **4** seller; depois `_carregar_fotos_brutas` e `_montar_contexto` (patchados nos testes unitários).

- [ ] **Step 1: Escrever os testes que falham (anexar ao arquivo da Task 3)**

```python
# --- parte 2: orquestracao do worker -------------------------------------
from contextlib import asynccontextmanager


@asynccontextmanager
async def _sessao(db):
    yield db


def _listing_pendente():
    listing = MagicMock()
    listing.id = uuid4(); listing.seller_id = uuid4(); listing.sku_external_id = "38"
    listing.ml_category_id = "MLB6284"; listing.status = "pending_image_approval"
    listing.error_message = None
    return listing


def _linha(sort_order, status="uploaded", asset_key="k-antiga", approved=False):
    from app.models.listing_image import ListingImage
    return ListingImage(id=uuid4(), listing_id=uuid4(), status=status, approved=approved,
                        sort_order=sort_order, kind="benefits_ai", asset_key=asset_key)


def _db_worker(alvo, listing, anteriores=(), extra=()):
    """Respostas na ORDEM das consultas de `_regenerate_position_async`:
    placeholder, listing, anteriores, seller (+ `extra`, ex.: recarga do
    placeholder depois de rollback)."""
    db = AsyncMock()
    respostas = [
        MagicMock(scalar_one_or_none=MagicMock(return_value=alvo)),
        MagicMock(scalar_one=MagicMock(return_value=listing)),
        MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=list(anteriores))))),
        MagicMock(scalar_one=MagicMock(return_value=MagicMock())),
        *extra,
    ]
    db.execute = AsyncMock(side_effect=respostas)
    db.add = MagicMock(); db.commit = AsyncMock(); db.rollback = AsyncMock(); db.delete = AsyncMock()
    return db


def _patches_worker(db, gerar):
    """worker_session, token, fotos e contexto mockados; `gerar` substitui `_gerar_posicao`."""
    return [
        patch("app.database.worker_session", lambda: _sessao(db)),
        patch("app.workers.tasks.image_tasks._fetch_upload_token", new_callable=AsyncMock, return_value="tok"),
        patch("app.workers.tasks.image_tasks._carregar_fotos_brutas", new_callable=AsyncMock,
              return_value=([b"1", b"2", b"3"], "38")),
        patch("app.workers.tasks.image_tasks._montar_contexto", new_callable=AsyncMock, return_value=MagicMock()),
        patch("app.workers.tasks.image_tasks._gerar_posicao", gerar),
    ]


async def _rodar(db, gerar, listing_id, image_id):
    from contextlib import ExitStack

    from app.workers.tasks.image_tasks import _regenerate_position_async

    with ExitStack() as stack:
        for p in _patches_worker(db, gerar):
            stack.enter_context(p)
        return await _regenerate_position_async(str(listing_id), str(image_id))


class TestRegenerarPosicaoWorker:
    @pytest.mark.asyncio
    async def test_sucesso_preenche_o_placeholder_e_apaga_as_anteriores(self):
        listing = _listing_pendente()
        alvo = _placeholder(2, "benefits_ai")
        antiga = _linha(2, asset_key="CAFE085/38/benefits_ai-old.jpg")
        rotulos = []

        async def gerar(db, l, ctx, numero, alvo=None):
            from app.services.ai.cost_log import image_edit_task
            rotulos.append((numero, image_edit_task()))
            alvo.status = "uploaded"; alvo.ml_picture_id = "pic-novo"
            return True

        db = _db_worker(alvo, listing, anteriores=[antiga])
        result = await _rodar(db, gerar, listing.id, alvo.id)

        assert rotulos == [(2, "image_edit_regen")]
        db.delete.assert_awaited_once_with(antiga)
        db.commit.assert_awaited()
        assert listing.status == "pending_image_approval" and listing.error_message is None
        assert result["status"] == "uploaded" and result["removidas"] == 1

    @pytest.mark.asyncio
    async def test_contexto_pede_campos_e_copy_so_quando_a_posicao_exige(self):
        """0 e 3: sem campos. 1 e 4: campos sem copy. 2: campos com copy."""
        from app.workers.tasks.image_tasks import _regenerate_position_async

        esperado = {0: (False, False), 1: (True, False), 2: (True, True), 3: (False, False), 4: (True, False)}
        for numero, (com_campos, com_copy) in esperado.items():
            listing = _listing_pendente()
            alvo = _placeholder(numero, "x")

            async def gerar(db, l, ctx, n, alvo=None):
                alvo.status = "uploaded"; return True

            db = _db_worker(alvo, listing)
            from contextlib import ExitStack
            with ExitStack() as stack:
                patches = _patches_worker(db, gerar)
                for p in patches[:3] + patches[4:]:
                    stack.enter_context(p)
                montar = stack.enter_context(patch(
                    "app.workers.tasks.image_tasks._montar_contexto",
                    new_callable=AsyncMock, return_value=MagicMock(),
                ))
                await _regenerate_position_async(str(listing.id), str(alvo.id))
            kw = montar.await_args.kwargs
            assert (kw["com_campos"], kw["com_copy"]) == (com_campos, com_copy), numero

    @pytest.mark.asyncio
    async def test_motor_nao_produz_vira_generation_failed_e_mantem_a_anterior(self):
        listing = _listing_pendente()
        alvo = _placeholder(2, "benefits_ai")
        antiga = _linha(2)

        async def gerar(db, l, ctx, numero, alvo=None):
            return False  # 2 tentativas falharam; placeholder continua `generating`

        db = _db_worker(alvo, listing, anteriores=[antiga])
        result = await _rodar(db, gerar, listing.id, alvo.id)

        assert alvo.status == "generation_failed"
        assert alvo.validation_error
        db.delete.assert_not_awaited()
        db.commit.assert_awaited()
        assert listing.status == "pending_image_approval" and listing.error_message is None
        assert result["status"] == "generation_failed"

    @pytest.mark.asyncio
    async def test_qa_reprovou_mantem_evidencia_e_a_anterior(self):
        listing = _listing_pendente()
        alvo = _placeholder(2, "benefits_ai")
        antiga = _linha(2)

        async def gerar(db, l, ctx, numero, alvo=None):
            alvo.status = "validation_failed"; alvo.validation_error = "fundo"; alvo.asset_key = "k"
            return False

        db = _db_worker(alvo, listing, anteriores=[antiga])
        result = await _rodar(db, gerar, listing.id, alvo.id)

        assert alvo.status == "validation_failed", "nao pode ser sobrescrito por generation_failed"
        db.delete.assert_not_awaited()
        db.commit.assert_awaited()
        assert result["status"] == "validation_failed"

    @pytest.mark.asyncio
    async def test_motor_indisponivel_faz_rollback_e_generation_failed_sem_standby(self):
        from app.services.image_engines.base import ImageEngineUnavailableError

        listing = _listing_pendente()
        alvo = _placeholder(2, "benefits_ai")

        async def gerar(db, l, ctx, numero, alvo=None):
            raise ImageEngineUnavailableError("OpenAI Edits API 429: insufficient_quota")

        recarga = MagicMock(scalar_one=MagicMock(return_value=alvo))
        db = _db_worker(alvo, listing, extra=[recarga])
        result = await _rodar(db, gerar, listing.id, alvo.id)

        db.rollback.assert_awaited_once()
        assert alvo.status == "generation_failed"
        assert "insufficient_quota" in alvo.validation_error
        assert listing.status == "pending_image_approval", "nunca pending_ai_engine"
        assert listing.error_message is None
        assert result["status"] == "generation_failed"

    @pytest.mark.asyncio
    async def test_fotos_brutas_ausentes_vira_generation_failed(self):
        from contextlib import ExitStack

        from app.workers.tasks.image_tasks import _regenerate_position_async

        listing = _listing_pendente()
        alvo = _placeholder(1, "presentation_ai")
        db = _db_worker(alvo, listing)
        with ExitStack() as stack:
            stack.enter_context(patch("app.database.worker_session", lambda: _sessao(db)))
            stack.enter_context(patch("app.workers.tasks.image_tasks._fetch_upload_token",
                                      new_callable=AsyncMock, return_value="tok"))
            stack.enter_context(patch("app.workers.tasks.image_tasks._carregar_fotos_brutas",
                                      new_callable=AsyncMock, return_value=None))
            montar = stack.enter_context(patch("app.workers.tasks.image_tasks._montar_contexto",
                                               new_callable=AsyncMock))
            await _regenerate_position_async(str(listing.id), str(alvo.id))

        montar.assert_not_awaited()
        assert alvo.status == "generation_failed" and "fotos brutas" in alvo.validation_error
        assert listing.status == "pending_image_approval"

    @pytest.mark.asyncio
    async def test_placeholder_ja_consumido_e_skipped_antes_de_gastar(self):
        listing = _listing_pendente()
        alvo = _placeholder(2, "benefits_ai"); alvo.status = "rejected"
        gerar = AsyncMock()
        db = _db_worker(alvo, listing)
        result = await _rodar(db, gerar, listing.id, alvo.id)

        assert result["skipped"] is True
        gerar.assert_not_awaited()
        assert db.execute.await_count == 1
        db.delete.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_placeholder_inexistente_e_skipped(self):
        listing = _listing_pendente()
        db = _db_worker(None, listing)
        result = await _rodar(db, AsyncMock(), listing.id, uuid4())
        assert result["skipped"] is True

    @pytest.mark.asyncio
    async def test_anuncio_que_saiu_de_pending_image_approval_apaga_o_placeholder(self):
        listing = _listing_pendente(); listing.status = "generating_description"
        alvo = _placeholder(2, "benefits_ai")
        gerar = AsyncMock()
        db = _db_worker(alvo, listing)
        result = await _rodar(db, gerar, listing.id, alvo.id)

        assert result["skipped"] is True
        gerar.assert_not_awaited()
        db.delete.assert_awaited_once_with(alvo)
        db.commit.assert_awaited_once()
        assert listing.status == "generating_description", "o worker nao toca no status"

    @pytest.mark.asyncio
    async def test_mark_regen_failed_so_toca_placeholder_generating(self):
        from app.workers.tasks.image_tasks import _mark_regen_failed

        alvo = _placeholder(2, "benefits_ai")
        db = AsyncMock()
        db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=alvo)))
        db.commit = AsyncMock()
        with patch("app.database.worker_session", lambda: _sessao(db)):
            await _mark_regen_failed(str(alvo.id), "boom")
        assert alvo.status == "generation_failed" and alvo.validation_error == "boom"

        ja_pronto = _placeholder(2, "benefits_ai"); ja_pronto.status = "uploaded"
        db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=ja_pronto)))
        with patch("app.database.worker_session", lambda: _sessao(db)):
            await _mark_regen_failed(str(ja_pronto.id), "boom")
        assert ja_pronto.status == "uploaded"

    def test_task_registrada_na_fila_de_imagens(self):
        from app.workers.celery_app import celery_app
        from app.workers.tasks.image_tasks import regenerate_position

        assert regenerate_position.name == "app.workers.tasks.image_tasks.regenerate_position"
        assert celery_app.conf.task_routes["app.workers.tasks.image_tasks.*"] == {"queue": "images"}
        assert regenerate_position.max_retries == 2
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `docker compose exec -T backend pytest -q -p no:cacheprovider tests/test_regenerar_posicao_worker.py -k "Worker or mark_regen"`
Expected: FAIL com `ImportError: cannot import name '_regenerate_position_async'`.

- [ ] **Step 3: Implementar (fim de `image_tasks.py`)**

```python
# ---------------------------------------------------------------------------
# Regeneracao de UMA posicao (spec docs/superpowers/specs/2026-09-12-regenerar-posicao.md).
#
# O anuncio fica em `pending_image_approval` o tempo todo: este worker NUNCA
# escreve `listing.status` nem `listing.error_message`. `generating_images`
# reativaria o guard de `_generate_images_async`; `pending_ai_engine` /
# `pending_raw_photos` sao retomados pelo beat com a geracao COMPLETA (5
# chamadas). Falha aqui e' falha da LINHA (placeholder), nao do anuncio.
# ---------------------------------------------------------------------------

async def _falhar_regeneracao(db, alvo, motivo: str) -> dict:
    """Placeholder vira `generation_failed` com o motivo; a linha anterior da
    posicao fica como estava."""
    from app.models.listing_image import GENERATION_FAILED_STATUS

    alvo.status = GENERATION_FAILED_STATUS
    alvo.validation_error = motivo[:500]
    await db.commit()
    logger.warning(
        "regen_posicao listing_id=%s posicao=%s image_id=%s result=generation_failed reason=%s",
        alvo.listing_id, alvo.sort_order, alvo.id, motivo,
    )
    return {
        "listing_id": str(alvo.listing_id), "image_id": str(alvo.id),
        "posicao": alvo.sort_order, "status": GENERATION_FAILED_STATUS,
    }


async def _regenerate_position_async(listing_id: str, image_id: str) -> dict:
    from sqlalchemy import select

    from app.database import worker_session
    from app.models.listing import Listing
    from app.models.listing_image import GENERATING_STATUS, ListingImage
    from app.models.seller import Seller
    from app.services.ai.cost_log import (
        IMAGE_EDIT_TASK_REGEN,
        set_cost_context,
        set_image_edit_task,
    )
    from app.services.image_engines.base import ImageEngineUnavailableError
    from app.services.image_position_profiles import profile_for_category

    async with worker_session() as db:
        # 1) O placeholder e' o guard de idempotencia: retry ou dispatch duplo
        # encontra `status != generating` e desiste antes de gastar.
        alvo = (
            await db.execute(
                select(ListingImage).where(
                    ListingImage.id == image_id, ListingImage.listing_id == listing_id
                )
            )
        ).scalar_one_or_none()
        if alvo is None or alvo.status != GENERATING_STATUS:
            return {"listing_id": listing_id, "image_id": image_id, "skipped": True}

        # 2) Aprovacao venceu a corrida entre o endpoint e este worker: o
        # placeholder sai e nada e' gerado (o anuncio ja seguiu).
        listing = (
            await db.execute(select(Listing).where(Listing.id == listing_id))
        ).scalar_one()
        if listing.status != "pending_image_approval":
            await db.delete(alvo)
            await db.commit()
            return {
                "listing_id": listing_id, "image_id": image_id, "skipped": True,
                "reason": f"status={listing.status}",
            }

        posicao = alvo.sort_order
        set_cost_context(listing_id=listing.id, sku=listing.sku_external_id)
        set_image_edit_task(IMAGE_EDIT_TASK_REGEN)

        # 3) Quem ocupava a posicao ANTES de comecar: so estas podem ser
        # apagadas no sucesso. Linhas criadas pela propria regeneracao (o
        # fallback da capa, por exemplo) nunca entram aqui. Aprovada nao entra:
        # o endpoint ja recusou a posicao aprovada, e o filtro e' a segunda
        # cerca.
        anteriores = (
            await db.execute(
                select(ListingImage).where(
                    ListingImage.listing_id == listing.id,
                    ListingImage.sort_order == posicao,
                    ListingImage.id != alvo.id,
                    ListingImage.approved.is_(False),
                )
            )
        ).scalars().all()
        anteriores_info = [(a.id, a.status, a.asset_key) for a in anteriores]

        # 4) Token e fotos brutas (relidas do bucket: foto trocada entra).
        seller = (
            await db.execute(select(Seller).where(Seller.id == listing.seller_id))
        ).scalar_one()
        access_token = await _fetch_upload_token(seller, db)

        carregado = await _carregar_fotos_brutas(db, listing)
        if carregado is None:
            return await _falhar_regeneracao(
                db, alvo, "fotos brutas do SKU não encontradas no bucket do seller"
            )
        fotos, sku = carregado
        profile = profile_for_category(listing.ml_category_id)

        try:
            ctx = await _montar_contexto(
                db, listing, access_token, profile, fotos, sku,
                com_campos=posicao in (1, 2, 4), com_copy=(posicao == 2),
            )
            subiu = await _gerar_posicao(db, listing, ctx, posicao, alvo=alvo)
        except ImageEngineUnavailableError as exc:
            # Motor fora: NAO e' standby do anuncio (o beat retomaria as 5).
            # Rollback descarta o que esta tentativa tenha tocado; recarrega o
            # placeholder porque o rollback expira os objetos.
            await db.rollback()
            alvo = (
                await db.execute(select(ListingImage).where(ListingImage.id == image_id))
            ).scalar_one()
            return await _falhar_regeneracao(db, alvo, f"Motor de imagem indisponível: {exc}")

        if not subiu:
            if alvo.status == GENERATING_STATUS:
                # Nem IA nem fallback produziram nada.
                return await _falhar_regeneracao(
                    db, alvo, "o motor de imagem não produziu imagem válida para esta posição"
                )
            # IA produziu, QA reprovou: o placeholder virou `validation_failed`
            # com os bytes no R2 — evidencia para o humano. Nao foi sucesso:
            # a anterior fica.
            await db.commit()
            logger.warning(
                "regen_posicao listing_id=%s posicao=%s image_id=%s result=%s anteriores_mantidas=%s",
                listing.id, posicao, alvo.id, alvo.status, len(anteriores_info),
            )
            return {
                "listing_id": listing_id, "image_id": image_id,
                "posicao": posicao, "status": alvo.status, "removidas": 0,
            }

        # 5) Sucesso: a nova subiu ao ML. As anteriores nao aprovadas saem, com
        # o asset_key de cada uma no log (o objeto no R2 continua la).
        for a in anteriores:
            await db.delete(a)
        await db.commit()
        for (aid, astatus, akey) in anteriores_info:
            logger.info(
                "regen_posicao listing_id=%s posicao=%s apagada id=%s status=%s asset_key=%s",
                listing.id, posicao, aid, astatus, akey,
            )
        logger.info(
            "regen_posicao listing_id=%s sku=%s posicao=%s image_id=%s kind=%s result=substituida removidas=%s",
            listing.id, sku, posicao, alvo.id, alvo.kind, len(anteriores_info),
        )
        return {
            "listing_id": listing_id, "image_id": image_id, "posicao": posicao,
            "status": alvo.status, "kind": alvo.kind, "removidas": len(anteriores_info),
        }


async def _mark_regen_failed(image_id: str, error: str) -> None:
    """Ultima tentativa do Celery estourou: o placeholder (se ainda
    `generating`) vira `generation_failed`. Nunca toca no anuncio."""
    try:
        from sqlalchemy import select

        from app.database import worker_session
        from app.models.listing_image import GENERATING_STATUS, ListingImage

        async with worker_session() as db:
            alvo = (
                await db.execute(select(ListingImage).where(ListingImage.id == image_id))
            ).scalar_one_or_none()
            if alvo is not None and alvo.status == GENERATING_STATUS:
                await _falhar_regeneracao(db, alvo, error)
    except Exception as mark_exc:
        logger.error(
            "Could not mark regeneration %s as failed (original error: %s): %s",
            image_id, error, mark_exc,
        )


@celery_app.task(name="app.workers.tasks.image_tasks.regenerate_position", bind=True, max_retries=2)
def regenerate_position(self, listing_id: str, image_id: str) -> dict:
    try:
        return asyncio.run(_regenerate_position_async(listing_id, image_id))
    except Exception as exc:
        if self.request.retries >= self.max_retries:
            asyncio.run(_mark_regen_failed(image_id, str(exc)))
            raise
        raise self.retry(exc=exc, countdown=2 ** self.request.retries * 5)  # 5s, 10s
```

- [ ] **Step 4: Rodar e ver passar**

Run: `docker compose exec -T backend pytest -q -p no:cacheprovider tests/test_regenerar_posicao_worker.py`
Expected: todos PASS.

- [ ] **Step 5: Suíte inteira sem banco**

Run: `docker compose exec -T backend pytest -q -p no:cacheprovider`
Expected: nenhum failed; `56 skipped`; nenhum arquivo de teste pré-existente modificado (`git status`).

- [ ] **Step 6: Commit**

```bash
git add backend/app/workers/tasks/image_tasks.py backend/tests/test_regenerar_posicao_worker.py
git commit -m "feat(image-tasks): task regenerate_position preenche o placeholder e apaga a anterior no sucesso"
```

---

### Task 5: Service `regenerate_position` + endpoint

**Files:**
- Modify: `backend/app/services/listing_service.py` (imports; método novo depois de `resume_ai_engine`)
- Modify: `backend/app/api/v1/endpoints/listings.py` (rota nova depois de `approve_images`, antes de `publish_listing`)
- Test: `backend/tests/test_regenerar_posicao_service.py`

**Interfaces:**
- Produces: `ListingService.regenerate_position(listing: Listing, posicao: int) -> ListingImage` (placeholder já commitado; task enfileirada). Rota `POST /api/v1/listings/{listing_id}/images/positions/{posicao}/regenerate` → 202 `ImageOut`.
- Consumes: Task 2 (`GENERATING_STATUS`, `POSITION_KINDS`), Task 4 (`regenerate_position.delay(str(listing.id), str(placeholder.id))`).

- [ ] **Step 1: Escrever os testes que falham**

```python
# backend/tests/test_regenerar_posicao_service.py
"""Regeneracao de UMA posicao — service, bloqueio das aprovacoes e rota.
Sem banco (sempre roda). O comportamento com linhas reais esta em
`test_regenerar_posicao_pg.py`."""
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError


def _listing(status="pending_image_approval"):
    listing = MagicMock()
    listing.id = uuid.uuid4(); listing.seller_id = uuid.uuid4()
    listing.status = status; listing.sku_external_id = "38"
    return listing


def _db_com_linhas(linhas):
    db = AsyncMock()
    r = MagicMock(); r.scalars.return_value.all.return_value = list(linhas)
    db.execute = AsyncMock(return_value=r)
    db.add = MagicMock(); db.commit = AsyncMock(); db.rollback = AsyncMock(); db.refresh = AsyncMock()
    return db


def _linha(approved):
    img = MagicMock(); img.approved = approved; img.status = "uploaded"; return img


class TestRegenerarPosicaoService:
    @pytest.mark.asyncio
    async def test_recusa_fora_de_pending_image_approval_antes_de_consultar(self):
        from app.services.listing_service import ListingService

        db = _db_com_linhas([])
        svc = ListingService(db)
        with patch("app.workers.tasks.image_tasks.regenerate_position") as task:
            with pytest.raises(HTTPException) as exc:
                await svc.regenerate_position(_listing("ready_to_publish"), 2)
        assert exc.value.status_code == 409
        assert "pending_image_approval" in exc.value.detail and "ready_to_publish" in exc.value.detail
        db.execute.assert_not_awaited(); task.delay.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("posicao", [-1, 5, 90])
    async def test_recusa_posicao_fora_de_0_a_4(self, posicao):
        from app.services.listing_service import ListingService

        db = _db_com_linhas([])
        with pytest.raises(HTTPException) as exc:
            await ListingService(db).regenerate_position(_listing(), posicao)
        assert exc.value.status_code == 422
        db.execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_recusa_posicao_aprovada_sem_criar_placeholder_nem_enfileirar(self):
        from app.services.listing_service import ListingService

        db = _db_com_linhas([_linha(approved=True)])
        with patch("app.workers.tasks.image_tasks.regenerate_position") as task:
            with pytest.raises(HTTPException) as exc:
                await ListingService(db).regenerate_position(_listing(), 2)
        assert exc.value.status_code == 409 and "aprovada" in exc.value.detail
        db.add.assert_not_called(); db.commit.assert_not_awaited(); task.delay.assert_not_called()

    @pytest.mark.asyncio
    async def test_cria_placeholder_commita_e_enfileira_nessa_ordem(self):
        from app.models.listing_image import ListingImage
        from app.services.listing_service import ListingService

        listing = _listing()
        db = _db_com_linhas([_linha(approved=False)])
        ordem = []
        db.commit = AsyncMock(side_effect=lambda: ordem.append("commit"))
        with patch("app.workers.tasks.image_tasks.regenerate_position") as task:
            task.delay = MagicMock(side_effect=lambda *a: ordem.append("delay"))
            placeholder = await ListingService(db).regenerate_position(listing, 2)

        assert isinstance(placeholder, ListingImage)
        assert (placeholder.status, placeholder.kind, placeholder.sort_order, placeholder.approved) == (
            "generating", "benefits_ai", 2, False)
        assert placeholder.listing_id == listing.id and placeholder.source_sku == "38"
        db.add.assert_called_once_with(placeholder)
        assert ordem == ["commit", "delay"], "enfileira so DEPOIS do commit"
        task.delay.assert_called_once_with(str(listing.id), str(placeholder.id))

    @pytest.mark.asyncio
    @pytest.mark.parametrize("posicao,kind", [(0, "cover_ai"), (1, "presentation_ai"), (3, "detail_ai"), (4, "specs_ai")])
    async def test_kind_do_placeholder_e_o_da_posicao(self, posicao, kind):
        from app.services.listing_service import ListingService

        db = _db_com_linhas([])
        with patch("app.workers.tasks.image_tasks.regenerate_position"):
            placeholder = await ListingService(db).regenerate_position(_listing(), posicao)
        assert placeholder.kind == kind and placeholder.sort_order == posicao

    @pytest.mark.asyncio
    async def test_segundo_clique_vira_409_proprio_sem_enfileirar(self):
        from app.services.listing_service import ListingService

        db = _db_com_linhas([_linha(approved=False)])
        db.commit = AsyncMock(side_effect=IntegrityError("INSERT", {}, Exception("uq_listing_images_generating_slot")))
        with patch("app.workers.tasks.image_tasks.regenerate_position") as task:
            with pytest.raises(HTTPException) as exc:
                await ListingService(db).regenerate_position(_listing(), 2)
        assert exc.value.status_code == 409
        assert exc.value.detail == "Regeneração em andamento na posição 2; aguarde."
        db.rollback.assert_awaited_once(); task.delay.assert_not_called()


def test_rota_declarada_com_202_e_image_out():
    from app.main import app

    rotas = {
        (route.path, tuple(sorted(route.methods))): route
        for route in app.routes if getattr(route, "methods", None)
    }
    rota = rotas[("/api/v1/listings/{listing_id}/images/positions/{posicao}/regenerate", ("POST",))]
    assert rota.status_code == 202
    assert rota.response_model.__name__ == "ImageOut"
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `docker compose exec -T backend pytest -q -p no:cacheprovider tests/test_regenerar_posicao_service.py`
Expected: FAIL com `AttributeError: 'ListingService' object has no attribute 'regenerate_position'` e `KeyError` na rota.

- [ ] **Step 3: Implementar o service**

Imports em `listing_service.py`:

```python
from sqlalchemy.exc import IntegrityError
...
from app.models.listing_image import (
    CANDIDATE_SORT_ORDER_FLOOR,
    COVER_SORT_ORDER,
    GENERATING_STATUS,
    POSITION_KINDS,
    PROMOTABLE_COVER_KINDS,
    ListingImage,
)
```

Método, depois de `resume_ai_engine`:

```python
    async def regenerate_position(self, listing: Listing, posicao: int) -> ListingImage:
        """Regenera UMA posicao (0..4) do esquema de 5 posicoes.

        Insere um placeholder `ListingImage(status="generating")` e faz
        commit ANTES de enfileirar: o placeholder e' a trava contra duplo
        clique (indice unico parcial `uq_listing_images_generating_slot`) e o
        que a tela le como "gerando". So posicao NAO aprovada — imagem
        aprovada nao e' substituida por tras do operador. So em
        `pending_image_approval`; o anuncio nao muda de status (ver o
        cabecalho da task em image_tasks.py). Spec:
        docs/superpowers/specs/2026-09-12-regenerar-posicao.md.
        """
        if listing.status != "pending_image_approval":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Regeneração de imagem disponível apenas no status "
                    f"'pending_image_approval' (atual: '{listing.status}')"
                ),
            )
        if posicao not in POSITION_KINDS:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Posição inválida: informe um número de 0 a 4",
            )
        ocupantes = (
            await self.db.execute(
                select(ListingImage).where(
                    ListingImage.listing_id == listing.id,
                    ListingImage.sort_order == posicao,
                )
            )
        ).scalars().all()
        if any(img.approved for img in ocupantes):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"A posição {posicao} já está aprovada; imagem aprovada não é regenerada",
            )

        placeholder = ListingImage(
            listing_id=listing.id,
            status=GENERATING_STATUS,
            approved=False,
            sort_order=posicao,
            kind=POSITION_KINDS[posicao],
            source_sku=listing.sku_external_id,
        )
        self.db.add(placeholder)
        try:
            await self.db.commit()
        except IntegrityError:
            # `uq_listing_images_generating_slot`: ja existe placeholder
            # `generating` nesta posicao — outro clique venceu.
            await self.db.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Regeneração em andamento na posição {posicao}; aguarde.",
            )
        await self.db.refresh(placeholder)

        from app.workers.tasks.image_tasks import regenerate_position
        regenerate_position.delay(str(listing.id), str(placeholder.id))
        return placeholder
```

- [ ] **Step 4: Implementar o endpoint**

Em `endpoints/listings.py`, logo após `approve_images`:

```python
@router.post(
    "/{listing_id}/images/positions/{posicao}/regenerate",
    response_model=ImageOut,
    status_code=202,
)
async def regenerate_image_position(
    listing_id: UUID,
    posicao: int,
    active_seller=Depends(get_active_seller),
    db: AsyncSession = Depends(get_db),
):
    """Regenera UMA posição (0..4, = `sort_order`) do esquema de 5 posições.

    Devolve 202 com o placeholder (`status="generating"`); a imagem chega
    pela task e aparece em `GET /listings/{id}` com `status="uploaded"`,
    `validation_failed` (QA) ou `generation_failed` (motor). 409 fora de
    `pending_image_approval`, em posição aprovada ou com regeneração já em
    andamento; 422 fora de 0..4. Ver `ListingService.regenerate_position`.
    """
    svc = ListingService(db)
    listing = await svc.get_or_404(listing_id, active_seller.id)
    placeholder = await svc.regenerate_position(listing, posicao)
    return ImageOut.model_validate(placeholder)
```

- [ ] **Step 5: Rodar e ver passar**

Run: `docker compose exec -T backend pytest -q -p no:cacheprovider tests/test_regenerar_posicao_service.py tests/test_status_counts_rota.py`
Expected: todos PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/listing_service.py backend/app/api/v1/endpoints/listings.py backend/tests/test_regenerar_posicao_service.py
git commit -m "feat(listings): POST /images/positions/{posicao}/regenerate cria o placeholder e enfileira"
```

---

### Task 6: Bloquear `approve_images` e `bulk_approve_images` durante regeneração

**Files:**
- Modify: `backend/app/services/listing_service.py` (`approve_images` após carregar `images`; `bulk_approve_images` antes do UPDATE; função de módulo `_mensagem_regeneracao_em_andamento`)
- Test: `backend/tests/test_regenerar_posicao_service.py` (anexar)

**Interfaces:**
- Produces: `_mensagem_regeneracao_em_andamento(posicoes: list[int]) -> str` = `"Regeneração em andamento na posição {a, b}; aguarde a conclusão antes de aprovar."`
- **Restrição de mock:** `tests/test_lote_para_em_ready_to_publish.py` responde qualquer consulta de `bulk_approve_images` após a primeira com um `MagicMock` (`rowcount = 1`). `MagicMock().scalars().all()` é um `MagicMock`, **verdadeiro** num `if`, mas **iterável vazio** (`__iter__` padrão devolve `iter([])`). Por isso a checagem em massa **itera** o resultado (`sorted(...)`) em vez de testar a veracidade. Em `approve_images` não há consulta nova: usa a lista `images` já carregada (`mock_img.status` é `MagicMock`, diferente de `"generating"`). Não "simplificar" isso.

- [ ] **Step 1: Escrever os testes que falham (anexar ao arquivo da Task 5)**

```python
class TestAprovacaoBloqueadaDuranteRegeneracao:
    def _img(self, sort_order, status, approved=False):
        img = MagicMock(); img.id = uuid.uuid4(); img.sort_order = sort_order
        img.status = status; img.approved = approved; img.kind = "benefits_ai"; img.ml_picture_id = "p"
        return img

    @pytest.mark.asyncio
    async def test_approve_images_409_com_mensagem_propria(self):
        from app.services.listing_service import ListingService

        listing = _listing()
        normal, gerando = self._img(1, "uploaded"), self._img(2, "generating")
        db = _db_com_linhas([normal, gerando])
        with patch("app.workers.tasks.ai_tasks.generate_description") as gen:
            with pytest.raises(HTTPException) as exc:
                await ListingService(db).approve_images(listing, [normal.id], user_id=uuid.uuid4())
        assert exc.value.status_code == 409
        assert exc.value.detail == "Regeneração em andamento na posição 2; aguarde a conclusão antes de aprovar."
        assert listing.status == "pending_image_approval"
        db.add.assert_not_called(), "nenhum evento de revisao"
        db.commit.assert_not_awaited(); gen.delay.assert_not_called()
        assert normal.approved is False and gerando.status == "generating"

    @pytest.mark.asyncio
    async def test_approve_images_lista_todas_as_posicoes_em_regeneracao(self):
        from app.services.listing_service import ListingService

        db = _db_com_linhas([self._img(4, "generating"), self._img(0, "generating"), self._img(1, "uploaded")])
        with pytest.raises(HTTPException) as exc:
            await ListingService(db).approve_images(_listing(), [uuid.uuid4()], user_id=uuid.uuid4())
        assert "posição 0, 4;" in exc.value.detail

    @pytest.mark.asyncio
    async def test_bulk_approve_images_item_falho_com_mensagem_propria(self):
        from app.services.listing_service import ListingService

        listing = _listing()
        db = AsyncMock(); chamadas = [0]

        async def execute_side(stmt):
            chamadas[0] += 1
            r = MagicMock()
            if chamadas[0] == 1:
                r.scalar_one_or_none = MagicMock(return_value=listing)
            elif chamadas[0] == 2:   # consulta das posicoes em regeneracao
                r.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=[3])))
            else:
                raise AssertionError("o UPDATE nao pode rodar com regeneracao em andamento")
            return r

        db.execute = execute_side; db.commit = AsyncMock(); db.add = MagicMock(); db.rollback = AsyncMock()
        with patch("app.workers.tasks.ai_tasks.generate_description") as gen:
            result = await ListingService(db, listing.seller_id).bulk_approve_images(
                [listing.id], user_id=uuid.uuid4())

        assert result.processed == 0 and result.failed == 1
        assert result.results[0].error == "Regeneração em andamento na posição 3; aguarde a conclusão antes de aprovar."
        assert listing.status == "pending_image_approval"
        db.commit.assert_not_awaited(); gen.delay.assert_not_called()

    @pytest.mark.asyncio
    async def test_bulk_sem_regeneracao_segue_normal(self):
        from app.services.listing_service import ListingService

        listing = _listing()
        db = AsyncMock(); chamadas = [0]

        async def execute_side(stmt):
            chamadas[0] += 1
            r = MagicMock()
            if chamadas[0] == 1:
                r.scalar_one_or_none = MagicMock(return_value=listing)
            elif chamadas[0] == 2:
                r.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
            else:
                r.rowcount = 5
            return r

        db.execute = execute_side; db.commit = AsyncMock(); db.add = MagicMock(); db.rollback = AsyncMock()
        with patch("app.workers.tasks.ai_tasks.generate_description") as gen:
            result = await ListingService(db, listing.seller_id).bulk_approve_images(
                [listing.id], user_id=uuid.uuid4())
        assert result.processed == 1 and listing.status == "generating_description"
        gen.delay.assert_called_once()


def test_mensagem_de_regeneracao_em_andamento():
    from app.services.listing_service import _mensagem_regeneracao_em_andamento
    assert _mensagem_regeneracao_em_andamento([2]) == (
        "Regeneração em andamento na posição 2; aguarde a conclusão antes de aprovar.")
    assert _mensagem_regeneracao_em_andamento([0, 4]).startswith("Regeneração em andamento na posição 0, 4;")
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `docker compose exec -T backend pytest -q -p no:cacheprovider tests/test_regenerar_posicao_service.py -k "Bloqueada or mensagem"`
Expected: FAIL (a aprovação passa e `generate_description.delay` é chamado; `ImportError` na mensagem).

- [ ] **Step 3: Implementar**

Função de módulo em `listing_service.py`, antes de `class ListingService`:

```python
def _mensagem_regeneracao_em_andamento(posicoes: list[int]) -> str:
    """Bloqueio das aprovacoes enquanto ha placeholder `generating`. Mensagem
    PROPRIA, nao "estado invalido": o operador precisa saber que e'
    temporario e qual posicao esta sendo refeita."""
    lista = ", ".join(str(p) for p in posicoes)
    return f"Regeneração em andamento na posição {lista}; aguarde a conclusão antes de aprovar."
```

Em `approve_images`, logo depois de `images = result.scalars().all()` e **antes** do bloco `approved_set = ...`:

```python
        # Regeneracao de posicao em andamento: aprovar agora marcaria o
        # placeholder como `rejected` e o worker desistiria depois de ja ter
        # pago a chamada (ou publicaria sem a posicao refeita). Bloqueia com
        # mensagem propria, antes de qualquer escrita. Usa a lista ja
        # carregada — nenhuma consulta a mais.
        em_regeneracao = sorted(img.sort_order for img in images if img.status == GENERATING_STATUS)
        if em_regeneracao:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=_mensagem_regeneracao_em_andamento(em_regeneracao),
            )
```

Em `bulk_approve_images`, logo depois do `continue` de `"estado inválido"` e **antes** do comentário que precede o `sa_update(ListingImage)`:

```python
                # Mesmo bloqueio do individual, com item falho e mensagem
                # propria em vez de derrubar o lote. `sorted(...)` ITERA o
                # resultado de proposito (ver Task 6 do plano
                # 2026-09-12-regenerar-posicao): nao trocar por `if r:`.
                em_regeneracao = sorted(
                    (
                        await self.db.execute(
                            select(ListingImage.sort_order).where(
                                ListingImage.listing_id == lid,
                                ListingImage.status == GENERATING_STATUS,
                            )
                        )
                    ).scalars().all()
                )
                if em_regeneracao:
                    results.append(BulkItemResult(
                        listing_id=lid, success=False,
                        error=_mensagem_regeneracao_em_andamento(em_regeneracao),
                    ))
                    continue
```

- [ ] **Step 4: Rodar e ver passar — inclusive os testes existentes das aprovações**

Run: `docker compose exec -T backend pytest -q -p no:cacheprovider tests/test_regenerar_posicao_service.py tests/test_lote_para_em_ready_to_publish.py tests/test_bulk_service.py tests/test_cover_sort_order_invariant.py tests/test_specs_variant.py`
Expected: todos PASS, sem alteração nos pré-existentes.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/listing_service.py backend/tests/test_regenerar_posicao_service.py
git commit -m "feat(listings): aprovação individual e em massa recusam com 409 próprio durante regeneração"
```

---

### Task 7: Ponta a ponta em Postgres real

**Files:**
- Test: `backend/tests/test_regenerar_posicao_pg.py`

**Interfaces:**
- Consumes: tudo das Tasks 2–6; `_preparar_banco`/`_semear` de `tests/test_bulk_approve_por_posicao.py`; `_Ambiente` de `tests/test_cinco_posicoes.py`.

- [ ] **Step 1: Escrever os testes**

```python
# backend/tests/test_regenerar_posicao_pg.py
"""Regeneracao de UMA posicao, ponta a ponta, em Postgres REAL: endpoint
(service) cria o placeholder, o worker preenche, a linha anterior sai so no
sucesso, as outras posicoes nao sao tocadas, o anuncio nao muda de status,
aprovacoes ficam bloqueadas, o custo sai rotulado.

So roda com `TEST_DATABASE_URL` apontando para `publicar_test`
(`_pg_dedicado`). Nunca em paralelo com outra suite contra o mesmo banco.
"""
import os
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest
from fastapi import HTTPException

from tests.test_bulk_approve_por_posicao import _preparar_banco, _semear
from tests.test_cinco_posicoes import _Ambiente

TEST_DB = os.environ.get("TEST_DATABASE_URL")
_precisa_db = pytest.mark.skipif(
    not TEST_DB, reason="TEST_DATABASE_URL nao definido: teste real so roda com Postgres dedicado"
)

CINCO = [
    ("cover_ai", 0, "p0", "uploaded"),
    ("presentation_ai", 1, "p1", "uploaded"),
    ("benefits_ai", 2, "p2", "uploaded"),
    ("detail_ai", 3, "p3", "uploaded"),
    ("specs_ai", 4, "p4", "uploaded"),
]


class _AmbienteComRotulo(_Ambiente):
    """Registra o rotulo de custo vigente em cada chamada ao motor."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.rotulos = []

    async def _edit(self, images, prompt, n, size=None):
        from app.services.ai.cost_log import image_edit_task
        self.rotulos.append(image_edit_task())
        return await super()._edit(images, prompt, n, size)


@asynccontextmanager
async def _sessao_real(sm):
    async with sm() as s:
        yield s


async def _linhas(sm, listing_id):
    from sqlalchemy import select

    from app.models.listing_image import ListingImage

    async with sm() as s:
        rows = (await s.execute(
            select(ListingImage).where(ListingImage.listing_id == listing_id)
            .order_by(ListingImage.sort_order, ListingImage.created_at)
        )).scalars().all()
        return [(r.id, r.sort_order, r.kind, r.status, r.ml_picture_id, r.approved) for r in rows]


async def _status_do_listing(sm, listing_id):
    from sqlalchemy import select

    from app.models.listing import Listing

    async with sm() as s:
        return (await s.execute(select(Listing).where(Listing.id == listing_id))).scalar_one().status


async def _eventos(sm, listing_id):
    from sqlalchemy import func, select

    from app.models.listing_review_event import ListingReviewEvent

    async with sm() as s:
        return (await s.execute(
            select(func.count()).select_from(ListingReviewEvent)
            .where(ListingReviewEvent.listing_id == listing_id)
        )).scalar_one()


async def _pedir_regeneracao(sm, listing_id, seller_id, posicao):
    """Chama o service como o endpoint faz; devolve o id do placeholder.
    `regenerate_position.delay` e' mockado — o worker roda a mao no teste."""
    from app.services.listing_service import ListingService

    async with sm() as s:
        svc = ListingService(s, seller_id)
        listing = await svc.get_or_404(listing_id, seller_id)
        with patch("app.workers.tasks.image_tasks.regenerate_position") as task:
            task.delay = MagicMock()
            placeholder = await svc.regenerate_position(listing, posicao)
            task.delay.assert_called_once_with(str(listing_id), str(placeholder.id))
        return placeholder.id


async def _rodar_worker(sm, listing_id, placeholder_id, ambiente):
    from app.workers.tasks.image_tasks import _regenerate_position_async

    with ambiente as amb, \
         patch("app.database.worker_session", lambda: _sessao_real(sm)), \
         patch("app.workers.tasks.image_tasks._fetch_upload_token", new_callable=AsyncMock, return_value="tok"), \
         patch("app.workers.tasks.image_tasks._carregar_fotos_brutas", new_callable=AsyncMock,
               return_value=([b"f1", b"f2", b"f3"], "38")):
        result = await _regenerate_position_async(str(listing_id), str(placeholder_id))
    return result, amb


@_precisa_db
class TestRegeneracaoPontaAPonta:
    @pytest.mark.asyncio
    async def test_substitui_so_a_posicao_pedida_e_rotula_o_custo(self):
        engine, sm = await _preparar_banco()
        try:
            listing_id, seller_id, _ = await _semear(sm, CINCO)
            antes = await _linhas(sm, listing_id)
            id_antiga_2 = next(r[0] for r in antes if r[1] == 2)

            pid = await _pedir_regeneracao(sm, listing_id, seller_id, 2)
            durante = await _linhas(sm, listing_id)
            assert (pid, 2, "benefits_ai", "generating", None, False) in durante

            result, amb = await _rodar_worker(sm, listing_id, pid, _AmbienteComRotulo())

            assert result["status"] == "uploaded" and result["removidas"] == 1
            assert amb.rotulos == ["image_edit_regen"], "uma chamada, rotulada"
            depois = await _linhas(sm, listing_id)
            na_2 = [r for r in depois if r[1] == 2]
            assert na_2 == [(pid, 2, "benefits_ai", "uploaded", na_2[0][4], False)]
            assert na_2[0][4] and na_2[0][4] != "p2"
            assert id_antiga_2 not in {r[0] for r in depois}
            # As outras 4 posicoes: mesmos ids, mesmos ml_picture_id.
            outras_antes = sorted(r for r in antes if r[1] != 2)
            outras_depois = sorted(r for r in depois if r[1] != 2)
            assert outras_antes == outras_depois
            assert await _status_do_listing(sm, listing_id) == "pending_image_approval"
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_falha_do_motor_mantem_a_anterior_e_nao_muda_o_status(self):
        engine, sm = await _preparar_banco()
        try:
            listing_id, seller_id, _ = await _semear(sm, CINCO)
            pid = await _pedir_regeneracao(sm, listing_id, seller_id, 2)

            result, _ = await _rodar_worker(sm, listing_id, pid, _Ambiente(falhar_em={0, 1}))

            assert result["status"] == "generation_failed"
            depois = await _linhas(sm, listing_id)
            na_2 = sorted(r for r in depois if r[1] == 2)
            assert {r[3] for r in na_2} == {"uploaded", "generation_failed"}
            assert any(r[4] == "p2" for r in na_2), "a anterior permanece"
            assert await _status_do_listing(sm, listing_id) == "pending_image_approval"
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_posicao_0_cai_no_fallback_deterministico(self):
        engine, sm = await _preparar_banco()
        try:
            listing_id, seller_id, _ = await _semear(sm, CINCO)
            pid = await _pedir_regeneracao(sm, listing_id, seller_id, 0)

            result, amb = await _rodar_worker(sm, listing_id, pid, _Ambiente(falhar_em={0, 1}))

            assert result["status"] == "uploaded" and result["kind"] == "cover_deterministic"
            assert len(amb.prompts) == 2, "2 tentativas da IA, depois o fallback sem IA"
            na_0 = [r for r in await _linhas(sm, listing_id) if r[1] == 0]
            assert len(na_0) == 1 and na_0[0][0] == pid and na_0[0][2] == "cover_deterministic"
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_posicao_aprovada_e_recusada_sem_placeholder(self):
        from sqlalchemy import update

        from app.models.listing_image import ListingImage

        engine, sm = await _preparar_banco()
        try:
            listing_id, seller_id, _ = await _semear(sm, CINCO)
            async with sm() as s:
                await s.execute(update(ListingImage).where(
                    ListingImage.listing_id == listing_id, ListingImage.sort_order == 2
                ).values(approved=True))
                await s.commit()

            with pytest.raises(HTTPException) as exc:
                await _pedir_regeneracao(sm, listing_id, seller_id, 2)
            assert exc.value.status_code == 409 and "aprovada" in exc.value.detail
            assert len([r for r in await _linhas(sm, listing_id) if r[1] == 2]) == 1
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_duplo_disparo_gera_um_placeholder_so(self):
        engine, sm = await _preparar_banco()
        try:
            listing_id, seller_id, _ = await _semear(sm, CINCO)
            pid = await _pedir_regeneracao(sm, listing_id, seller_id, 2)
            with pytest.raises(HTTPException) as exc:
                await _pedir_regeneracao(sm, listing_id, seller_id, 2)
            assert exc.value.status_code == 409
            assert exc.value.detail == "Regeneração em andamento na posição 2; aguarde."
            gerando = [r for r in await _linhas(sm, listing_id) if r[3] == "generating"]
            assert [r[0] for r in gerando] == [pid]
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_aprovacoes_bloqueadas_durante_regeneracao(self):
        from app.services.listing_service import ListingService

        engine, sm = await _preparar_banco()
        try:
            listing_id, seller_id, user_id = await _semear(sm, CINCO)
            await _pedir_regeneracao(sm, listing_id, seller_id, 3)
            id_da_1 = next(r[0] for r in await _linhas(sm, listing_id) if r[1] == 1)
            esperada = "Regeneração em andamento na posição 3; aguarde a conclusão antes de aprovar."

            async with sm() as s:
                svc = ListingService(s, seller_id)
                listing = await svc.get_or_404(listing_id, seller_id)
                with patch("app.workers.tasks.ai_tasks.generate_description") as gen:
                    with pytest.raises(HTTPException) as exc:
                        await svc.approve_images(listing, [id_da_1], user_id=user_id)
                    assert exc.value.status_code == 409 and exc.value.detail == esperada
                    gen.delay.assert_not_called()

            async with sm() as s:
                with patch("app.workers.tasks.ai_tasks.generate_description") as gen:
                    result = await ListingService(s, seller_id).bulk_approve_images([listing_id], user_id=user_id)
                assert result.failed == 1 and result.results[0].error == esperada
                gen.delay.assert_not_called()

            assert await _status_do_listing(sm, listing_id) == "pending_image_approval"
            assert await _eventos(sm, listing_id) == 0
            assert all(r[5] is False for r in await _linhas(sm, listing_id))
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_aprovacao_que_venceu_a_corrida_faz_o_worker_desistir(self):
        """Placeholder criado, anuncio aprovado antes de o worker rodar
        (aprovacao em massa nao mexe no status da linha): o worker apaga o
        placeholder e nao gera nada."""
        from sqlalchemy import update

        from app.models.listing import Listing

        engine, sm = await _preparar_banco()
        try:
            listing_id, seller_id, _ = await _semear(sm, CINCO)
            pid = await _pedir_regeneracao(sm, listing_id, seller_id, 2)
            async with sm() as s:
                await s.execute(update(Listing).where(Listing.id == listing_id)
                                .values(status="generating_description"))
                await s.commit()

            result, amb = await _rodar_worker(sm, listing_id, pid, _Ambiente())

            assert result["skipped"] is True and amb.prompts == []
            assert pid not in {r[0] for r in await _linhas(sm, listing_id)}
            assert await _status_do_listing(sm, listing_id) == "generating_description"
        finally:
            await engine.dispose()
```

- [ ] **Step 2: Rodar sem banco (deve pular) e com banco (deve passar)**

Run: `docker compose exec -T backend pytest -q -p no:cacheprovider tests/test_regenerar_posicao_pg.py`
Expected: `7 skipped`.

Run: `docker exec publicaradmlb-backend-1 sh -c 'TEST_DATABASE_URL="${DATABASE_URL%/*}/publicar_test" pytest -q -p no:cacheprovider tests/test_regenerar_posicao_pg.py'`
Expected: `7 passed`. Se algum falhar, corrigir o **código** (Tasks 3–6), nunca afrouxar o teste; se a falha for de um teste pré-existente, parar e reportar.

- [ ] **Step 3: Commit**

```bash
git add backend/tests/test_regenerar_posicao_pg.py
git commit -m "test(regenerar-posicao): ponta a ponta em Postgres real"
```

---

### Task 8: Documentação (`CLAUDE.md`) e prova final das duas linhas de base

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Atualizar o `CLAUDE.md`**

1. Tabela **Estado das fases**: nova linha antes de "Fase 6":
   `| Regenerar posição (2026-09-12) | ✅ | `POST /listings/{id}/images/positions/{posicao}/regenerate` (0..4): placeholder `generating` com índice único parcial como trava, task `regenerate_position` preenche no lugar e apaga a anterior não aprovada só no sucesso; anúncio fica em `pending_image_approval`; aprovações recusam com 409 próprio durante a regeneração; custo com `task=image_edit_regen`. Branch `feat/regenerar-posicao`, sem merge |`
2. **backend/app/models/**, item `listing_image.py`: acrescentar "Vocabulário de status da regeneração: `GENERATING_STATUS` (placeholder), `GENERATION_FAILED_STATUS` (não produziu imagem; `validation_failed` continua sendo QA) e `POSITION_KINDS` (kind oficial por posição). Quarto índice em `listing_id`: `uq_listing_images_generating_slot (listing_id, sort_order) WHERE status = 'generating'`, a trava do duplo clique".
3. **backend/app/workers/tasks/**, item `image_tasks.py`: acrescentar "`_gerar_cinco_posicoes` é um laço de 0 a 4 sobre `_gerar_posicao(db, listing, ctx, numero, alvo=None)` com um `_ContextoGeracao` montado por `_montar_contexto` (`com_campos`/`com_copy` controlam a consulta de atributos e a copy do LLM). `regenerate_position(listing_id, image_id)`: regenera UMA posição no placeholder, nunca toca em `listing.status`, `ImageEngineUnavailableError` vira `generation_failed` na linha (nunca `pending_ai_engine`)".
4. **Migrations aplicadas**: `f7b3e9c1d2a5` deixa de ser "(head atual)"; nova linha `a1d7c3e9f5b2 — índice único parcial uq_listing_images_generating_slot (trava do placeholder de regeneração) (head atual)`.
5. **Endpoints implementados**: acrescentar `POST /api/v1/listings/{id}/images/positions/{posicao}/regenerate   regenera UMA posição (0..4) não aprovada, só em pending_image_approval; 202 com o placeholder`.
6. **Ordem das imagens do anúncio**: parágrafo novo após "Toda aprovação humana grava 1 linha…": "**Regenerar uma posição não muda o anúncio de status.** O placeholder `generating` bloqueia `approve_images`/`bulk_approve_images` com 409 próprio. No sucesso a anterior não aprovada da posição é apagada (asset_key no log); falha do motor vira `generation_failed` na linha e a anterior fica; QA reprovada vira `validation_failed` e a anterior também fica (duas linhas não aprovadas na posição até a próxima regeneração). Copy da posição 2 é gerada de novo a cada regeneração (texto pode mudar; a tela precisa avisar)".
7. **backend/tests/**: linhas para `test_regenerar_posicao_custo.py`, `test_migracao_indice_regeneracao.py`, `test_regenerar_posicao_worker.py`, `test_regenerar_posicao_service.py`, `test_regenerar_posicao_pg.py` (Postgres real) com o resumo de cada.
8. **SPECs de referência**: linha `docs/superpowers/specs/2026-09-12-regenerar-posicao.md | Regeneração de UMA posição: decisões do passo 0, contrato, guards do worker, pendências`.
9. Atualizar a nota da suíte com os números medidos no Step 2.

- [ ] **Step 2: As duas linhas de base**

Run: `docker compose exec -T backend pytest -q -p no:cacheprovider`
Expected: `N passed, 63 skipped` com `N >= 482` e **0 failed** (56 pulados antigos + 7 novos de PG).

Run: `docker exec publicaradmlb-backend-1 sh -c 'TEST_DATABASE_URL="${DATABASE_URL%/*}/publicar_test" pytest -q -p no:cacheprovider'`
Expected: `M passed` com `M >= 538`, **0 failed, 0 skipped** relevantes.

Run: `git diff master --stat -- backend/tests/ | grep -v "test_regenerar_posicao\|test_migracao_indice_regeneracao"`
Expected: vazio — nenhum teste pré-existente alterado.

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(claude-md): regeneração de UMA posição — endpoint, task, vocabulário, migração e testes"
```

---

## Depois das tasks (fora do plano, no fluxo normal)

1. Revisão do **branch inteiro** (`git diff master...feat/regenerar-posicao`) com a skill de revisão.
2. `git push -u origin feat/regenerar-posicao`. **Sem merge, sem deploy.**
3. Relatório ao Daniel: números das duas suítes, arquivos, e as pendências registradas na spec (ListingJob morto; tela precisa mostrar `status` por imagem e avisar sobre a copy da posição 2; migração ainda não aplicada em dev nem em produção).
