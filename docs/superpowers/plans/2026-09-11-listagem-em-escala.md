# Plano — listagem para escala (contagem por status, filtro múltiplo, busca, índice)

**Branch:** `feat/listagem-em-escala` (a partir de `master` `bdfae7d`).
**Escopo:** só backend. `frontend/src` NÃO muda. O quadro atual (`PipelineBoard`,
que chama `GET /api/v1/listings?status=<um>&page_size=200`) tem que continuar
funcionando exatamente como hoje.

## Spec (autoridade)

A tarefa escrita pelo Daniel (mensagem de abertura da sessão). Resumo do que
vincula:

1. `GET /api/v1/listings/status-counts`: contagem por status do seller ativo,
   em UMA consulta agregada (nunca carrega linhas), inclui os status com zero,
   devolve o total.
2. `GET /api/v1/listings` aceita vários `status` (`?status=a&status=b`),
   compatível com o parâmetro único de hoje.
3. Parâmetro `search` na listagem, no mesmo padrão de
   `product_service.list_products` (`ilike` + `or_`), em `sku_external_id`,
   `selected_title`, `sku_brand`, `mlb_id`.
4. Migração Alembic sobre `c8d2f6a4e1b7` (head único, confirmado em prod e
   local) com índice que sirva ao filtro `seller_id + status` ordenado por
   `created_at desc`. Sem outra mudança de schema.
5. Limite por página (`le=200`): NÃO mudar. Só propor no relatório.
6. Testes vermelho-primeiro; os de várias linhas/agregação em Postgres real
   via `tests._pg_dedicado` (só `publicar_test`).

## Contexto do passo 0 (já investigado)

- **16 status** de `Listing.status`, mesma lista do `ListingStatus` do
  frontend (`frontend/src/types/listing.ts`): draft, generating_title,
  pending_title_approval, predicting_category, pending_seller_attributes,
  pending_description, generating_images, pending_raw_photos,
  pending_ai_engine, pending_image_approval, generating_description,
  ready_to_publish, publishing, published, published_paused, failed.
  **Não existe lista canônica no backend** — os literais estão espalhados.
- Índices em `listings` hoje (prod e migrations): `listings_pkey (id)`,
  `listings_mlb_id_key (mlb_id)`, `ix_listings_product_id (product_id)`.
  **`seller_id`, `status` e `created_at` não têm índice nenhum.**
- Prod: 10 linhas, seq scan, 0,14 ms — linha de base.
- O dashboard de sellers (`endpoints/sellers.py`) já faz
  `select(Listing.seller_id, Listing.status, func.count()).group_by(...)` —
  mesmo padrão a reusar, mas sem os zeros.

## Global Constraints

- Sem estado em memória de processo (2 workers uvicorn).
- Erros sempre `{"detail": "..."}` com status HTTP correto.
- Multi-tenant: toda query filtra por `seller_id` do seller ativo.
- Padrão de service: `XService(db)`; endpoints instanciam `ListingService(db)`.
- Testes de Postgres real: `skipif` sem `TEST_DATABASE_URL`, engine só por
  `tests._pg_dedicado._engine_dedicado()`, `drop_all`/`create_all` no início,
  `await engine.dispose()` em `finally`. Nunca rodar duas suítes contra
  `publicar_test` ao mesmo tempo.
- Comando para rodar testes (o código está em bind mount `./backend:/app`):
  - sem banco: `docker exec publicaradmlb-backend-1 sh -c 'pytest -q -p no:cacheprovider tests/<arquivo>'`
  - com banco: `docker exec publicaradmlb-backend-1 sh -c 'TEST_DATABASE_URL="${DATABASE_URL%/*}/publicar_test" pytest -q -p no:cacheprovider tests/<arquivo>'`
- Rede bloqueada na suíte (`conftest.py`): nada de HTTP real.
- Commits em Conventional Commits, mensagem em PT-BR sem acento nos títulos
  (padrão dos commits recentes), com as linhas de atribuição indicadas no
  dispatch.
- Docstrings/comentários em PT-BR, explicando o **porquê** (padrão da base).
- Rodar `cd frontend && npm run build`? NÃO — frontend não muda.

## Tarefas

### Task 1 — lista canônica de status + `GET /listings/status-counts`

**Arquivos:** `backend/app/models/listing.py`, `backend/app/schemas/listing.py`,
`backend/app/services/listing_service.py`,
`backend/app/api/v1/endpoints/listings.py`, `backend/tests/test_listagem_em_escala.py` (novo),
`backend/tests/test_status_counts_rota.py` (novo, sem banco).

**1a. Lista canônica** em `backend/app/models/listing.py` (nível de módulo,
antes da classe):

```python
# Ordem do pipeline (mesma do `ListingStatus` do frontend). E' a lista que a
# barra de resumo da fila usa: cada status aparece SEMPRE, com zero quando
# nao ha anuncio, pra barra nao mudar de tamanho a cada atualizacao.
LISTING_STATUSES: tuple[str, ...] = (
    "draft",
    "generating_title",
    "pending_title_approval",
    "predicting_category",
    "pending_seller_attributes",
    "pending_description",
    "generating_images",
    "pending_raw_photos",
    "pending_ai_engine",
    "pending_image_approval",
    "generating_description",
    "ready_to_publish",
    "publishing",
    "published",
    "published_paused",
    "failed",
)
```

**1b. Schema** em `backend/app/schemas/listing.py`, logo depois de `ListingPage`:

```python
class ListingStatusCounts(BaseModel):
    """Contagem de anuncios do seller ativo por status. `by_status` traz TODOS
    os status de `LISTING_STATUSES` (zero quando nao ha anuncio) e, se o banco
    tiver algum status fora da lista (legado), ele tambem aparece — nenhum
    anuncio some da conta. `total` e' a soma."""
    by_status: dict[str, int]
    total: int
```

**1c. Service** — método novo em `ListingService`, logo depois de `list_listings`:

```python
async def count_by_status(self, seller_id: UUID) -> ListingStatusCounts:
    """UMA consulta agregada (GROUP BY status) — nunca carrega linhas."""
    result = await self.db.execute(
        select(Listing.status, func.count().label("cnt"))
        .where(Listing.seller_id == seller_id)
        .group_by(Listing.status)
    )
    by_status = {s: 0 for s in LISTING_STATUSES}
    for row in result.all():
        by_status[row.status] = row.cnt
    return ListingStatusCounts(by_status=by_status, total=sum(by_status.values()))
```

**1d. Endpoint** em `endpoints/listings.py`, declarado **ANTES** de
`GET /{listing_id}` (FastAPI casa rotas na ordem de declaração; declarado
depois, `status-counts` cai em `/{listing_id}`, falha no parse de UUID e
devolve 422). Colocar imediatamente após `list_listings`:

```python
@router.get("/status-counts", response_model=ListingStatusCounts)
async def listing_status_counts(
    active_seller=Depends(get_active_seller),
    db: AsyncSession = Depends(get_db),
):
    """Barra de resumo da fila: contagem por status do seller ativo, em uma
    consulta agregada. Fica ANTES de `/{listing_id}` de proposito — ver
    comentario do roteamento."""
    return await ListingService(db).count_by_status(active_seller.id)
```

**Justificativa do nome** (vai no relatório): sub-recurso da coleção
`/listings`, mesmo escopo de auth e de seller do `GET /listings`; espelha o
nome `listings_by_status` que o dashboard de sellers já usa.

**1e. Testes — vermelho primeiro.**

`backend/tests/test_status_counts_rota.py` (sem banco, roda sempre):
- `test_status_counts_declarada_antes_do_detalhe`: percorre `app.routes`
  (`from app.main import app`), coleta os `path` na ordem, e afirma que o
  índice de `/api/v1/listings/status-counts` é MENOR que o de
  `/api/v1/listings/{listing_id}` (métodos GET). Explicar no docstring por
  que a ordem importa.
- `test_lista_canonica_tem_16_status_sem_repeticao`: `len(LISTING_STATUSES) == 16`
  e `len(set(...)) == 16`, e o conjunto é exatamente o dos 16 listados acima.

`backend/tests/test_listagem_em_escala.py` (Postgres real, `_precisa_db`),
classe `TestContagemPorStatus`. Helper compartilhado do arquivo:
`_semear(session_maker, especificacao)` cria 1 user e N sellers; para cada
seller, cria listings a partir de uma lista de dicts
`{"status": ..., "sku": ..., "title": ..., "brand": ..., "mlb_id": ...}`
(campos além de `status` opcionais). Devolve `(user_id, [seller_ids])`.
Seguir o modelo de `_semear` em `tests/test_bulk_approve_por_posicao.py`
(User com email único, Seller com `ml_user_id` único, Listing com os campos
obrigatórios: `sku_description="d"`, `sku_brand`, `price=10`,
`stock_quantity=1`, `condition="new"`, `listing_type_id="gold_special"`,
`created_via="batch"`). **`mlb_id` é UNIQUE**: gerar valores distintos.

Casos (cada um monta o banco do zero com `_preparar_banco()` e faz
`dispose()` no `finally`):
- seller A com 3 `failed`, 2 `ready_to_publish`, 1 `draft`; seller B com 4
  `failed` → `count_by_status(A)` devolve `failed=3`, `ready_to_publish=2`,
  `draft=1`, `total=6`; **todas** as 16 chaves de `LISTING_STATUSES` presentes;
  `pending_raw_photos == 0` (exemplo de zero); nenhuma chave com valor
  negativo; `sum(by_status.values()) == total`.
- seller sem nenhum anúncio → 16 chaves, todas zero, `total == 0`.
- status legado fora da lista (inserir 1 listing com `status="legado_x"`)
  → aparece em `by_status["legado_x"] == 1` e entra no `total`.

**Ordem obrigatória:** escrever os testes, rodar, mostrar a falha
(ImportError de `LISTING_STATUSES`/`count_by_status`), depois implementar,
rodar de novo, verde. Registrar as duas saídas no relatório.

### Task 2 — filtro por vários status + busca em `GET /listings`

**Arquivos:** `backend/app/services/listing_service.py`,
`backend/app/api/v1/endpoints/listings.py`, `backend/tests/test_listagem_em_escala.py`
(acrescenta classes).

**2a. Endpoint** — assinatura nova de `list_listings`:

```python
@router.get("", response_model=ListingPage)
async def list_listings(
    status: Optional[list[str]] = Query(None),
    search: Optional[str] = Query(None, max_length=200),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    active_seller=Depends(get_active_seller),
    db: AsyncSession = Depends(get_db),
):
    """`status` aceita repeticao (`?status=a&status=b`): cada agrupamento da
    fila junta 3 ou 4 status. Um so (`?status=a`) continua igual a hoje — o
    quadro atual depende disso. `search` casa por SKU, titulo, marca e MLB."""
    return await ListingService(db).list_listings(
        active_seller.id, status, page, page_size, search=search
    )
```

`page_size` fica em `le=200` — **não mudar** (decisão do Daniel: só proposta
no relatório).

**2b. Service** — `list_listings` passa a:

```python
async def list_listings(
    self,
    seller_id: UUID,
    filter_status: str | list[str] | None,
    page: int,
    page_size: int,
    search: str | None = None,
) -> ListingPage:
    query = select(Listing).where(Listing.seller_id == seller_id)

    # Aceita o parametro unico de hoje (str) e a lista da fila. Vazios
    # ("" ou lista vazia) nao filtram — mesmo comportamento do `if filter_status`
    # antigo.
    if isinstance(filter_status, str):
        filter_status = [filter_status]
    statuses = [s for s in (filter_status or []) if s]
    if statuses:
        query = query.where(Listing.status.in_(statuses))

    # Mesmo padrao de `ProductService.list_products`: ilike + or_.
    term = (search or "").strip()
    if term:
        like = f"%{term}%"
        query = query.where(
            or_(
                Listing.sku_external_id.ilike(like),
                Listing.selected_title.ilike(like),
                Listing.sku_brand.ilike(like),
                Listing.mlb_id.ilike(like),
            )
        )
    # ... resto igual (count via subquery, order_by created_at desc, offset/limit)
```

Importar `or_` de `sqlalchemy` no service (conferir os imports existentes).

**2c. Testes — vermelho primeiro**, no mesmo `test_listagem_em_escala.py`,
usando o `_semear` da Task 1. Classes:

`TestFiltroPorVariosStatus`
- seller A com 2 `failed`, 2 `ready_to_publish`, 1 `draft`, 1 `published`:
  - `filter_status=["failed", "ready_to_publish"]` → `total == 4`, todo item
    com status num dos dois.
  - `filter_status="failed"` (str, como hoje) → `total == 2`, todos `failed`.
  - `filter_status=["failed"]` (lista de 1) → mesmo resultado do caso anterior
    (mesmos ids).
  - `filter_status=None` → `total == 6`.
  - `filter_status=[]` → `total == 6` (lista vazia não filtra).
  - status inexistente `["nao_existe"]` → `total == 0`, `items == []`, sem erro.

`TestBusca`
- seller A com: `{"status":"draft","sku":"SKU-ALFA","title":"Perfume Wepink Martin 100ml","brand":"Wepink","mlb_id":"MLB111"}`,
  `{"status":"failed","sku":"SKU-BETA","title":"Body Splash Fatal","brand":"Fatal","mlb_id":"MLB222"}`,
  `{"status":"draft","sku":"SKU-GAMA","title":None,"brand":"Outra","mlb_id":None}`;
  seller B com `{"status":"draft","sku":"SKU-ALFA-B","title":"Perfume Wepink","brand":"Wepink","mlb_id":"MLB333"}`.
  - `search="alfa"` (case-insensitive, parcial) → só SKU-ALFA de A (B não vaza).
  - `search="martin"` → casa pelo título.
  - `search="fatal"` → casa pela marca (e pelo título; 1 item).
  - `search="MLB222"` → casa pelo `mlb_id`.
  - `search="wepink", filter_status=["failed"]` → `total == 0` (combina com
    status); `search="wepink", filter_status=["draft"]` → 1 item (SKU-ALFA).
  - `search="   "` → não filtra (`total == 3`).
  - `search="zzz"` → `total == 0`.
  - nenhum resultado traz `seller_id` diferente de A (checar via ids).

`TestPaginacao`
- seller A com 5 `failed` e 3 `draft`, `filter_status=["failed"]`, `search=None`:
  - `page=1, page_size=2` → 2 itens, `total == 5`, `page == 1`, `page_size == 2`.
  - `page=3, page_size=2` → 1 item, `total == 5`.
  - `page=50, page_size=2` → `items == []`, `total == 5`, sem exceção.
- com `search` que casa 2 dos 5 `failed` (dar títulos distintos) → `total == 2`.

`TestParametroStatusNaRota` (HTTP de verdade via `ASGITransport`, com
`app.dependency_overrides` para `get_active_seller` e `get_db`, ambos de
`app.core.dependencies`; a sessão vem do `session_maker` do `publicar_test`;
limpar `app.dependency_overrides` no `finally`). `get_active_seller` deve
devolver um objeto com `.id` = seller A (um `SimpleNamespace(id=...)` basta;
conferir se o endpoint usa outro atributo — ele só usa `.id`).
  - `GET /api/v1/listings?status=failed&status=ready_to_publish` → 200, `total` certo.
  - `GET /api/v1/listings?status=failed` → 200, igual ao de hoje.
  - `GET /api/v1/listings?search=alfa` → 200.
  - `GET /api/v1/listings/status-counts` → 200 com `by_status` e `total`
    (prova que a rota não cai em `/{listing_id}`).

Vermelho esperado: `TypeError` (`search` inesperado) e assinatura antiga;
mostrar a saída antes e depois.

### Task 3 — migração do índice composto

**Arquivos:** `backend/alembic/versions/d4e8b2a6f9c1_indice_listings_seller_status_created.py`
(novo), `backend/app/models/listing.py` (`__table_args__`),
`backend/tests/test_migracao_indice_listagem.py` (novo).

**Decisão (ruling do controlador, justificar no docstring da migração):**
UM índice composto `ix_listings_seller_status_created` em
`listings (seller_id, status, created_at DESC)`, em vez de três separados:
- a consulta real é sempre `WHERE seller_id = ? [AND status IN (...)] ORDER BY created_at DESC LIMIT n`;
  o composto atende o filtro e, no caso de um status, entrega já na ordem
  (sem Sort); com vários status o planner faz scans por valor e um sort
  pequeno sobre o recorte, não sobre a tabela;
- a contagem `GROUP BY status` por seller é index-only scan no prefixo
  `(seller_id, status)`;
- `seller_id` sozinho fica coberto pelo prefixo — não precisa de índice
  próprio; índices separados precisariam de BitmapAnd e não ajudariam o ORDER BY.
- `search` com `ilike '%x%'` não usa btree (precisaria de `pg_trgm`); fora
  do escopo — a busca roda sobre o recorte já reduzido por seller+status.

**3a. Model** — em `Listing`, logo depois de `__tablename__`:

```python
__table_args__ = (
    # Fila de trabalho: filtro por seller + status, ordenado por created_at
    # desc. Ver migration d4e8b2a6f9c1 para o porque de UM composto.
    Index("ix_listings_seller_status_created", "seller_id", "status", text("created_at DESC")),
)
```

Importar `Index` e `text` de `sqlalchemy`. Conferir que `Base.metadata.create_all`
cria o índice (o teste de migração depende disso).

**3b. Migração** — cabeçalho no padrão de `c8d2f6a4e1b7_*.py` (docstring em
PT-BR com o porquê, `revision = 'd4e8b2a6f9c1'`, `down_revision = 'c8d2f6a4e1b7'`):

```python
def upgrade() -> None:
    op.create_index(
        'ix_listings_seller_status_created', 'listings',
        ['seller_id', 'status', sa.text('created_at DESC')],
    )

def downgrade() -> None:
    op.drop_index('ix_listings_seller_status_created', table_name='listings')
```

**3c. Teste — vermelho primeiro**, `backend/tests/test_migracao_indice_listagem.py`,
copiando a infraestrutura de `tests/test_migracao_eventos_de_revisao.py`
(`_carregar_migracao`, `_rodar_op`, `_preparar_banco`, `_indices`):
- `test_downgrade_remove_e_upgrade_recria_o_indice`: após `create_all` o
  índice existe (model); `downgrade()` → some; `upgrade()` → volta;
  `downgrade()` → some de novo.
- `test_indice_cobre_seller_status_created_desc`: após `upgrade()`, ler
  `pg_indexes.indexdef` do índice e afirmar que contém
  `(seller_id, status, created_at DESC)`.
- `test_revisao_encadeia_no_head_atual`: `mod.down_revision == "c8d2f6a4e1b7"`.
- Confirmar head único: `docker exec publicaradmlb-backend-1 alembic heads`
  deve imprimir só `d4e8b2a6f9c1 (head)`; registrar no relatório. Aplicar no
  banco local de dev: `docker exec publicaradmlb-backend-1 alembic upgrade head`
  e depois `alembic downgrade -1` + `upgrade head` (prova de reversibilidade
  fora do teste também). **Nunca em produção.**

### Task 4 — documentação (CLAUDE.md)

**Arquivo:** `CLAUDE.md` (raiz). Só texto:
- "Endpoints implementados": acrescentar
  `GET /api/v1/listings/status-counts   contagem por status do seller (16 chaves, zeros incluídos) + total`
  e, na linha de `GET /api/v1/listings`, `?status=a&status=b` (repetível) e `search`.
- "Migrations aplicadas": `d4e8b2a6f9c1 — índice composto ix_listings_seller_status_created (seller_id, status, created_at DESC) (head atual)`,
  tirando "(head atual)" de `c8d2f6a4e1b7`.
- "backend/tests/": `test_listagem_em_escala.py`, `test_status_counts_rota.py`,
  `test_migracao_indice_listagem.py` com uma linha cada e a contagem de casos.
- Contagem da suíte: rodar a suíte inteira sem e com `TEST_DATABASE_URL`
  (comandos nos Global Constraints, com `tests/` inteiro) e atualizar a
  linha "Suíte completa: **N passed, M skipped** ...; **K passed** com ela (2026-09-11)".
  Copiar os números da saída real, não estimar.
- Nota curta em "Frontend": o quadro ainda usa `?status=<um>`; a fila (bloco B)
  vai consumir `status-counts` + `status` repetido + `search`.
