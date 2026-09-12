# Publicar AD MLB

Sistema web para automação de criação e publicação de anúncios no Mercado Livre.

## Stack

| Camada | Tecnologia |
|---|---|
| Backend API | Python 3.12 + FastAPI + SQLAlchemy 2.0 (async) |
| Workers | Celery 5 + Redis 7 |
| Banco de dados | PostgreSQL 16 |
| Frontend | Next.js 14 (App Router) + TypeScript + Tailwind + shadcn/ui |
| Storage de imagens | Fotos brutas: bucket público do seller (leitura por URL). Imagens geradas: bucket R2 dedicado `r2-mktp-img-ia` via API S3 (`r2_asset_service`) — **funciona da VPS; o ISP local bloqueia o endpoint S3**, então prova de R2 só a partir do servidor |
| Infra local | Docker Compose |
| Infra produção | **VPS própria** (`vps-360`, Ubuntu 24.04) + Docker Compose + Nginx como proxy reverso + Let's Encrypt. Backend **no ar** em `https://app.360ecomm.com.br` |

---

## Estado das fases

| Fase / Sprint | Status | Descrição |
|---|---|---|
| Fase 0 | ✅ | Planejamento, SPECs (SPEC-000 a SPEC-009), scaffolding, docker-compose |
| Fase 1 | ✅ | Auth JWT, ML OAuth, modelos SQLAlchemy, migration inicial, admin criado |
| Fase 2 | ✅ | Serviço de IA, category service, listing service, endpoints REST, workers Celery reais |
| Fase 3 | ✅ | Pipeline de imagens: Gemini Imagen 4 → ML CDN direto (R2 bypassed: ISP bloqueia r2.cloudflarestorage.com) |
| Fase 4 | ✅ | Geração de descrição (IA) + publicação no ML via API |
| Fase 5 | ✅ | Frontend Next.js 14 completo |
| Sprint 1 | ✅ | Multi-account N:N: tabela `user_seller_access`, header `X-Seller-ID`, seletor de seller na sidebar |
| Sprint 2 | ✅ | Batch import: upload de planilha de anúncios → pipeline avança sozinho até `pending_image_approval`; depois da aprovação humana das imagens para em `ready_to_publish` e só publica por `pipeline/publish` ou `bulk/publish` |
| SPEC-010 | ✅ | Catálogo de Produtos: tabela `products`, ProductService multi-tenant, CRUD via UI e planilha |
| SPEC-011 | ✅ | Listing upload refatorado: planilha de anúncios só tem campos de publicação; dados do produto vêm do catálogo |
| Quick fixes F-1..F-4 | ✅ | Resiliência do pipeline de imagens: ensure_dimensions seguro, _mark_failed robusto, ImageRateLimitError + backoff 429 |
| SPEC-012 | ✅ | Resiliência estrutural do pipeline de imagens (token refresh, idempotência, Celery chain, lock otimista) |
| Trilha 2 · Fase 3 | ♻️ | Cards de benefício com Pillow (benefícios / modo de uso / especificações). **Substituídos pelo esquema de 5 posições e removidos em 2026-09-10** — a copy do LLM sobrevive na posição 2 (`benefits_ai`) |
| Fase 5a | ✅ | Artefatos de produção: `Dockerfile.prod` multi-stage non-root, `docker-compose.prod.yml`, `.dockerignore`, limites de memória. Correção de segurança: `/openapi.json` fechado fora de development |
| Fase 5b | ✅ | Deploy na VPS: vhost + TLS, `.env` de produção gerado do zero, stack no ar, migrations aplicadas. Correção de 2 bugs de OAuth |
| Fase 5c | ✅ | **Primeiro anúncio real publicado**: `MLB5145387291` (SKU 37, Wepink Martin). Validação de `allowed_values`, modo catálogo (`family_name`), cards a partir da capa determinística |
| Frentes A e B | ✅ | Variante de capa e ficha técnica por IA sob demanda: `cover_variant_service`, `specs_variant_service`, `promote_cover`, `promote_specs`, `replace_item_pictures`. Candidato nasce `approved=False` e só vai ao ar por ação humana |
| Ficha ancorada em atributo | ✅ | `build_specs_card` monta os bullets do `value_name` real — o `card_specs` Pillow e a variante de IA param de depender da redação do LLM |
| **Esquema de 5 posições** | ✅ | Padrão único de imagens de **produto único**, em toda categoria: perfil próprio só em MLB6284, as demais usam `PERFIL_PADRAO`. Em produção desde 2026-09-01 (`083ad2e`); universal desde 2026-09-10 |
| SKU 38 | ✅ | 2º anúncio real: `MLB7574387170` (Body Splash Fatal Black For Her 200ml). Publicado com 8 fotos e depois trocado para as 5 do esquema novo |
| Robustez do lote (2026-09-10) | ✅ | Lote validado ponta a ponta (T38 e SKU 45 real). Correções: `EMPTY_GTIN_REASON` condicional, título/copy/descrição sem thinking, prefill pelo `domain_discovery`, título preserva tipo de produto, placeholder "Sem marca", `PERFIL_PADRAO` universal (caminho antigo e reuso removidos), standbys `pending_raw_photos` e `pending_ai_engine` com beat, índices únicos dos slots, log `ai_cost`, `gemini-3.8-flash` fixo, fotos brutas jpg/png/webp, write-back no R2 (`asset_key`, sem blob no banco), write-back por seller removido |
| Rodada 2026-09-11 | ✅ | Lote **para em `ready_to_publish`** depois da aprovação humana (auto-publish removido); `bulk/approve-images` aprova por **posição** (`sort_order < 90` e `ml_picture_id`), não por kind; kind `presentation` → `presentation_ai` (migração de dados); `ImageOut` expõe `kind`/`is_candidate`/`validation_error`; **`listing_review_events`**: 1 evento por aprovação humana, na mesma transação, FK com `ON DELETE CASCADE`; `listing_images.review_seconds` removida; **aprovação que não aprova nada é recusada** nos dois caminhos (individual: 422 antes de qualquer escrita; massa: item falho com `"nenhuma imagem aprovável"`), sem evento, sem mudar status e sem `generate_description`. Provas reais: T38 `7c555deb` (individual), SKU 45 `1b455d45` (massa), T37X `5577e279` (evento), T38 `525001a1` (recusa + aprovação válida) — os quatro em `ready_to_publish`, nunca publicar |
| Fase 6 | 🔲 | Frontend em produção (Vercel ou na própria VPS) + revisão humana de categoria + **tela de revisão/promoção de candidatos** |

> **Railway e Vercel foram descartados para o backend.** A escolha final foi
> VPS própria, que já hospedava outros apps da 360.

> **Atenção à numeração:** "Fase 3" aparece duas vezes. A da linha de cima
> (pipeline de imagens) é da trilha original; a **Trilha 2** é a de qualidade
> de imagem, iniciada depois (`qa-imagens-fase1` → `capa-deterministica-fase2`
> → `cards-beneficio-fase3`). Ao falar de "Fase 3", diga de qual trilha.

---

## Portas locais (Docker)

| Serviço | Porta host | Porta container |
|---|---|---|
| backend (FastAPI) | **8001** | 8000 |
| postgres | 5433 | 5432 |
| redis | 6379 | 6379 |
| pgadmin | 5050 | 80 |
| frontend (Next.js) | 3000 | 3000 |

> A porta do backend é 8001 (não 8000) — conflito resolvido na Fase 1.

---

## Comandos essenciais

```bash
# Subir todo o ambiente
docker compose up -d

# Ver logs
docker compose logs -f backend
docker compose logs -f celery_worker

# Aplicar migrations
docker compose exec backend alembic upgrade head

# Criar nova migration
docker compose exec backend alembic revision --autogenerate -m "descricao"

# Rebuild após mudança de código
docker compose build backend celery_worker
docker compose up -d backend celery_worker celery_beat

# Frontend
cd frontend && npm run dev    # desenvolvimento
cd frontend && npm run build  # checar erros TS
```

---

## Produção (VPS)

| Item | Valor |
|---|---|
| Domínio | `https://app.360ecomm.com.br` (Cloudflare, **DNS only** — proxy laranja ainda não ativado) |
| Acesso | alias SSH `vps-360` (ver `CLAUDE.md` global para as regras de SSH) |
| Diretório do projeto | `/root/publicar-ad-mlb` (fora de qualquer `root` do Nginx) |
| `.env` de produção | `/root/publicar-ad-mlb/.env`, `600 root:root` — gerado do zero, nada copiado do dev |
| Porta interna | `127.0.0.1:8010` → 8000 no container. **Só loopback**; quem fala com ela é o Nginx |
| Vhost | `/etc/nginx/sites-available/app.360ecomm.com.br` |
| Certificado | Let's Encrypt via `certbot --nginx`, renovação pelo `certbot.timer` já existente |
| Código | `git pull` via deploy key dedicada (alias SSH `github-admlb`) |

```bash
# Sempre com -p: o nome de projeto derivado do diretório colide com o de dev
docker compose -p publicar-ad-mlb -f docker-compose.prod.yml up -d --build

# Migrations — passo manual e deliberado, nunca automático no boot
docker compose -p publicar-ad-mlb -f docker-compose.prod.yml run --rm backend alembic upgrade head

# Diagnóstico pós-deploy: a suíte roda dentro da imagem de produção
docker compose -p publicar-ad-mlb -f docker-compose.prod.yml exec backend pytest -q
```

> **`docker compose restart` NÃO relê o `env_file`.** As variáveis são fixadas na
> criação do container. Depois de mudar o `.env`, use
> `up -d --force-recreate <serviço>`. E confira o resultado **por hash**, não por
> comprimento: dois segredos diferentes com o mesmo tamanho fazem a checagem por
> comprimento passar com o valor velho carregado.

> **Limites de memória são obrigatórios aqui.** A VPS tem 2 vCPU, 7.8Gi de RAM e
> **zero swap**, dividida com o Postgres do host, MariaDB e o app de outro
> cliente. Sem `mem_limit`, estourar memória faz o OOM killer escolher uma vítima
> qualquer — possivelmente o processo do vizinho.

---

> **A qualidade da foto bruta limita o teto do resultado.** Metade das imagens
> do SKU 37 saía com o texto "MARTIN" marmorizado, e a suspeita natural foi o
> motor. Não era: **o defeito já estava na `37-2.jpg` original** — o modelo
> estava sendo fiel a ela. Trocar a foto resolveu; nenhum ajuste de prompt
> resolveria, porque a informação não existia na entrada. Antes de culpar o
> modelo por texto ruim, **abrir a foto de origem**.

---

## Variáveis de ambiente

Copie `.env.example` para `.env`. NUNCA commite `.env`.

Chaves relevantes:
- `ML_APP_ID`, `ML_CLIENT_SECRET`, `ML_REDIRECT_URI` — app Mercado Livre
- `SECRET_KEY` — JWT (hex 64 chars)
- `FERNET_KEY` — criptografia de tokens ML (base64 Fernet)
- `POSTGRES_PASSWORD`, `REDIS_PASSWORD`
- `AI_PROVIDER` — `gemini` (padrão) ou `claude`
- `GEMINI_API_KEY` — usado só para Gemini Flash (texto). O Imagen 4 (texto-imagem) foi removido em 2026-09-10
- `GEMINI_MODEL` — modelo de texto. **Nome explícito, nunca alias `-latest`** (hoje `gemini-3.8-flash`; ver comentário em `config.py`). Título em lote, copy dos cards e descrição rodam com `thinkingBudget: 0` e teto folgado, porque o budget zero é melhor esforço nesse modelo
- `ANTHROPIC_API_KEY` (se usar Claude como provider)
- `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET_NAME`, `R2_PUBLIC_URL` — Cloudflare R2 legado (configurado mas não usado)
- `R2_ASSET_BUCKET_NAME`, `R2_ASSET_BUCKET_ENDPOINT`, `R2_ASSET_BUCKET_ACCESS_KEY_ID`, `R2_ASSET_BUCKET_SECRET_ACCESS_KEY` — bucket R2 **dedicado** para as imagens geradas por IA (write-back na geração, `services/r2_asset_service.py`). Distinto do bucket de fotos brutas dos sellers. Vazio = a geração continua, mas as linhas nascem com `asset_key=None` e o log avisa. O banco **não guarda mais blob**: `ListingImage.asset_key` aponta para `{apelido_ml}/{sku}/{kind}-{AAAAMMDD-HHMMSS}-{4hex}.jpg` (ex.: `CAFE085/37/cover_ai-20260910-234512-ab12.jpg`), montado só em `asset_key_for()`

- `ENVIRONMENT` — **default inseguro**: enquanto o valor for `development`, o
  `main.py` publica `/docs` **e** `/openapi.json`. Todo ambiente que não for dev
  explícito precisa de `ENVIRONMENT=production`. O `docker-compose.prod.yml`
  crava o valor nos 3 serviços para não depender do `.env` do servidor.
- `FRONTEND_URL` — vazia por padrão. Vazia, o callback do OAuth devolve
  `{"status": "connected"}`; preenchida, redireciona para
  `<FRONTEND_URL>/settings?ml_connected=true`.
- `ALLOWED_ORIGINS` — é `list[str]`, o pydantic-settings **só aceita JSON**.
  `ALLOWED_ORIGINS=https://x` derruba o boot com `SettingsError`; a forma certa é
  `ALLOWED_ORIGINS=["https://x"]`. Em produção está **omitida** de propósito.

> `FREEPIK_API_KEY` não é mais necessário — FreePik foi descartado. Imagens são **edição** das fotos brutas do seller (`OpenAIEditEngine`); não existe mais geração do zero.

> **`OPENAI_IMAGE_MODEL`: o `.env` mascara o default do `config.py`.** O default
> no código é `gpt-image-2`, mas produção rodou semanas com `gpt-image-1`
> porque o `.env` (copiado do de dev na Fase 5b) trazia o valor antigo, e
> `.env` sempre vence o default. O sintoma foi rótulo corrompido nas imagens
> (`160ml` no lugar de `100ml`, `weoink` no lugar de `wepink`). **Conferir o
> valor carregado em RUNTIME dentro do container, não no arquivo** — e não só
> no backend: quem instancia o `OpenAIEditEngine` é o `celery_worker`.

> **`FERNET_KEY` NÃO pode vir de `openssl rand -hex 32`.** O `Fernet()` exige 32
> bytes em **base64 url-safe** (44 chars). Pior: `_fernet` é construído em nível
> de módulo em `app/core/security.py`, então chave inválida não falha na primeira
> criptografia — derruba o import e põe o container em crash-loop. Gere com
> `Fernet.generate_key()` ou `openssl rand 32 | base64 -w0 | tr '+/' '-_'`.

---

## Uso do Subagent-Driven Development (SDD)

Antes de qualquer implementação, Claude deve avaliar a complexidade e **anunciar a decisão** antes de começar, aguardando confirmação do usuário.

### Quando usar SDD
- 5 ou mais tasks independentes
- Múltiplos arquivos com integração entre si (ex: migration + service + endpoint + frontend)
- Risco real de regressão em funcionalidades existentes
- Features estruturais (auth, pipeline, multi-tenant, workers Celery)

### Quando implementar direto (sem SDD)
- Até 4 arquivos modificados
- Spec clara com código já definido
- Baixo risco de regressão
- Bug fixes, ajustes de UI, novos campos simples, endpoints CRUD isolados

### Anúncio obrigatório antes de implementar
Claude deve sempre declarar, antes de começar qualquer implementação:

> "Esta feature é **[simples/complexa]** — vou implementar **[diretamente/via SDD]** porque **[razão em uma linha]**."

Aguardar confirmação do usuário antes de prosseguir.

---

## Convenções de código

- **API**: REST, sempre versionada em `/api/v1/`
- **Auth**: JWT Bearer em todos os endpoints, exceto `/health` e `/api/v1/auth/ml/callback`
- **Multi-tenant**: header `X-Seller-ID` identifica o seller ativo; validado contra `user_seller_access` no middleware
- **Erros**: sempre retornar `{"detail": "mensagem"}` com o status HTTP correto
- **Segurança**: tokens ML criptografados no banco (Fernet); chaves de API nunca em código
- **Branches**: `feature/nome`, `fix/nome`, `chore/nome`
- **Commits**: Conventional Commits — `feat:`, `fix:`, `chore:`, `docs:`, `test:`

---

## Padrões de código estabelecidos

### Services
```python
class XService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
```
Instanciados nos endpoints com `XService(db)`.

### Workers Celery (tasks assíncronas)
```python
@celery_app.task(bind=True, max_retries=3)
def my_task(self, listing_id: str) -> dict:
    try:
        return asyncio.run(_my_task_async(listing_id))
    except Exception as exc:
        raise self.retry(exc=exc, countdown=2 ** self.request.retries * 5)

async def _my_task_async(listing_id: str) -> dict:
    from app.database import worker_session  # import aqui, não no topo
    async with worker_session() as db:
        ...
```

### Batch dispatch atômico (SPEC-012)
Nos gatilhos batch (`category_tasks.py`, `listing_service.submit_attributes`, `listing_service.bulk_generate_images`, `raw_photo_standby_service`), a transição de status e o dispatch são feitos atomicamente:
```python
from sqlalchemy import update as sa_update
result = await db.execute(
    sa_update(Listing)
    .where(Listing.id == listing_id, Listing.status == "pending_description")
    .values(status="generating_images")
    .execution_options(synchronize_session=False)
)
await db.commit()
if result.rowcount == 1:
    from app.workers.tasks.image_tasks import generate_images
    generate_images.delay(listing_id)
```
- `rowcount == 0` → outro worker ganhou a race condition, não despacha nada
- **Só `generate_images` é despachado.** O listing para em `pending_image_approval` até um humano aprovar as imagens; `generate_description` só é disparado por `approve_images`/`bulk_approve_images` e termina em `ready_to_publish`. `publish_listing` **nunca** é enfileirado por caminho automático — só por `trigger_publish` (`pipeline/publish`) e `bulk_publish` (`bulk/publish`), em lote também
- `execution_options(synchronize_session=False)` obrigatório no async SQLAlchemy

### Idempotência em workers batch
No início de `_generate_images_async`, antes de qualquer processamento:
```python
if listing.status != "generating_images":
    return {"listing_id": listing_id, "skipped": True}
```
Protege contra double-dispatch (retry ou bug de enfileiramento duplo).

### Sessão de banco nos workers
`worker_session()` está em `app.database` — usar como context manager async.
Nos endpoints, usa-se `Depends(get_db)` de `app.core.dependencies`.

### Lazy loading de relacionamentos
Nunca passar um objeto ORM com relacionamentos lazy direto para `Model.model_validate()` — causa `MissingGreenlet`. Sempre carregar os relacionamentos com queries separadas.

### Provider de IA — 3 métodos abstratos
`AIProvider` (`ai/base.py`) declara `generate_titles`, `generate_description`
e `generate_card_copy`. **Provider novo tem que implementar os 3** — faltar um faz a classe estourar `TypeError` na
instanciação. Os prompts ficam centralizados em `ai/prompts.py` como
`build_*_prompt()`; `gemini.py` e `claude.py` compartilham a assinatura básica
`_call(prompt, max_tokens, temperature)`; o Gemini ainda aceita `thinking` e `task`
(log `ai_cost`) e levanta erro em `finishReason=MAX_TOKENS` em vez de devolver
texto cortado. `claude.py` reusa `_extract_json` de `gemini.py` e **não** loga custo
(fora de uso).

### Atributo de lista: `allowed_values` só é enumeração quando o tipo é `list`

O ML usa `values` de **dois jeitos**, e o `value_type` distingue:

| Tipo | Significado de `values` | Comportamento |
|---|---|---|
| `list` | enumeração fechada | valor fora dela é descartado / **422** |
| `string` | lista de **sugestões** | texto livre passa; o ML resolve o `value_id` |

Tratar os dois igual bloqueava dado legítimo: `BRAND` em MLB6284 devolve 24
sugestões, "Wepink" não está entre elas — e mesmo assim o anúncio
`MLB5145387291` está **ativo** nessa categoria com
`BRAND value_id='13065330' value_name='Wepink'`, id que o próprio ML atribuiu.
A regra vale nos **dois** pontos que precisam concordar: `_save_attributes`
(prefill) e `_validar_valor` (submit).

`submit_attributes` recusa com **422** listando os aceitos, e resolve o
`value_id` sozinho quando o cliente manda só o nome.

Sem isso o erro só aparece na publicação, como
`Attribute [X] is not valid, item values [(null:Y)]` — mensagem obscura, no
momento mais caro, depois de já ter gasto geração de imagem e descrição. O
caso real: `"Colônia"` é válido em MLB178938 (perfume **pet**) e inexistente
em MLB6284 (perfumes), onde o equivalente é `"Água de colônia"`.

### Modo catálogo do ML: `family_name` em vez de `title`
Algumas categorias recusam `title` e exigem `family_name` — os dois são
**mutuamente exclusivos**. A detecção **não** usa campo da categoria:
`settings.catalog_domain` existe em TODAS as categorias verificadas
(perfumes, desodorantes, celulares, perfume pet), então gatear nele mandaria
todo anúncio para o modo catálogo. `publish_service` tenta com `title` e, se
o ML recusar por falta de `family_name`, refaz sem `title`.

### Nada de estado em memória de processo
A API roda com `uvicorn --workers 2` em produção. Qualquer estado guardado em
variável de módulo (dict, cache, contador) vive **num worker só**: uma
requisição grava, a seguinte cai no outro processo e não encontra nada. Falha
intermitente, sem erro no log.

Foi exatamente o bug do state do OAuth (`ml_oauth_service.py`), que ficava num
`dict` de módulo e quebrava o fluxo em ~metade das tentativas. Estado
compartilhado vai para o **Redis**, que já é o broker do Celery:

```python
await client.setex(f"ml_oauth_state:{state}", 600, user_id)   # TTL sempre
valor = await client.getdel(chave)   # atômico: impede replay
```

O TTL não é detalhe: sem ele, fluxo abandonado nunca é limpo.

### Bcrypt
Usa `bcrypt` diretamente, sem `passlib` (incompatível com bcrypt 4.x).
Ver `app/core/security.py`: `hash_password()` e `verify_password()`.

### Models no Alembic
**Todo model novo deve ser importado em `app/models/__init__.py`** para que o Alembic detecte as tabelas e resolva as FKs. Omitir causa `NoReferencedTableError` na geração de migrations.

---

## Arquitetura de duas planilhas

**TGFPRO (catálogo de produtos), 24 colunas:** sku, descricao, marca, modelo, ean, ncm, origemfiscal, icmscst, icmsrate, piscst, cofinscst, pesokg, comprimentocm, larguracm, alturacm, custo, grupo_produto, referencia_tecnica, aplicacao_veiculo, cor, tamanho, capacidade, material, genero. As 8 últimas são os campos estruturados para geração de título (SPEC-013); o parser normaliza o cabeçalho (remove `_`, acentos e espaços) antes de mapear
- Upload via `POST /api/v1/products/upload` ou formulário `/products/new` / `/products/{sku}/edit`
- Multi-tenant: `seller_id` em todas as queries via `ProductService._base_query()`

**TGFMLB (anúncios):** sku, preco, estoque, tipo, condicao, seo
- Upload via `POST /api/v1/import` → batch pipeline automático
- Valida existência do produto por `(seller_id, sku)` antes de criar listing
- Denormaliza campos do produto no listing no momento da criação

**Modelos XLSX disponíveis para download:** gerados client-side com ExcelJS (Calibri 11pt, cabeçalho negrito fundo cinza `#E2E8F0`). EAN e NCM pré-formatados como texto (`numFmt: "@"`). Suporta `;` e `,` como separador CSV (auto-detect no backend). Suporta `,` e `.` como decimal.

---

## Arquivos implementados

### backend/app/models/
- `base.py` — Base, TimestampMixin
- `user.py` — User
- `seller.py` — Seller (access_token_enc, refresh_token_enc, token_expires_at)
- `user_seller_access.py` — UserSellerAccess (user_id, seller_id, role) — tabela N:N
- `product.py` — Product (sku, description, brand, **model**, ean, ncm, fiscal, físico, custo)
- `listing.py` — Listing (sku_external_id, sku_description, sku_brand, **sku_model**, price, status, ...) + **`approved_image_count`**, uma `column_property` com subconsulta correlata em `listing_images` (`approved` e `sort_order < CANDIDATE_SORT_ORDER_FLOOR`): sai na **mesma** consulta de qualquer `select(Listing)`, então a listagem de 200 continua em 2 statements e os endpoints que fazem `ListingSummary.model_validate(listing)` recebem o valor sem consulta a mais. **Não** usar `deferred=True`: no async o carregamento tardio estoura `MissingGreenlet` na serialização. Objeto `Listing(...)` criado em memória e nunca carregado tem o atributo `None` (testes com listing falso passam `approved_image_count=0`)
- `listing_title.py` — ListingTitle + índice simples `ix_listing_titles_listing_id` (migração `f7b3e9c1d2a5`; lida por `listing_id` em 5 pontos)
- `listing_attribute.py` — ListingAttribute (allowed_values JSONB, is_required, source, **`tags` JSONB** com o dicionário de tags do ML inteiro) + propriedade **`is_editable`** (`False` só com `hidden` ou `read_only`; `tags` nulo = editável, na dúvida mostrar; `fixed` fora da regra por decisão pendente) — a única definição, o frontend não reimplementa
- `listing_image.py` — ListingImage (ml_picture_id, approved, sort_order, kind, validation_error) + propriedade `is_candidate` (`sort_order >= CANDIDATE_SORT_ORDER_FLOOR`), a única definição de candidata. Três índices em `listing_id`: os dois **parciais** de slot (`uq_listing_images_cover_slot`, `uq_listing_images_specs_slot`, únicos, com `WHERE` de capa/ficha aprovada) e, desde `f7b3e9c1d2a5`, o **simples** `ix_listing_images_listing_id`, que coexiste com eles e cobre o que eles não cobrem: "todas as imagens deste anúncio" (a subconsulta de `approved_image_count` fazia Seq Scan por linha da listagem sem ele: 153 ms → 1,2 ms na página de 200)
- `listing_review_event.py` — ListingReviewEvent (listing_id, user_id, action, mode, approved_count, review_seconds, created_at) — 1 linha por aprovação humana de imagens, imutável (sem `updated_at`). Apaga junto com o listing (FK `ON DELETE CASCADE` + `cascade="all, delete-orphan"` em `Listing.review_events`); `user_id` não cascateia
- `listing_description.py` — ListingDescription
- `listing_job.py` — ListingJob + índice simples `ix_listing_jobs_listing_id` (migração `f7b3e9c1d2a5`; lida só no detalhe, mas a tabela só cresce, recebe INSERT num único ponto e o detalhe é a tela mais aberta)
- `product_image.py` — ProductImage (seller_id, sku, ml_picture_id, is_approved) — índice SKU→imagem. **Sem escrita nem leitura desde 2026-09-10**; fica como registro histórico dos SKUs 37/38 até decisão de apagar
- `batch_import.py` — BatchImport + BatchImportRow

### Arquivos de produção (raiz e backend/)
- `docker-compose.prod.yml` — stack de produção; `mem_limit` em todos os serviços, postgres/redis sem `ports:`, backend só em `127.0.0.1:8010`, sem pgadmin nem frontend
- `backend/Dockerfile.prod` — multi-stage (toolchain fica no estágio de build), usuário non-root `appuser` uid 10001, uvicorn com 2 workers e sem `--reload`
- `backend/.dockerignore` — exclui `.env` explicitamente, como rede de segurança caso o build context mude de `./backend` para `.`
- `backend/pytest.ini` — `cache_dir = /tmp/pytest_cache`: `/app` pertence ao root e o processo roda como `appuser`. Dar `chown` em `/app` deixaria o código gravável pelo usuário de runtime, anulando metade do ganho do non-root

### backend/app/services/
- `auth_service.py` — login, refresh token
- `ml_oauth_service.py` — OAuth ML, troca code→token, refresh token ML
- `ai/base.py`, `ai/gemini.py`, `ai/claude.py`, `ai/prompts.py`, `ai/service.py` — providers de IA
- `category_service.py` — CategoryService: prediz categoria ML + salva atributos; pré-preenche BRAND, MODEL, GTIN, SELLER_SKU, dimensões, peso com unidades corretas
- `listing_service.py` — ListingService: CRUD + pipeline
- `image_service.py` — MLPictureService + `validate_image()` + `ensure_dimensions()` (upscale para 1024px antes do upload) + `ImageRateLimitError` (HTTP 429 → backoff 60s×2^retries)
- `publish_service.py` — PublishService + `get_valid_access_token()` + MLValidationError
- `product_service.py` — ProductService (multi-tenant via `_base_query()`): list, get, create, update, upsert
- `product_import_service.py` — parser CSV/XLSX de produtos (auto-detect delimitador)
- `batch_import_service.py` — parser CSV/XLSX de anúncios (auto-detect delimitador)
- `image_card_copy_service.py` — copy via LLM (`generate_card_copy()`, só `card_benefits` é consumido hoje, pela posição 2) e `build_specs_card()` (bullets determinísticos da posição 4). **Nunca levanta exceção** — devolve `[]` em falha e descarta ângulo inutilizável. Sanitiza sem confiar no LLM: trunca título em 40 e bullets em 50 chars, exige 2–3 bullets, e roda uma **denylist de conteúdo proibido pelo ML** (preço, URL, telefone, frete grátis, superlativo) no texto **cru, antes de truncar** — truncar o preço para fora não pode servir de lavagem
- `cover_variant_service.py` — Frente A: `generate_cover_variant()` (candidato `cover_ai`), `promote_cover()`, `_load_latest_deterministic_cover()`. `_pick_prompt()` devolve **sempre** o prompt leve — capa branca em toda categoria. `_COVER_PROMPT_RICH` fica no módulo **dormant**, para o toggle por seller no frontend; há teste que falha se alguém apagá-lo
- `specs_variant_service.py` — Frente B: `generate_specs_variant()` (candidato `specs_ai`), `promote_specs()`, `_build_specs_prompt()`. O prompt **não tem título**: a ficha sai só com bullets, e proíbe cabeçalho explicitamente — omitir sem proibir convida o motor a inventar um
- `image_position_profiles.py` — `PERFIL_PADRAO` (genérico, fallback universal) + tabela `{categoria_folha: PositionProfile}` de perfis lapidados por vertical. Carrega canvas **e** conteúdo, então um perfil novo (Moda em 4:5) é uma linha a mais. `detail_caption_for()` deriva a legenda do SKU, não sorteia: regerar não pode trocar a legenda por baixo de uma revisão já feita
- `image_position_prompts.py` — prompts das posições 2, 3 e 4. Cláusula `CRITICAL` **idêntica** nas três, palavra por palavra

> **`build_specs_card` continua devolvendo `title`.** A remoção do cabeçalho
> vale só para o prompt da IA; o card Pillow (`card_specs`) mantém o dele.

### backend/app/api/v1/endpoints/
- `health.py`, `auth.py`, `listings.py`
- `products.py` — CRUD de produtos + upload de planilha
- `import.py` — batch import de anúncios + histórico

### backend/app/schemas/
- `listing.py` — `ImageOut` (`id`, `ml_picture_id`, `status`, `approved`, `sort_order`, `kind`, `is_candidate`, `validation_error`): o que `GET /listings/{id}` devolve por imagem. **`is_candidate` é calculado no backend** (`ListingImage.is_candidate`); o frontend consome o booleano e **não** reimplementa a comparação de `sort_order`. `validation_error` é o motivo quando `status = validation_failed`. Espelho TS em `frontend/src/types/listing.ts`
- `listing.py` — `AttributeOut` também devolve `tags` (dicionário do ML como veio) e `is_editable` (lido da propriedade do model via `from_attributes`). Espelho TS em `frontend/src/types/listing.ts`; **nenhuma tela filtra por isso ainda** (bloco B)
- `listing.py` — `ListingSummary` (o item de `GET /listings` e o retorno dos endpoints de ação) passou a trazer **`sku_description`** (a fila mostra no lugar do título quando `selected_title` é `NULL`), **`ml_category_id`** (coluna Categoria da fila; o caminho completo é da tela de revisão) e **`approved_image_count`** (marca "Incompleto (n/5)" quando 0 < n < 5; zero é "ainda não revisado"). `ListingDetail` **herda** os três de `ListingSummary` em vez de redeclarar: a contagem já vem na linha do listing, sem consulta extra, e a lista `images` do detalhe é quem detalha. Espelho TS em `frontend/src/types/listing.ts`

### backend/app/workers/tasks/
- `ai_tasks.py` — `generate_title`, `generate_description`
- `category_tasks.py` — `predict_category` (batch: UPDATE atômico `pending_description → generating_images` + só `generate_images.delay`, se sem attrs pendentes)
- `image_tasks.py` — `generate_images` (`_fetch_upload_token` para refresh automático de token ML; guard de idempotência; **sempre gera**, sem reuso via ProductImage desde 2026-09-10; sem foto bruta no bucket → `pending_raw_photos`, nunca fallback; `ensure_dimensions` antes do upload)
- `raw_photo_tasks.py` — `check_pending_raw_photos` (beat, 15 min): retoma listings em `pending_raw_photos` quando as fotos aparecem no bucket. Lógica em `services/raw_photo_standby_service.py` (`try_resume_raw_photos`, UPDATE atômico + reentrada por `dispatch_image_generation`, que é só `generate_images.delay`)
- `ai_engine_tasks.py` — `check_pending_ai_engine` (beat, 15 min): redispara listings em `pending_ai_engine` (motor OpenAI fora: crédito, 401/403, 5xx, timeout). Sem pré-checagem, a OpenAI não expõe saldo: tentar é a checagem. `_tentar` deixa `ImageEngineUnavailableError` subir na última tentativa e o worker aborta a geração inteira (rollback das posições parciais) em vez de seguir com galeria parcial ou cair em `failed` 
- `publish_tasks.py` — `publish_listing` (MLValidationError → failed sem retry)
- `batch_tasks.py` — `process_batch` (lê planilha, cria listings, dispara pipeline)

### backend/tests/
> **`conftest.py` bloqueia a rede em toda a suíte.** Fixture autouse que faz
> qualquer egresso HTTP real levantar `NetworkAccessAttempted` nomeando a URL.
> O patch é na camada de **transporte** do httpx (`AsyncHTTPTransport.handle_async_request`
> / `HTTPTransport.handle_request`) e no `botocore`, **não** no `AsyncClient` —
> por isso `ASGITransport` (usado em `test_health.py`) e `MockTransport` seguem
> funcionando. Escape hatch: `@pytest.mark.allow_network`. Se um teste novo
> esquecer de mockar o provider de IA, ele falha em alto e bom som em vez de
> gastar chamada paga.

- `test_image_service.py` — `TestEnsureDimensions` (5 casos)
- `test_image_tasks.py` — `TestMarkFailed` (4), `TestGenerateImagesRateLimit` (2), `TestFetchUploadToken` (2), `TestGenerateImagesIdempotency` (2)
- `test_batch_dispatch.py` — `TestCategoryTaskDispatchesGenerateImages` (3), `TestSubmitAttributesDispatchesGenerateImages` (1), `TestSubmitAttributesReadyToPublishNaoPublicaSozinho` (1), `TestRemovedInternalDispatch` (1): gatilhos de lote despacham só `generate_images.delay`, nunca `publish_listing`
- `test_lote_para_em_ready_to_publish.py` — `approve_images` e `bulk_approve_images` em lote → `generate_description` → `ready_to_publish`, `publish_listing` nunca chamado (2)
- `test_image_card_copy_service.py` — saneamento da copy, denylist de conteúdo (true positives + **13 casos de falso positivo**: `12V`, `3,5cm`, `500ml`, `12,50 m`, `99,9%`, `5000mAh`…)
- `test_perfil_padrao.py` — `PERFIL_PADRAO` para categoria sem perfil próprio, nunca None, roteamento para as 5 posições, nunca auto-aprova em lote
- `test_r2_asset_store.py` — bucket R2 de ativos: chave `{apelido_ml}/{sku}/{kind}-{timestamp}-{4hex}` (sem colisão no mesmo segundo, ordem cronológica pelo nome), put/get via boto3, sem credencial não levanta, `_salvar_posicao` grava aprovada (bytes preparados) e reprovada (bytes crus), model sem `image_bytes`
- `test_ml_oauth_state.py` — state do OAuth no Redis (incluindo o cenário "inicia num worker, completa em outro") e destino pós-callback com/sem `FRONTEND_URL`
- `test_cover_variant.py` / `test_cover_promote.py` — Frente A: capa sempre branca, prompt rico dormant, candidato reprovado guarda os bytes, promoção idempotente e autocurável
- `test_specs_variant.py` / `test_specs_promote.py` — Frente B: ficha sem título, promoção lendo o slot da galeria em vez de cravar número
- `test_specs_card_deterministic.py` — bullets do `value_name` real (200 execuções → 1 resultado) e a linguagem visual unificada do prompt
- `test_cinco_posicoes.py` — roteamento por categoria-folha, nenhuma posição nasce aprovada, canvas do perfil, falha de uma não derruba as outras, capa determinística como fallback invisível
- `test_allowed_values_por_tipo.py` — `values` é enumeração só em `value_type == "list"`; EAN do produto chegando ao GTIN
- `test_ml_replace_pictures.py` — substituição TOTAL de fotos: recusa lista vazia, ID repetido e perda de `must_keep`
- `test_bulk_approve_por_posicao.py` — Postgres real (só com `TEST_DATABASE_URL`): `bulk_approve_images` aprova as 5 posições (0–4, inclusive `cover_ai`/`specs_ai` oficiais e a `cover_deterministic` de fallback) e deixa as candidatas 90/91 `approved=False`; os 2 últimos cobrem a condição do `ml_picture_id` (posição reprovada no QA não é aprovada, nem quando há fallback) (4)
- `test_recusa_aprovacao_vazia.py` — Postgres real (só com `TEST_DATABASE_URL`): aprovação que não aprova nada é recusada nos dois caminhos. Em massa: tudo reprovado no QA → item `success=False` com `"nenhuma imagem aprovável"`, zero eventos, status intacto, `generate_description` não disparado; lote misto (vazio primeiro) → o normal é aprovado e o vazio falha sem contaminá-lo. Individual: ids de outro anúncio → 422 **antes de qualquer escrita** (nenhuma imagem vira `rejected`); lista vazia continua 422 (4)
- `test_status_counts_rota.py` — sem banco (sempre roda): `GET /listings/status-counts` declarada antes de `GET /{listing_id}` (ordem de rota do FastAPI) e `LISTING_STATUSES` com 16 chaves sem repetição (2)
- `test_listagem_em_escala.py` — Postgres real (só com `TEST_DATABASE_URL`): `TestContagemPorStatus` — `count_by_status` agrega em UMA consulta e sempre devolve as 16 chaves, zero quando vazio, status legado aparece e entra no total (3); `TestFiltroPorVariosStatus` — `?status=` repetido junta os grupos, string única continua igual a hoje, lista vazia/None não filtra (6); `TestBusca` — `ilike` sobre SKU/título/`sku_description`/marca/mlb_id (a descrição casa mesmo sem título escolhido), combinada com filtro de status, isolamento por seller (9); `TestPaginacao` — com filtro de status, com busca e sem duplicata/lacuna quando `created_at` empata (batch import, prova o desempate por `id` em `order_by`) (3); `TestParametroStatusNaRota` — `status` repetido e `search` na rota real, `status-counts` não cai no path param (4) (25)
- `test_migracao_indice_listagem.py` — migração `d4e8b2a6f9c1`: `TestIndiceListagemEmEscala` (Postgres real, só com `TEST_DATABASE_URL`) — downgrade remove/upgrade recria o índice composto, índice cobre `(seller_id, status, created_at DESC)` (2); `test_revisao_encadeia_no_head_atual` — sem banco, prova que a migração encadeia no head (1) (3)
- `test_atributos_tags_ml.py` — `is_editable` (hidden/read_only → False; nulo, `{}`, só `required`, `fixed` → True) (6); `_save_attributes` grava o dicionário de tags cru (NULL quando o ML não manda) e `is_required` continua igual (4); `AttributeOut` expõe `tags`/`is_editable` (2); `GET /listings/{id}` traz os dois por atributo (Postgres real) (1) (13)
- `test_migracao_tags_atributos.py` — migração `e5f9c3b7a2d4`: encadeia no head (sem banco) (1); downgrade remove / upgrade recria a coluna JSONB nullable (Postgres real) (1) (2)
- `test_listing_summary_para_fila.py` — Postgres real (só com `TEST_DATABASE_URL`): `ListingSummary.model_validate` de uma linha real traz `ml_category_id`, `sku_description` e `approved_image_count` (1); contagem de aprovadas na mesma resposta de `list_listings` — 5→5, 4→4, candidata aprovada em `sort_order` 90 não conta, 5 geradas sem aprovar → 0, sem imagem → 0 (1); `list_listings` emite o mesmo número de statements com 1 e com 10 anúncios (contagem + página; imprime o SQL com `-s`) (1); `GET /listings` real devolve os três campos por item (1) (4)
- `test_migracao_indices_fk.py` — migração `f7b3e9c1d2a5`: encadeia no head (sem banco) (1); Postgres real — downgrade remove os três índices e preserva os dois parciais de `listing_images`, upgrade recria, downgrade de novo remove (1); os três são btree simples em `(listing_id)`, sem `WHERE`, e o `indexdef` do `create_all` (models) é idêntico ao da migração (1) (3)

> Suíte completa: **472 passed, 56 skipped** sem `TEST_DATABASE_URL`; **528 passed** com ela (2026-09-12, medido em `f9f3478`). Os pulados são os testes
> com Postgres real (`test_bulk_approve_por_posicao.py`, `test_eventos_de_revisao.py`, `test_recusa_aprovacao_vazia.py`, `test_listagem_em_escala.py`, os de migração — incluindo `test_migracao_indice_listagem.py` — e a corrida real de
> `test_promocao_indice_unico.py`), que só rodam com `TEST_DATABASE_URL` apontando para o banco
> local dedicado `publicar_test` (ver memória do projeto). O `conftest` põe o broker do Celery em
> `memory://`, então a suíte pode rodar dentro da imagem de produção sem enfileirar nada no Redis real.
> Os testes reais fazem `drop_all`/`create_all` no `publicar_test` e são donos exclusivos dele — nunca rodar duas suítes (ou uma suíte e um arquivo avulso) contra ele ao mesmo tempo; a colisão aparece como `DBAPIError` em `DROP TABLE`.

### Migrations aplicadas (ordem cronológica)
- `a7519acf4e00` — schema inicial (8 tabelas)
- `08c6a96e1502` — coluna `allowed_values JSONB` em `listing_attributes`
- *(várias)* — multi-account, batch_import, product_images, products
- `d3aa35ba6d71` — `products.model` + `listings.sku_model` + fix índices
- `c1d5e8b3a207` — `validation_error` em `listing_images`
- `2f769b55c74e` — `image_bytes` + `review_seconds` em `listing_images`
- `9c4d2e7f1a55` — drop de `image_engine_state` (subsistema de motor de imagem removido)
- `3d8f1b2c9e47` — índices únicos parciais dos slots de capa e ficha (`uq_listing_images_cover_slot` / `_specs_slot`)
- `5a1c7e2d9b04` — `listing_images.asset_key` (referência dos bytes no bucket R2 de ativos)
- `7b2d9f4e1c58` — drop de `listing_images.image_bytes`. **Só rodar depois de** `scripts/migrate_image_bytes_to_r2.py` e de backup da tabela
- `8e3a5c1d7f92` — remove o write-back por seller (RF7): `seller_image_configs.write_*`, `listing_images.r2_write_status` e `url_r2`. Ver `docs/superpowers/specs/2026-09-10-entrega-ao-seller-bucket-proprio-pausada.md`
- `9f4c2b7e1d63` — kind `presentation` → `presentation_ai` em `listing_images` (só dados, sem mudança de schema)
- `b3e7a1c9d5f2` — cria `listing_review_events` (FK de `listing_id` com `ON DELETE CASCADE`)
- `c8d2f6a4e1b7` — drop de `listing_images.review_seconds` (o tempo de revisão vive só no evento)
- `d4e8b2a6f9c1` — índice composto `ix_listings_seller_status_created` (seller_id, status, created_at DESC)
- `e5f9c3b7a2d4` — `listing_attributes.tags` (JSONB nullable, sem preenchimento retroativo: linhas antigas ficam NULL = editáveis)
- `f7b3e9c1d2a5` — índices simples em `listing_id` de `listing_images` (`ix_listing_images_listing_id`, coexiste com os dois parciais de slot), `listing_titles` e `listing_jobs`. As outras 4 FKs sem índice (`listings.created_by`, `batch_imports.created_by`, `listing_review_events.user_id`, `batch_import_rows.listing_id`) ficaram de fora de propósito: nenhuma consulta filtra por elas (ver docstring da migração) (head atual)

---

## State machine do Listing

```
draft
  └─(pipeline/start)──► generating_title
                           └─(worker OK)──► pending_title_approval         [manual]
                                              └─(titles/{id}/select)──► predicting_category
                           └─(batch_mode)──► (auto-seleciona) ──► predicting_category
                                                                     └─(worker OK, attrs preenchidos)──► pending_description
                                                                     └─(worker OK, attrs pendentes)──► pending_seller_attributes
                                                                                                          └─(attributes PUT)──► pending_description
                                                                                                          └─(batch: pausa — SKU aguarda ação manual)
                                                                                       └─(pending_description)──► [manual: pipeline/generate_images]
                                                                                                                    └─(batch: auto)──► generating_images
                                                                                                                                         └─(sem {sku}-1/-2 em .jpg/.png/.webp no bucket)──► pending_raw_photos ──(beat 15 min ou resume_raw_photos)──► generating_images
                                                                                                                                         └─(OpenAI indisponível: crédito/401/403/5xx/timeout)──► pending_ai_engine ──(beat 15 min ou resume_ai_engine)──► generating_images
                                                                                                                                         └─(worker OK)──► pending_image_approval [manual]
                                                                                                                                                             └─(images/approve)──► generating_description
                                                                                                                                                                      └─(worker OK)──► ready_to_publish [manual — lote também para aqui]
                                                                                                                                                                                  └─(pipeline/publish ou bulk/publish)──► publishing
                                                                                                                                                                                                 └─(worker OK)──► published
Em qualquer estado: falha ──► failed ──(retry)──► generating_title
MLValidationError (400 do ML) ──► failed (sem retry automático)
```

---

## Ordem das imagens do anúncio

**Um único caminho desde 2026-09-10: o esquema de 5 posições, em toda
categoria.** `profile_for_category` nunca devolve None: categoria com perfil
próprio (hoje só **MLB6284** → Perfumaria) usa o dela; qualquer outra usa
`PERFIL_PADRAO` (mesmo canvas 1200×1200, legendas de detalhe neutras). O
caminho antigo — individuais por foto, capa composta de kit, capa
determinística persistida, 3 cards Pillow e a **auto-aprovação em lote** —
foi removido por completo, não deixado dormente. Sem foto bruta no bucket, o
listing vai para `pending_raw_photos` (ver `raw_photo_standby_service`).

| # | `kind` | Origem | Entrada |
|---|---|---|---|
| 0 | `cover_ai` | IA, prompt leve (fundo branco) | capa determinística |
| 1 | `presentation_ai` | IA | **todas** as fotos brutas do SKU |
| 2 | `benefits_ai` | IA, copy do LLM (`card_benefits`) | 1ª foto |
| 3 | `detail_ai` | IA, legenda fixa do perfil | `pick_detail_source()` (3ª foto se existir) |
| 4 | `specs_ai` | IA, bullets do `value_name` real | capa determinística |

**Candidata é posição, não kind.** As candidatas das Frentes A e B nascem em
`sort_order` 90/91 (`CANDIDATE_SORT_ORDER_FLOOR = 90`) com os **mesmos**
kinds `cover_ai`/`specs_ai` das posições 0 e 4 oficiais, então o kind não
distingue nada. `bulk_approve_images` aprova o que tem `sort_order < 90`
**e** `ml_picture_id` preenchido — o mesmo critério que a publicação usa para
montar `pictures` — e deixa 90/91 como estão. A segunda condição existe
porque uma posição reprovada no QA fica sem `ml_picture_id`: aprová-la
colidiria com o fallback no índice único do slot (`uq_listing_images_*_slot`).
Um filtro por kind deixaria a capa e a ficha oficiais de fora e publicaria
3 de 5 imagens.

**A aprovação individual renumera; a em massa não.** `approve_images`
reatribui `sort_order` de forma sequencial, na ordem em que os ids chegam em
`approved_ids` (deduplicados, preservando a ordem): o `sort_order` que a
imagem tinha antes **não** é preservado. A numeração começa em 0 quando a
primeira aprovada tem kind de capa (`PROMOTABLE_COVER_KINDS`); se não tiver,
começa em 1 e a posição 0 fica vaga, porque é **reservada** a capa.
Consequência para a tela de revisão (bloco B): **a ordem em que o frontend
envia os ids é a ordem publicada no ML** — quem montar a galeria precisa
enviar os ids na ordem desejada. `bulk_approve_images` **não** renumera:
mantém o `sort_order` existente. A explicação longa está no comentário do
laço de `approve_images`, em `listing_service.py`.

Canvas vem de `PositionProfile.canvas` — não de constante do worker. Cada
posição é independente, com 2 tentativas; falha em uma não derruba as
outras. A capa determinística é calculada mas **não vira linha visível**: só
é persistida se a posição 0 por IA falhar por completo.

**Todas nascem `approved=False`, em qualquer categoria, também em lote.** O
listing para em `pending_image_approval` e nada é enfileirado depois da
geração: a descrição só nasce da aprovação humana (`images/approve` ou
`bulk/approve`), e a publicação só de `pipeline/publish` ou `bulk/publish`.
Não existe mais nenhum caminho que publique sem revisão humana das imagens.

**Toda aprovação humana grava 1 linha em `listing_review_events`**, na mesma
transação da aprovação: individual (`images/approve`) guarda o
`review_seconds` recebido do operador (pode ser `None`); em massa
(`bulk/approve-images`) grava sempre `review_seconds=NULL` e `mode="bulk"` —
nunca estima nem reparte tempo entre os anúncios do lote.

> **Vertical seria destrutivo aqui.** `normalize_to_square` **recorta o
> centro**, não adiciona borda: um canvas 3:4 perderia o painel de texto das
> posições 1–3 — o texto que justifica a existência delas — e **ainda passaria
> no QA**, porque `validate_image` não exige quadrado. O ML recomenda 1200×1200
> para a maioria das categorias; o 4:5 é recomendação de Moda/Vestuário, e
> entraria como perfil próprio.

Perfil é chaveado pela **categoria-folha, nunca pela raiz**: a raiz de MLB6284
é MLB1246 (Beleza), com 13 filhas — chavear nela aplicaria "Frasco elegante" a
esmalte e álcool em gel. Categoria sem perfil próprio **não herda o da irmã
nem o da raiz**: recebe o genérico.

> **`ProductImage` (índice SKU→imagem) não recebe mais linhas.** Nenhum
> caminho o lê nem o escreve desde a remoção do reuso e do caminho antigo. A
> tabela e o model ficam como registro histórico dos SKUs 37/38 até decisão
> explícita de apagar.

## Endpoints implementados

```
POST   /api/v1/auth/login
POST   /api/v1/auth/refresh
GET    /api/v1/auth/ml/connect
GET    /api/v1/auth/ml/callback
GET    /api/v1/auth/ml/status

POST   /api/v1/products                    criar produto (409 se SKU já existe)
PUT    /api/v1/products/{sku}              atualizar produto (todos os campos)
GET    /api/v1/products                    listar com paginação e busca
GET    /api/v1/products/{sku}              detalhe de produto
POST   /api/v1/products/upload             importar planilha de produtos (XLSX/CSV)

POST   /api/v1/import                      importar planilha de anúncios (batch)
GET    /api/v1/import                      listar imports recentes
GET    /api/v1/import/{id}                 detalhe de import com status por linha

POST   /api/v1/listings                    criar anúncio (status: draft)
GET    /api/v1/listings                    listar com paginação/filtro; `status` repetível (`?status=a&status=b`) e `search` (SKU, título, `sku_description`, marca, `mlb_id`)
GET    /api/v1/listings/status-counts      contagem por status do seller (as 16 canônicas com zeros + eventuais status legados) + total
GET    /api/v1/listings/{id}               detalhe (com títulos, atributos, imagens, jobs)
DELETE /api/v1/listings/{id}               excluir (só draft ou failed)
POST   /api/v1/listings/{id}/pipeline/start
POST   /api/v1/listings/{id}/pipeline/retry
POST   /api/v1/listings/{id}/titles/{tid}/select
PUT    /api/v1/listings/{id}/attributes
POST   /api/v1/listings/{id}/pipeline/generate_images
POST   /api/v1/listings/{id}/images/approve
POST   /api/v1/listings/{id}/pipeline/publish
POST   /api/v1/listings/{id}/activate                    published_paused → published
POST   /api/v1/listings/{id}/pipeline/resume_raw_photos  retomada manual de pending_raw_photos (409 se as fotos ainda faltam)
GET    /api/v1/system/pending-raw-photos                 contagem/lista dos listings do seller em pending_raw_photos
POST   /api/v1/listings/{id}/pipeline/resume_ai_engine   retomada manual de pending_ai_engine (motor OpenAI fora / crédito)
GET    /api/v1/system/pending-ai-engine                  contagem/lista dos listings do seller em pending_ai_engine

POST   /api/v1/listings/bulk/start-pipeline              em massa: draft → generating_title (fila: "Iniciar pipeline")
POST   /api/v1/listings/bulk/approve-titles              em massa: pending_title_approval → predicting_category (fila: "Aprovar títulos")
POST   /api/v1/listings/bulk/reject-titles               em massa: pending_title_approval → generating_title de novo (fila: "Reprovar títulos")
POST   /api/v1/listings/bulk/generate-images             em massa: pending_description → generating_images (fila: "Gerar imagens")
POST   /api/v1/listings/bulk/approve-images              em massa: aprova por posição (sort_order < 90 e ml_picture_id) → generating_description (fila: "Aprovar imagens")
POST   /api/v1/listings/bulk/publish                     em massa: ready_to_publish → publishing (fila: "Publicar", com confirmação)
PUT    /api/v1/listings/bulk/attribute                   preenche um atributo em vários anúncios (grade /listings/attributes)
GET    /api/v1/listings/bulk/attributes                  linhas da grade de atributos em massa

GET    /api/v1/health                                    sem auth: status do banco e do redis
GET    /api/v1/dashboard                                 painel de contas (/): um item por seller com contagem por status
GET    /api/v1/sellers                                   contas ML que o usuário acessa
GET    /api/v1/sellers/image-config                      raw_base_url do seller ativo (bucket público de fotos brutas)
PUT    /api/v1/sellers/image-config                      cria/atualiza a raw_base_url
GET    /api/v1/title-configs                             regras de título por grupo de produto do seller
POST   /api/v1/title-configs                             cria regra
PUT    /api/v1/title-configs/{config_id}                 atualiza regra
DELETE /api/v1/title-configs/{config_id}                 remove regra

POST   /api/v1/listings/{id}/images/cover-ai-variant     candidato cover_ai (sort_order 90)
POST   /api/v1/listings/{id}/images/specs-ai-variant     candidato specs_ai (sort_order 91)
POST   /api/v1/listings/{id}/images/{img}/promote-cover  quem ocupa sort_order 0
POST   /api/v1/listings/{id}/images/{img}/promote-specs  quem ocupa o slot de ficha
```

> **Todo `/listings/bulk/*` devolve `BulkResult`** (`processed`, `failed`, `results[{listing_id, success, error}]`) e recusa item fora do status esperado com `"estado inválido"`, sem derrubar os outros. A fila deriva as ações do status dos selecionados e mostra o motivo por SKU; erro técnico (`SQL`, `Traceback`, `sqlalchemy`, texto longo) nunca chega cru à tela.

> **`promote_specs` NÃO tem posição fixa, `promote_cover` tem.** A capa é 0 por
> invariante do domínio (`COVER_SORT_ORDER`). A ficha não: `card_specs` nasce em
> `start_sort_order + saved`, então sua posição depende de quantas individuais o
> anúncio gerou — 7 no SKU 37, 5 num anúncio com 2 individuais. `promote_specs`
> **lê** o slot da ficha que já está na galeria em vez de impor um; cravar um
> `SPECS_SORT_ORDER` mudaria a numeração de todo anúncio já publicado.

> **`replace_item_pictures` (em `publish_service`) é substituição TOTAL.** O PUT
> de `pictures` no ML não faz merge: mandar 2 IDs num item de 8 fotos deixa o
> anúncio com 2. A função recusa lista vazia, ID repetido e lista que perca
> algum `must_keep`, nomeando o que sumiria. `fetch_item` é GET **autenticado** —
> a chamada pública devolve 403.

---

## Frontend — páginas implementadas

Rodar com `npm run dev` dentro de `frontend/`. Porta: `http://localhost:3000`

| Rota | Página |
|---|---|
| `/` | Painel de contas: um card por seller conectado com contagem por status, botão "Usar" e link para a fila (`app/(dashboard)/page.tsx`) |
| `/listings` | **Fila de trabalho** (substituiu o kanban, removido em `2349bb5`): abre filtrada em "Esperando você", barra de resumo com três blocos (Processando / Esperando você / Concluído) vinda de `status-counts`, busca com atraso de 300 ms, paginação de 50, **sem** atualização automática (botão Atualizar), seleção que sobrevive à paginação e ao filtro, ações em massa derivadas do status dos selecionados (Publicar exige confirmação com a lista de SKUs). Clique na linha vai à etapa que espera ação; o SKU é sempre atalho para o detalhe |
| `/listings/attributes` | Grade de atributos em massa (`AttributeGridEditor`), destino do botão "Preencher atributos" da fila |
| `/products` | Catálogo de produtos (tabela com zebra striping, expansão fiscal, editar por linha) |
| `/products/new` | Novo produto (formulário: identificação / fiscal / embalagem) |
| `/products/[sku]/edit` | Editar produto (mesmo formulário, SKU readonly, staleTime: 0) |
| `/products/upload` | Importar planilha de produtos (+ botão Baixar modelo) |
| `/import` | Importar anúncios batch (+ botão Baixar modelo, histórico de imports) |
| `/listings/new` | Novo anúncio manual |
| `/listings/[id]` | Detalhe adaptativo por status |
| `/listings/[id]/titles` | Seleção de título gerado pela IA |
| `/listings/[id]/attributes` | Form dinâmico de atributos ML |
| `/listings/[id]/images` | Galeria de imagens com aprovação |
| `/listings/[id]/preview` | Preview + botão publicar |
| `/settings` | OAuth ML + lista de contas conectadas |
| `/login` | Login por e-mail e senha |

**UX:**
- Topbar removida; título em cada página; seletor de seller no rodapé da sidebar
- Transição suave entre páginas: `key={pathname}` com `animate-in fade-in duration-200`
- Títulos em PT-BR sentence case

> **Referência do fluxo do operador (bloco B):** `docs/superpowers/specs/frontend-fluxo-operador.md`.
> A fila (`/listings`) foi construída em três tarefas, todas em `master` desde 2026-09-12:
> `e176fb2` (camada de API: `getListings` com `status` repetido e `search`, `getStatusCounts`,
> `STATUS_GROUPS`), `41c9d6d` (tela) e `2349bb5` (seleção e ações em massa; quadro removido).
> Regras que ela consome e **não** reimplementa: `is_candidate` e `is_editable` vêm do backend.
> A marca "Incompleto (n/5)" compara `approved_image_count` com 5 fixo no frontend — pendência
> registrada no spec (o SKU 37 tem 8 aprovadas).

**Arquivos frontend críticos:**
- `src/components/layout/Sidebar.tsx` — sidebar com SellerSelector embutido
- `src/components/listings/WorkQueue.tsx` — a fila: contagens, filtro, busca, tabela, seleção (estado próprio em `Map`), estados de erro e vazio
- `src/components/listings/StatusSummaryBar.tsx` — os três blocos da barra de resumo; o selecionado expande por status; item "Outros" para status legado
- `src/components/listings/BulkActionsBar.tsx` — barra fixa de ações em massa, derivadas do status dos selecionados; seleção mista sem ação; diálogo de confirmação só para Publicar
- `src/components/listings/BulkResultPanel.tsx` — resultado da ação em massa: contagens e, por falha, SKU e motivo saneado
- `src/lib/status-summary.ts` — agrupa `status-counts` nos três blocos, legado vira "Outros", `balanced` confere a soma contra `total`
- `src/lib/bulk-actions.ts` — ação por status, seleção mista, `sanitizeBulkError` (SQL/Traceback/sqlalchemy/texto longo nunca vão para a tela), `summarizeBulkResult`
- `src/lib/listing-destination.ts` — para onde a linha da fila leva, por status
- `src/hooks/useDebouncedValue.ts` — atraso da busca (300 ms), escrito à mão
- `src/components/listings/ListingStatusBadge.tsx` — único lugar que pinta status
- `src/components/products/ProductForm.tsx` — formulário compartilhado criar/editar produto
- `src/lib/api/client.ts` — fetch wrapper (Bearer, redirect 401)
- `src/lib/api/products.ts`, `listings.ts`, `auth.ts`, `sellers.ts`, `import.ts`
- `src/lib/download-template.ts` — ExcelJS: `downloadProductTemplate()`, `downloadListingTemplate()`
- `src/lib/utils.ts` — `formatPrice()`, `formatQuantity()` (usam `Number()` antes de `toLocaleString`)
- `src/types/product.ts`, `types/listing.ts` (`ListingStatus`, `STATUS_LABELS`, `STATUS_GROUP_OF`/`STATUS_GROUPS`, `StatusCounts`, `ListingSummary` com `sku_description`, `ml_category_id`, `approved_image_count`)

> **Não existem mais:** `PipelineBoard.tsx`, `ListingCard.tsx` e a rota `/listings/board` — removidos em `2349bb5`. Não procurar por eles.

> **Nota:** o padrão `lib/` do `.gitignore` é artefato de build Python; desde `5f92130`, `!frontend/src/lib/` reinclui este diretório. Arquivos novos aqui entram com `git add` normal.

---

## Atributos ML pré-preenchidos automaticamente

O `category_service.py` pré-preenche estes atributos a partir dos dados do produto/listing:

| Atributo ML | Fonte | Regra |
|---|---|---|
| `ITEM_CONDITION` | `listing.condition` | "new" → "Novo", "used" → "Usado" |
| `BRAND` | `listing.sku_brand` | Skip se vazio ou "Sem marca" (case-insensitive) |
| `MODEL` | `listing.sku_model` | Skip se vazio; vem de `product.model` |
| `GTIN` | EAN do produto | Só preenche se numérico e len in (8, 12, 13, 14) |
| `SELLER_SKU` | `listing.sku_external_id` | — |
| `SELLER_PACKAGE_WEIGHT` | `package_weight_kg × 1000` | Formato: `"120 g"` (com unidade) |
| `SELLER_PACKAGE_LENGTH/WIDTH/HEIGHT` | dimensões em cm | Formato: `"16 cm"` (com unidade) |

---

## Erros ML resolvidos (commit ecf8978, 2026-06-22)

| Erro ML | Causa | Fix |
|---|---|---|
| GTIN com formato inválido | EAN "NA" ou não-numérico passava `if ean:` | `.isdigit()` + length check |
| Dimensão omitida (unidade inválida) | Enviava `"16"` sem unidade | f-string `f"{val} cm"` |
| Peso omitido (unidade inválida) | Enviava `"120"` sem unidade | `format(val,'f') + " g"` |
| Marca obrigatória não adicionada | "Sem marca" enviado ao ML | Skip se empty ou == "sem marca" |
| Modelo obrigatório não adicionado | Campo inexistente no sistema | Campo `model` adicionado ao catálogo |
| Fotos com menos de 500 pixels | Gemini Imagen fast pode gerar < 500px | `ensure_dimensions()` upscale para 1024px |

---

## Categoria: o que aprendemos com perfumaria

**Não existe categoria "body splash" no ML.** Perfume, deo colônia e body splash
caem todos em **`MLB6284`** (`Beleza e Cuidado Pessoal > Perfumes`), que é
**folha**, filha direta da raiz e a única das 13 filhas de Beleza que não se
subdivide. A distinção de tipo vive no atributo `PERFUME_TYPE`, não na árvore.

> **`domain_discovery` erra feio com termo curto.** Para `"body splash"` ele
> devolve **`MLB269718` Águas Minerais em 1º lugar**, com Perfumes em 2º — e
> `category_service._predict_category` pega `results[0]`. O que salvou o SKU 38
> foi o título trazer marca e "Colônia". **Não há revisão humana de categoria
> hoje** — é o item pendente da Fase 6.

Cuidado com a homônima: **`MLB178938`** também se chama "Perfumes", mas é
`Pet Shop > Cães > … > Perfumes`. É a origem do caso `"Colônia"` — valor válido
lá e inexistente em MLB6284, onde o equivalente é `"Água de colônia"`.

**O ML reescreve `UNIT_VOLUME`:** enviamos `"200 ml"` e ele armazena `"200 mL"`.
Não é erro; só não comparar enviado × publicado nesse campo.

---

## Requisitos para smartphones (categoria MLB1055)

Atributos obrigatórios que o ML valida contra bases externas:
- **GTIN**: EAN-13 numérico da embalagem
- **Nº Anatel**: número de homologação (12 dígitos sem hífens) — validado contra banco Anatel
- **MODEL**: modelo do produto (ex: "Galaxy A54 128GB") — agora vem de `product.model`
- **BRAND**: marca real (não placeholder)

Esses dados devem estar no catálogo de produtos antes do pipeline de batch.

---

## SPECs de referência

| SPEC | Assunto |
|---|---|
| specs/SPEC-000-overview.md | Arquitetura geral e módulos |
| specs/SPEC-001-database.md | Schema do banco de dados |
| specs/SPEC-002-api-contract.md | Contrato da API REST |
| specs/SPEC-003-ml-oauth.md | OAuth com Mercado Livre |
| specs/SPEC-004-ai-service.md | Serviço de IA (títulos e descrições) |
| specs/SPEC-005-category-attributes.md | Predição de categoria + atributos ML |
| specs/SPEC-006-image-pipeline.md | Pipeline de imagens |
| specs/SPEC-007-job-queue.md | Fila de jobs Celery + state machine |
| specs/SPEC-008-frontend.md | Arquitetura do frontend |
| specs/SPEC-009-security.md | Modelo de segurança |
| docs/superpowers/specs/esquema-5-posicoes.md | Esquema de 5 posições (padrão único de imagens) |
| docs/superpowers/specs/2026-09-10-entrega-ao-seller-bucket-proprio-pausada.md | Entrega no bucket do seller: ideia pausada e como retomar |
| docs/superpowers/specs/frontend-fluxo-operador.md | Fluxo do operador: desenho do frontend (bloco B), aprovado em 2026-09-11 — fila de trabalho, revisão de imagens por posição, atributos por `is_editable`, ordem de construção e pendências |
