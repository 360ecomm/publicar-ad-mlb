# Publicar ativo — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publicar o anúncio já ativo no ML (parar de desfazer o padrão do ML), reportar o estado final real da publicação (incluindo `under_review`) e dar ao operador o caminho de volta para anúncio pausado.

**Architecture:** `publish()` cria o item com `"status": "active"`; `_ensure_paused` vira `_aguardar_validacao` (espera o `sub_status` limpar, nunca força nada, devolve o estado final); o worker traduz o estado ML para o vocabulário local (novo status `published_under_review`; `published_paused` vira balde de anomalia com botão "Reativar" na tela).

**Tech Stack:** FastAPI + SQLAlchemy async, Celery, Next.js 14 + TypeScript.

**Spec:** `docs/superpowers/specs/2026-09-15-publicar-ativo.md`

## Global Constraints

- Branch `feat/publicar-ativo` a partir do HEAD de `master` (`c816332`). **Sem merge, sem deploy**; a branch será publicada no fim para revisão humana.
- **Nenhum anúncio existente pode ser ativado como efeito desta mudança.** Sem migração (nem de schema nem de dados), sem varredura retroativa. Nenhum caminho de código novo faz `PUT {"status": "active"}` além de `activate_listing`, que continua exigindo POST explícito e status `published_paused`.
- O `PUT {"status": "paused"}` que forçava pausa **não pode existir em caminho nenhum** ao final da branch.
- Linhas de base da suíte (em `c816332`): **671 passed / 88 skipped** sem `TEST_DATABASE_URL`; **759 passed / 0 skipped** com ela. Cada task termina com a suíte verde (as contagens só crescem).
- Comando da suíte (host Windows, container de dev já no ar):
  - sem PG real: `docker exec publicaradmlb-backend-1 sh -c 'cd /app && pytest -q -p no:cacheprovider tests/'`
  - com PG real: `docker exec publicaradmlb-backend-1 sh -c 'cd /app && TEST_DATABASE_URL="${DATABASE_URL%/*}/publicar_test" pytest -q -p no:cacheprovider tests/'` (banco `publicar_test` é de uso EXCLUSIVO — nunca duas execuções ao mesmo tempo)
  - o código-fonte NÃO é montado por bind no container? É sim em dev (`docker-compose.yml` monta `./backend` em `/app`) — edições no host valem imediatamente dentro do container, sem rebuild.
- Frontend: verificação é `cd frontend && npm run build` (o `next build` type-checa inclusive `src/lib/__tests__/*`). Não instalar dependência nova.
- Commits: Conventional Commits em PT-BR (`feat:`, `fix:`, `test:`, `docs:`), terminando com as duas linhas de atribuição da sessão (Co-Authored-By + Claude-Session) conforme instrução da sessão.
- Vocabulário novo: status local `published_under_review`, rótulo "Em análise no ML". `published_paused` ganha rótulo "Pausado no ML" e muda de grupo na fila (`done` → `waiting`).

---

### Task 1: Vocabulário — status `published_under_review`

**Files:**
- Modify: `backend/app/models/listing.py` (tupla `LISTING_STATUSES` e o comentário de `EDITABLE_ATTRIBUTE_STATUSES`)
- Modify: `backend/tests/test_status_counts_rota.py`
- Modify: `backend/tests/test_editar_atributos_vocabulario.py`
- Check (provavelmente sem mudança): `backend/tests/test_listagem_em_escala.py` — conferir com `grep -n "16" backend/tests/test_listagem_em_escala.py` se algum teste crava o número 16 em vez de derivar de `LISTING_STATUSES`; se cravar, atualizar para derivar ou para 17.

**Interfaces:**
- Consumes: nada de tasks anteriores.
- Produces: a string de status `"published_under_review"` presente em `LISTING_STATUSES` e AUSENTE de `EDITABLE_ATTRIBUTE_STATUSES` — Tasks 2 e 3 dependem dela.

- [ ] **Step 1: Atualizar os testes de vocabulário (devem FALHAR)**

Em `backend/tests/test_status_counts_rota.py`, no teste `test_lista_canonica_tem_16_status_sem_repeticao`: renomear para `test_lista_canonica_tem_17_status_sem_repeticao`, acrescentar `"published_under_review"` ao set `esperado` (ao lado de `"published"`) e trocar os três `16` por `17`. Ajustar o docstring ("17 status do pipeline").

Em `backend/tests/test_editar_atributos_vocabulario.py`:
- `test_os_sete_perigosos_ficam_de_fora` → renomear para `test_os_oito_perigosos_ficam_de_fora` e acrescentar `"published_under_review"` ao set `proibidos` (mesma justificativa de `published`/`published_paused`: editar anúncio que já está no ML é pendência futura). Atualizar docstring se mencionar "sete".
- `test_particao_exata_de_listing_statuses`: acrescentar `"published_under_review"` ao set local de proibidos usado na partição (o teste deve continuar provando `EDITABLE | proibidos == LISTING_STATUSES` **e** interseção vazia).

- [ ] **Step 2: Rodar os dois arquivos e confirmar que falham**

Run: `docker exec publicaradmlb-backend-1 sh -c 'cd /app && pytest -q -p no:cacheprovider tests/test_status_counts_rota.py tests/test_editar_atributos_vocabulario.py'`
Expected: FAIL (status ausente de `LISTING_STATUSES`).

- [ ] **Step 3: Acrescentar o status ao model**

Em `backend/app/models/listing.py`:
- Na tupla `LISTING_STATUSES`, inserir `"published_under_review",` imediatamente depois de `"published",` (antes de `"published_paused",`).
- No bloco de comentário acima de `EDITABLE_ATTRIBUTE_STATUSES`: trocar "Os sete de fora nao sao esquecimento" por "Os oito de fora nao sao esquecimento" e, na linha que já lista `published / published_paused`, incluir `published_under_review` no mesmo grupo (ex.: `published / published_paused / published_under_review — editar anuncio NO AR (ou em analise no ML) e' pendencia futura, com regras proprias`). NÃO mexer no conteúdo do frozenset.

- [ ] **Step 4: Rodar a suíte inteira sem PG e conferir verde**

Run: `docker exec publicaradmlb-backend-1 sh -c 'cd /app && pytest -q -p no:cacheprovider tests/'`
Expected: 671 passed / 88 skipped (mesmas contagens — nenhum teste novo foi criado, só renomeados/ajustados).

- [ ] **Step 5: Rodar com PG real (o `count_by_status` e a listagem derivam de `LISTING_STATUSES`)**

Run: `docker exec publicaradmlb-backend-1 sh -c 'cd /app && TEST_DATABASE_URL="${DATABASE_URL%/*}/publicar_test" pytest -q -p no:cacheprovider tests/'`
Expected: 759 passed. Se `test_listagem_em_escala.py` cravar "16 chaves" em assert e falhar, ajustar o teste para derivar de `LISTING_STATUSES` (ou 17) — é ajuste de teste, não de produto.

- [ ] **Step 6: Commit**

```bash
git add backend/app/models/listing.py backend/tests/test_status_counts_rota.py backend/tests/test_editar_atributos_vocabulario.py backend/tests/test_listagem_em_escala.py
git commit -m "feat(status): published_under_review entra no vocabulário (17 status)"
```

---

### Task 2: Backend — criar ativo, `_aguardar_validacao`, mapa no worker

**Files:**
- Modify: `backend/app/services/publish_service.py`
- Modify: `backend/app/workers/tasks/publish_tasks.py`
- Modify: `backend/tests/test_publish_service.py` (classe `TestPublishEnsuresPaused` substituída; `TestPublishPicsPayloadCap` ajustada ao retorno em tupla)
- Modify: `backend/tests/test_publish_catalog_mode.py` (patches de `_ensure_paused` → `_aguardar_validacao`; retorno em tupla)
- Create: `backend/tests/test_publicar_ativo.py`

**Interfaces:**
- Consumes: status `"published_under_review"` (Task 1).
- Produces: `PublishService.publish(...) -> tuple[str, str]` (`(item_id, estado_final_ml)`); `_aguardar_validacao(item_id, item_data, access_token) -> str`; `publish_tasks._status_apos_publicacao(estado_ml: str) -> str` (função de módulo). `PublishService._ensure_paused` DEIXA DE EXISTIR.

- [ ] **Step 1: Escrever os testes novos (devem FALHAR)**

Criar `backend/tests/test_publicar_ativo.py` com este conteúdo (mesmo estilo de mock de `test_publish_service.py` — reusar `_listing`, `_image` e `_sem_teto_de_categoria` importando-os OU copiando os helpers; preferir `from tests.test_publish_service import _listing, _image, _sem_teto_de_categoria` se o import funcionar no layout do container, senão copiar):

```python
"""feat/publicar-ativo: o item nasce ativo e o sistema reporta o estado real.

O passo 0 (leitura do anuncio 31) provou que o ML IGNORA o "status": "paused"
enviado na criacao e devolve o item em outro estado; o PUT que vinha em
seguida e' que o pausava. Esta branch para de desfazer o padrao do ML:
cria ativo, espera a validacao das fotos e reporta o que o ML decidiu.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.publish_service import PublishService
from app.workers.tasks.publish_tasks import _status_apos_publicacao
from tests.test_publish_service import _listing, _image, _sem_teto_de_categoria


class TestStatusAposPublicacao:
    def test_active_vira_published(self):
        assert _status_apos_publicacao("active") == "published"

    def test_under_review_vira_published_under_review(self):
        assert _status_apos_publicacao("under_review") == "published_under_review"

    def test_paused_vira_published_paused(self):
        assert _status_apos_publicacao("paused") == "published_paused"

    def test_estado_desconhecido_cai_na_anomalia(self):
        for estado in ("", "inactive", "closed", "qualquer_coisa"):
            assert _status_apos_publicacao(estado) == "published_paused", estado

    def test_todos_os_retornos_existem_no_vocabulario(self):
        from app.models.listing import LISTING_STATUSES
        for estado in ("active", "under_review", "paused", ""):
            assert _status_apos_publicacao(estado) in LISTING_STATUSES


class TestPublishCriaAtivoESemPut:
    @pytest.mark.asyncio
    async def test_payload_de_criacao_pede_active(self):
        create_response = MagicMock()
        create_response.status_code = 201
        create_response.json.return_value = {"id": "MLB1", "status": "active", "sub_status": []}
        mock_post = AsyncMock(return_value=create_response)
        with patch("httpx.AsyncClient") as mock_client_cls, _sem_teto_de_categoria():
            client = mock_client_cls.return_value.__aenter__.return_value
            client.post = mock_post
            client.put = AsyncMock()
            await PublishService(db=MagicMock()).publish(
                listing=_listing(), attributes=[], images=[_image()],
                description_html=None, access_token="token",
            )
        body = mock_post.await_args_list[0].kwargs["json"]
        assert body["status"] == "active"

    @pytest.mark.asyncio
    async def test_ml_ativa_na_hora_devolve_active_sem_put(self):
        create_response = MagicMock()
        create_response.status_code = 201
        create_response.json.return_value = {"id": "MLB1", "status": "active", "sub_status": []}
        mock_put = AsyncMock()
        with patch("httpx.AsyncClient") as mock_client_cls, _sem_teto_de_categoria():
            client = mock_client_cls.return_value.__aenter__.return_value
            client.post = AsyncMock(return_value=create_response)
            client.put = mock_put
            item_id, estado = await PublishService(db=MagicMock()).publish(
                listing=_listing(), attributes=[], images=[_image()],
                description_html=None, access_token="token",
            )
        assert (item_id, estado) == ("MLB1", "active")
        mock_put.assert_not_called()

    @pytest.mark.asyncio
    async def test_ml_devolve_paused_e_ninguem_forca_nada(self):
        create_response = MagicMock()
        create_response.status_code = 201
        create_response.json.return_value = {"id": "MLB1", "status": "paused", "sub_status": []}
        mock_put = AsyncMock()
        with patch("httpx.AsyncClient") as mock_client_cls, _sem_teto_de_categoria():
            client = mock_client_cls.return_value.__aenter__.return_value
            client.post = AsyncMock(return_value=create_response)
            client.put = mock_put
            _, estado = await PublishService(db=MagicMock()).publish(
                listing=_listing(), attributes=[], images=[_image()],
                description_html=None, access_token="token",
            )
        assert estado == "paused"
        mock_put.assert_not_called()

    @pytest.mark.asyncio
    async def test_ml_devolve_under_review_e_o_estado_e_reportado(self):
        create_response = MagicMock()
        create_response.status_code = 201
        create_response.json.return_value = {"id": "MLB1", "status": "under_review", "sub_status": []}
        mock_put = AsyncMock()
        with patch("httpx.AsyncClient") as mock_client_cls, _sem_teto_de_categoria():
            client = mock_client_cls.return_value.__aenter__.return_value
            client.post = AsyncMock(return_value=create_response)
            client.put = mock_put
            _, estado = await PublishService(db=MagicMock()).publish(
                listing=_listing(), attributes=[], images=[_image()],
                description_html=None, access_token="token",
            )
        assert estado == "under_review"
        mock_put.assert_not_called()

    @pytest.mark.asyncio
    async def test_espera_picture_download_pending_e_reporta_o_estado_final(self):
        create_response = MagicMock()
        create_response.status_code = 201
        create_response.json.return_value = {
            "id": "MLB1", "status": "paused", "sub_status": ["picture_download_pending"],
        }
        get_response = MagicMock()
        get_response.status_code = 200
        get_response.json.return_value = {"id": "MLB1", "status": "active", "sub_status": []}
        mock_put = AsyncMock()
        with patch("httpx.AsyncClient") as mock_client_cls, \
             patch("asyncio.sleep", new_callable=AsyncMock), \
             _sem_teto_de_categoria():
            client = mock_client_cls.return_value.__aenter__.return_value
            client.post = AsyncMock(return_value=create_response)
            client.get = AsyncMock(return_value=get_response)
            client.put = mock_put
            _, estado = await PublishService(db=MagicMock()).publish(
                listing=_listing(), attributes=[], images=[_image()],
                description_html=None, access_token="token",
            )
        assert estado == "active"
        mock_put.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_de_checagem_falhando_reporta_o_ultimo_estado_visto(self):
        create_response = MagicMock()
        create_response.status_code = 201
        create_response.json.return_value = {
            "id": "MLB1", "status": "paused", "sub_status": ["picture_download_pending"],
        }
        get_response = MagicMock()
        get_response.status_code = 500
        get_response.text = "boom"
        mock_put = AsyncMock()
        with patch("httpx.AsyncClient") as mock_client_cls, \
             patch("asyncio.sleep", new_callable=AsyncMock), \
             _sem_teto_de_categoria():
            client = mock_client_cls.return_value.__aenter__.return_value
            client.post = AsyncMock(return_value=create_response)
            client.get = AsyncMock(return_value=get_response)
            client.put = mock_put
            _, estado = await PublishService(db=MagicMock()).publish(
                listing=_listing(), attributes=[], images=[_image()],
                description_html=None, access_token="token",
            )
        assert estado == "paused"
        mock_put.assert_not_called()


class TestInvariantes:
    def test_ensure_paused_nao_existe_mais(self):
        """Guarda contra reintroducao E contra patch antigo passando em silencio."""
        assert not hasattr(PublishService, "_ensure_paused")

    def test_activate_continua_sendo_o_unico_caminho_de_ativacao_explicita(self):
        """activate_listing segue existindo e segue mandando PUT active —
        o caminho de volta do pausado (botao 'Reativar' na tela)."""
        assert hasattr(PublishService, "activate_listing")

    @pytest.mark.asyncio
    async def test_anuncio_ja_pausado_nao_e_tocado_pelo_worker(self):
        """O guard de idempotencia do worker: listing fora de 'publishing'
        (ex.: published_paused, os anuncios 31/37/38) e' pulado sem nenhuma
        chamada ao ML."""
        from app.workers.tasks import publish_tasks as pt

        listing = MagicMock()
        listing.status = "published_paused"

        execute_result = MagicMock()
        execute_result.scalar_one.return_value = listing

        db = MagicMock()
        db.execute = AsyncMock(return_value=execute_result)
        db.commit = AsyncMock()

        session_ctx = MagicMock()
        session_ctx.__aenter__ = AsyncMock(return_value=db)
        session_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("app.database.worker_session", return_value=session_ctx), \
             patch.object(PublishService, "publish", new_callable=AsyncMock) as mock_publish:
            result = await pt._publish_listing_async("lst-1")

        assert result == {"listing_id": "lst-1", "skipped": True}
        mock_publish.assert_not_called()
        assert listing.status == "published_paused"


class TestWorkerGravaOEstadoTraduzido:
    def _roda_worker(self, estado_ml):
        from app.workers.tasks import publish_tasks as pt

        listing = MagicMock()
        listing.status = "publishing"
        listing.id = "lst-1"

        one = MagicMock(); one.scalar_one.return_value = listing
        seller = MagicMock(); one_seller = MagicMock(); one_seller.scalar_one.return_value = seller
        vazio = MagicMock(); vazio.scalars.return_value.all.return_value = []
        nenhum = MagicMock(); nenhum.scalar_one_or_none.return_value = None

        db = MagicMock()
        db.execute = AsyncMock(side_effect=[one, one_seller, vazio, vazio, nenhum])
        db.commit = AsyncMock()

        session_ctx = MagicMock()
        session_ctx.__aenter__ = AsyncMock(return_value=db)
        session_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("app.database.worker_session", return_value=session_ctx), \
             patch("app.services.publish_service.get_valid_access_token", new_callable=AsyncMock, return_value="tok"), \
             patch.object(PublishService, "publish", new_callable=AsyncMock, return_value=("MLB77", estado_ml)):
            import asyncio as _a
            _a.run(pt._publish_listing_async("lst-1"))
        return listing

    def test_active_grava_published(self):
        assert self._roda_worker("active").status == "published"

    def test_under_review_grava_published_under_review(self):
        assert self._roda_worker("under_review").status == "published_under_review"

    def test_paused_grava_published_paused(self):
        assert self._roda_worker("paused").status == "published_paused"
```

ATENÇÃO ao helper `_roda_worker`: os patches de `worker_session` e `get_valid_access_token` precisam mirar o **caminho que o worker importa** (`_publish_listing_async` importa `from app.database import worker_session` e `from app.services.publish_service import ... get_valid_access_token` DENTRO da função, então patch em `app.database.worker_session` e `app.services.publish_service.get_valid_access_token` funciona). Conferir contra os padrões de `tests/test_image_tasks.py` se o mock de sessão precisar de ajuste; o teste deve provar o comportamento, não a forma exata do mock. Se `from tests.test_publish_service import ...` não resolver no container, copiar os três helpers para o arquivo novo com um comentário dizendo de onde vieram.

- [ ] **Step 2: Rodar o arquivo novo e confirmar que falha**

Run: `docker exec publicaradmlb-backend-1 sh -c 'cd /app && pytest -q -p no:cacheprovider tests/test_publicar_ativo.py'`
Expected: FAIL (`_status_apos_publicacao` não existe; payload ainda manda "paused"; `_ensure_paused` ainda existe).

- [ ] **Step 3: Implementar no service**

Em `backend/app/services/publish_service.py`:

1. No `body` de `publish()`: `"status": "paused",` → `"status": "active",`. Ajustar o comentário se houver.
2. Trocar a chamada `await self._ensure_paused(item_id, item_data, access_token)` por `estado_final = await self._aguardar_validacao(item_id, item_data, access_token)`.
3. `publish()` passa a devolver `return item_id, estado_final` (anotação `-> tuple[str, str]`; atualizar o docstring da função se existir).
4. Substituir `_ensure_paused` inteira por:

```python
    async def _aguardar_validacao(self, item_id: str, item_data: dict, access_token: str) -> str:
        """Espera a validação assíncrona das fotos terminar e devolve o
        status final do item no ML ('active', 'paused', 'under_review'...).

        NÃO força status nenhum. O passo 0 de feat/publicar-ativo provou que
        o ML ignora o status pedido na criação e decide sozinho ao fim da
        validação — o PUT que existia aqui só desfazia essa decisão.
        Se a validação não terminar dentro da janela (5 × 2 s), devolve o
        último estado visto: é foto do momento, não destino final.
        """
        status_value = item_data.get("status") or ""
        sub_status = item_data.get("sub_status") or []

        for _ in range(5):
            if "picture_download_pending" not in sub_status:
                break
            await asyncio.sleep(2)
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    f"{ML_ITEMS_URL}/{item_id}",
                    headers={"Authorization": f"Bearer {access_token}"},
                )
            if resp.status_code != 200:
                logger.warning("Falha ao checar status do item %s: %s", item_id, resp.text[:200])
                break
            item_data = resp.json()
            status_value = item_data.get("status") or ""
            sub_status = item_data.get("sub_status") or []

        return status_value
```

`activate_listing` fica exatamente como está.

- [ ] **Step 4: Implementar no worker**

Em `backend/app/workers/tasks/publish_tasks.py`:

1. Topo do módulo: acrescentar `import logging` e `logger = logging.getLogger(__name__)` (o módulo não tem logger hoje).
2. Função de módulo, acima de `_publish_listing_async`:

```python
def _status_apos_publicacao(estado_ml: str) -> str:
    """Traduz o status final do item no ML para o vocabulário local.

    'active' é o caminho feliz. 'under_review' é moderação do ML (o caso do
    SKU 37). Qualquer outra coisa — 'paused' inclusive, que deixou de ser o
    fluxo normal em feat/publicar-ativo — é anomalia e cai em
    published_paused, de onde o operador reativa pela tela."""
    if estado_ml == "active":
        return "published"
    if estado_ml == "under_review":
        return "published_under_review"
    return "published_paused"
```

3. Em `_publish_listing_async`, trocar:

```python
        mlb_id = await PublishService(db).publish(
```
por
```python
        mlb_id, estado_ml = await PublishService(db).publish(
```
e trocar
```python
        listing.mlb_id = mlb_id
        listing.status = "published_paused"
```
por
```python
        listing.mlb_id = mlb_id
        novo_status = _status_apos_publicacao(estado_ml)
        if novo_status != "published":
            logger.warning(
                "publish_estado_nao_ativo listing_id=%s mlb_id=%s estado_ml=%s status_local=%s",
                listing_id, mlb_id, estado_ml or "?", novo_status,
            )
        listing.status = novo_status
```

- [ ] **Step 5: Atualizar os testes existentes que conheciam o mundo antigo**

- `backend/tests/test_publish_service.py`: apagar a classe `TestPublishEnsuresPaused` inteira (os cenários agora vivem em `test_publicar_ativo.py`, com o comportamento novo). Em `TestPublishPicsPayloadCap`, ajustar chamadas que desempacotam o retorno de `publish()` (agora tupla) — se os testes só chamam sem usar o retorno, nada muda; se usam, `item_id, _ = ...`.
- `backend/tests/test_publish_catalog_mode.py`: todo `patch.object(PublishService, "_ensure_paused", ...)` vira `patch.object(PublishService, "_aguardar_validacao", new_callable=AsyncMock, return_value="active")`; asserts sobre o retorno de `publish()` passam a desempacotar a tupla.
- Rodar `grep -rn "_ensure_paused" backend/` ao final: a única ocorrência aceitável é ZERO (nem em comentário).

- [ ] **Step 6: Rodar a suíte inteira (sem e com PG)**

Run: os dois comandos das Global Constraints.
Expected: verde; contagem sem PG ≥ 671+N (N = testes novos de `test_publicar_ativo.py`), 88 skipped inalterado.

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/publish_service.py backend/app/workers/tasks/publish_tasks.py backend/tests/test_publicar_ativo.py backend/tests/test_publish_service.py backend/tests/test_publish_catalog_mode.py
git commit -m "feat(publicar): item nasce ativo e o worker grava o estado real do ML"
```

---

### Task 3: Frontend — status novo, anomalia com botão Reativar, textos de confirmação

**Files:**
- Modify: `frontend/src/types/listing.ts`
- Modify: `frontend/src/components/listings/ListingStatusBadge.tsx`
- Modify: `frontend/src/lib/__tests__/attribute-edit.test.ts`
- Modify: `frontend/src/app/(dashboard)/listings/[id]/page.tsx`
- Modify: `frontend/src/components/listings/BulkActionsBar.tsx`
- Modify: `frontend/src/components/listings/ListingPreview.tsx`
- Check: `frontend/src/lib/listing-destination.ts`, `frontend/src/lib/bulk-actions.ts`, `frontend/src/lib/status-summary.ts` — qualquer `Record<ListingStatus, ...>` exaustivo quebra o `tsc` até ganhar a chave nova; o `npm run build` aponta cada um.

**Interfaces:**
- Consumes: status `"published_under_review"` (Task 1); endpoint `POST /listings/{id}/activate` (já existe; client `activateListing` em `frontend/src/lib/api/listings.ts:267` já existe e NÃO muda).
- Produces: nada consumido por outra task.

- [ ] **Step 1: Vocabulário TS**

Em `frontend/src/types/listing.ts`:
- União `ListingStatus`: acrescentar `| "published_under_review"` depois de `"published"`.
- `STATUS_LABELS`: `published_paused: "Pausado"` → `published_paused: "Pausado no ML"`; acrescentar `published_under_review: "Em análise no ML"`.
- `STATUS_GROUP_OF`: acrescentar `published_under_review: "done"`; trocar `published_paused: "done"` → `published_paused: "waiting"`.
- Atualizar o comentário dos três blocos: `waiting` passa a citar "e `published_paused` (anomalia: o ML pausou ou nós nunca ativamos — o operador reativa pela tela do anúncio)"; `done` vira "no ar, ou em análise do ML (`published_under_review` — nada a fazer daqui; a moderação é lá)". Corrigir "cada um dos 16 status" → "cada um dos 17 status".

Em `frontend/src/components/listings/ListingStatusBadge.tsx`: acrescentar `published_under_review` nos dois records (variant `"outline"`, cor — usar a MESMA paleta `C.*` do arquivo; escolher a entrada âmbar/amarela já existente, distinta ou igual à de `published_paused`, sem inventar cor nova).

Em `frontend/src/lib/__tests__/attribute-edit.test.ts` (linha ~68): acrescentar `"published_under_review"` à lista de status NÃO editáveis.

- [ ] **Step 2: Card de pausado (com Reativar) e card de em análise no detalhe**

Em `frontend/src/app/(dashboard)/listings/[id]/page.tsx`, ao lado do bloco `{status === "published" && (...)}` (linha ~453), acrescentar dois blocos irmãos, seguindo os padrões visuais do arquivo (Card com borda/cor de fundo, ícones lucide já importados ou a importar, `useMutation` + `toast` + `queryClient.invalidateQueries` como nas mutations existentes do arquivo):

1. `status === "published_paused"`: Card âmbar. Título: **"Anúncio pausado no Mercado Livre"**. Texto: `"O anúncio existe no ML mas não está recebendo visitas. Isso pode ser falta de estoque, moderação do ML — ou a publicação não chegou a ativá-lo. Confira no ML e reative quando estiver tudo certo."` Link "Ver no Mercado Livre" quando `listing.mlb_id` existir (mesmo padrão de URL do card de published). Botão **"Reativar anúncio"** chamando `activateListing(listing.id)` (import de `@/lib/api/listings`) via `useMutation`: sucesso → `toast.success("Anúncio reativado no Mercado Livre")` + invalidar `["listing", id]` e `["listings"]`; erro → `toast.error` com a mensagem. Estado pending desabilita o botão com spinner (padrão do arquivo).
2. `status === "published_under_review"`: Card azul/cinza informativo, SEM botão. Título: **"Em análise no Mercado Livre"**. Texto: `"O ML está revisando este anúncio (moderação ou exigência de catálogo). Não há ação aqui: a decisão é do ML e aparece na conta do seller."` Link "Ver no Mercado Livre" quando houver `mlb_id`.

- [ ] **Step 3: Textos de confirmação**

Em `frontend/src/components/listings/BulkActionsBar.tsx` (diálogo de confirmação, linha ~165): trocar o parágrafo `"A publicação é irreversível: o anúncio vai ao ar na conta conectada."` por: `{selected.length === 1 ? "O anúncio vai ao ar imediatamente na conta conectada e passa a receber visitas e vendas." : "Os anúncios vão ao ar imediatamente na conta conectada e passam a receber visitas e vendas."}`

Em `frontend/src/components/listings/ListingPreview.tsx`: rótulo do botão `"Confirmar e Publicar"` → `"Publicar agora"`; parágrafo final `"O anúncio será publicado diretamente no Mercado Livre."` → `"O anúncio vai ao ar imediatamente no Mercado Livre e passa a receber visitas e vendas."`

- [ ] **Step 4: Build**

Run: `cd frontend && npm run build`
Expected: sucesso, zero erro de TS. Erros de Record exaustivo apontam exatamente os arquivos do "Check" acima — resolver acrescentando a chave nova com o valor coerente (`listing-destination`: os dois status novos/movidos levam a `/listings/{id}`; `bulk-actions`: nenhum bulk action para os dois).

- [ ] **Step 5: Commit**

```bash
git add frontend/src
git commit -m "feat(frontend): pausado vira anomalia com Reativar; em análise no ML; confirmações dizem que vai ao ar na hora"
```

---

### Task 4: Documentação

**Files:**
- Modify: `CLAUDE.md` (raiz do projeto)

**Interfaces:** nenhuma.

- [ ] **Step 1: Atualizar o CLAUDE.md**

1. Tabela "Estado das fases": nova linha `**Publicar ativo (2026-09-15)** | ✅ | Branch feat/publicar-ativo, sem merge e sem deploy: item nasce com status active (o ML ignorava o paused e o nosso PUT desfazia o padrão dele — provado no anúncio 31); _ensure_paused → _aguardar_validacao (espera o sub_status limpar, não força nada, reporta o estado final); worker traduz: active → published, under_review → published_under_review (status novo, 17º), resto → published_paused (anomalia, warning). Botão "Reativar anúncio" no detalhe (POST /activate). Confirmações de publicação dizem que vai ao ar imediatamente. Sem migração; nenhum anúncio existente é tocado`.
2. State machine: trocar o final `publishing └─(worker OK)──► published` por três desfechos: `└─(ML ativo)──► published`, `└─(ML under_review)──► published_under_review`, `└─(qualquer outro estado, anomalia)──► published_paused ──(activate)──► published`.
3. Endpoints: linha do `activate` → `POST /api/v1/listings/{id}/activate  reativar anúncio pausado no ML (published_paused → published); botão "Reativar anúncio" no detalhe`.
4. Ocorrências de "16" que agora são 17: linha do `status-counts` nos endpoints ("as 16 canônicas"), descrição de `test_status_counts_rota.py` ("16 chaves"), `test_listagem_em_escala.py` ("as 16 chaves") — ajustar as que a Task 1 tornou mentirosas (conferir com `grep -n "16 " CLAUDE.md`).
5. Na tabela de SPECs, acrescentar `docs/superpowers/specs/2026-09-15-publicar-ativo.md | Publicar ativo: por que o item nasce active, mapa de estados e decisões do passo 0`.

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: publicar ativo — estado real do ML, status novo e Reativar"
```
