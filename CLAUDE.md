# Publicar AD MLB

Sistema web para automação de criação e publicação de anúncios no Mercado Livre.

## Stack

| Camada | Tecnologia |
|---|---|
| Backend API | Python 3.12 + FastAPI + SQLAlchemy 2.0 (async) |
| Workers | Celery 5 + Redis 7 |
| Banco de dados | PostgreSQL 16 |
| Frontend | Next.js 14 (App Router) + TypeScript + Tailwind + shadcn/ui |
| Storage de imagens | Cloudflare R2 (URL pública funciona; API S3 bloqueada pelo ISP) |
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
| Sprint 2 | ✅ | Batch import: upload de planilha de anúncios → pipeline automático sem aprovação humana |
| SPEC-010 | ✅ | Catálogo de Produtos: tabela `products`, ProductService multi-tenant, CRUD via UI e planilha |
| SPEC-011 | ✅ | Listing upload refatorado: planilha de anúncios só tem campos de publicação; dados do produto vêm do catálogo |
| Quick fixes F-1..F-4 | ✅ | Resiliência do pipeline de imagens: ensure_dimensions seguro, _mark_failed robusto, ImageRateLimitError + backoff 429 |
| SPEC-012 | ✅ | Resiliência estrutural do pipeline de imagens (token refresh, idempotência, Celery chain, lock otimista) |
| Trilha 2 · Fase 3 | ✅ | Cards de benefício: 3 imagens extras por anúncio (benefícios / modo de uso / especificações) montadas com Pillow sobre foto já gerada, texto por LLM, sem motor de IA de imagem |
| Fase 5a | ✅ | Artefatos de produção: `Dockerfile.prod` multi-stage non-root, `docker-compose.prod.yml`, `.dockerignore`, limites de memória. Correção de segurança: `/openapi.json` fechado fora de development |
| Fase 5b | ✅ | Deploy na VPS: vhost + TLS, `.env` de produção gerado do zero, stack no ar, migrations aplicadas. Correção de 2 bugs de OAuth |
| Fase 5c | ✅ | **Primeiro anúncio real publicado**: `MLB5145387291` (SKU 37, Wepink Martin). Validação de `allowed_values`, modo catálogo (`family_name`), cards a partir da capa determinística |
| Frentes A e B | ✅ | Variante de capa e ficha técnica por IA sob demanda: `cover_variant_service`, `specs_variant_service`, `promote_cover`, `promote_specs`, `replace_item_pictures`. Candidato nasce `approved=False` e só vai ao ar por ação humana |
| Ficha ancorada em atributo | ✅ | `build_specs_card` monta os bullets do `value_name` real — o `card_specs` Pillow e a variante de IA param de depender da redação do LLM |
| **Esquema de 5 posições** | ✅ | Padrão de anúncio de **produto único** em categoria-folha com perfil (hoje só MLB6284). Em produção desde 2026-09-01 (`083ad2e`) |
| SKU 38 | ✅ | 2º anúncio real: `MLB7574387170` (Body Splash Fatal Black For Her 200ml). Publicado com 8 fotos e depois trocado para as 5 do esquema novo |
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
- `GEMINI_API_KEY` — usado para Gemini Flash (texto) e Gemini Imagen 4 (imagens)
- `GEMINI_MODEL` — modelo de texto (ex: `gemini-2.0-flash`)
- `ANTHROPIC_API_KEY` (se usar Claude como provider)
- `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET_NAME`, `R2_PUBLIC_URL` — Cloudflare R2 (configurado mas não usado no pipeline de imagens atualmente)

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

> `FREEPIK_API_KEY` não é mais necessário — FreePik foi descartado; imagens geradas pelo Gemini Imagen 4.

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
Nos gatilhos batch (`category_tasks.py`, `listing_service.submit_attributes`), a transição de status e o dispatch são feitos atomicamente:
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
    from celery import chain as celery_chain
    from app.workers.tasks.image_tasks import generate_images
    from app.workers.tasks.ai_tasks import generate_description
    from app.workers.tasks.publish_tasks import publish_listing
    celery_chain(
        generate_images.si(listing_id),
        generate_description.si(listing_id),
        publish_listing.si(listing_id),
    ).delay()
```
- `rowcount == 0` → outro worker ganhou a race condition, não despacha nada
- `.si()` (não `.s()`) — tasks não passam resultado para o próximo step
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

### Provider de IA — 4 métodos abstratos
`AIProvider` (`ai/base.py`) declara `generate_titles`, `generate_description`,
`generate_image_prompt` e `generate_card_copy`. **Provider novo tem que
implementar os 4** — faltar um faz a classe estourar `TypeError` na
instanciação. Os prompts ficam centralizados em `ai/prompts.py` como
`build_*_prompt()`; `gemini.py` e `claude.py` compartilham a mesma assinatura
`_call(prompt, max_tokens, temperature)`, e `claude.py` reusa `_extract_json`
de `gemini.py` em vez de duplicar.

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

**TGFPRO (catálogo de produtos):** sku, descricao, marca, modelo, ean, ncm, origemfiscal, icmscst, icmsrate, piscst, cofinscst, pesokg, comprimentocm, larguracm, alturacm, custo
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
- `listing.py` — Listing (sku_external_id, sku_description, sku_brand, **sku_model**, price, status, ...)
- `listing_title.py` — ListingTitle
- `listing_attribute.py` — ListingAttribute (allowed_values JSONB, is_required, source)
- `listing_image.py` — ListingImage (ml_picture_id, approved, sort_order)
- `listing_description.py` — ListingDescription
- `listing_job.py` — ListingJob
- `product_image.py` — ProductImage (seller_id, sku, ml_picture_id, is_approved) — índice SKU→imagem
- `batch_import.py` — BatchImport + BatchImportRow

### Arquivos de produção (raiz e backend/)
- `docker-compose.prod.yml` — stack de produção; `mem_limit` em todos os serviços, postgres/redis sem `ports:`, backend só em `127.0.0.1:8010`, sem pgadmin nem frontend
- `backend/Dockerfile.prod` — multi-stage (toolchain fica no estágio de build), usuário non-root `appuser` uid 10001, uvicorn com 2 workers e sem `--reload`
- `backend/.dockerignore` — exclui `.env` explicitamente, como rede de segurança caso o build context mude de `./backend` para `.`
- `backend/pytest.ini` — `cache_dir = /tmp/pytest_cache`: `/app` pertence ao root e o processo roda como `appuser`. Dar `chown` em `/app` deixaria o código gravável pelo usuário de runtime, anulando metade do ganho do non-root

### backend/app/assets/fonts/
- `Inter-Regular.ttf` (peso 400), `Inter-Bold.ttf` (peso 700), `OFL.txt` — fonte do renderizador de cards. Versionadas no repo de propósito: a imagem `python:3.12-slim` **não traz nenhuma TTF** e o Pillow só embarca fonte bitmap, então sem isso `ImageFont.truetype()` não tem o que carregar. Licença SIL OFL, uso comercial livre.

### backend/app/services/
- `auth_service.py` — login, refresh token
- `ml_oauth_service.py` — OAuth ML, troca code→token, refresh token ML
- `ai/base.py`, `ai/gemini.py`, `ai/claude.py`, `ai/prompts.py`, `ai/service.py` — providers de IA
- `category_service.py` — CategoryService: prediz categoria ML + salva atributos; pré-preenche BRAND, MODEL, GTIN, SELLER_SKU, dimensões, peso com unidades corretas
- `listing_service.py` — ListingService: CRUD + pipeline
- `image_service.py` — GeminiImageService (Imagen 4 fast) + MLPictureService + `validate_image()` + `ensure_dimensions()` (upscale para 1024px antes do upload) + `ImageRateLimitError` (HTTP 429 → backoff 60s×2^retries)
- `publish_service.py` — PublishService + `get_valid_access_token()` + MLValidationError
- `product_service.py` — ProductService (multi-tenant via `_base_query()`): list, get, create, update, upsert
- `product_import_service.py` — parser CSV/XLSX de produtos (auto-detect delimitador)
- `batch_import_service.py` — parser CSV/XLSX de anúncios (auto-detect delimitador)
- `image_card_copy_service.py` — copy dos cards via LLM: `CARD_KINDS`, `CardCopy`, `generate_card_copy()`. **Nunca levanta exceção** — devolve `[]` em falha e descarta ângulo inutilizável. Sanitiza sem confiar no LLM: trunca título em 40 e bullets em 50 chars, exige 2–3 bullets, e roda uma **denylist de conteúdo proibido pelo ML** (preço, URL, telefone, frete grátis, superlativo) no texto **cru, antes de truncar** — truncar o preço para fora não pode servir de lavagem
> **Base dos cards é a CAPA DETERMINÍSTICA**, com fallback para a 1ª
> individual. A capa é recorte do pixel original, sem IA: o rótulo nela é
> sempre fiel. Ancorar os cards nela troca 3 imagens de risco probabilístico
> por 3 de risco zero — antes, os 3 cards herdavam o rótulo corrompido de uma
> imagem de IA que ninguém tinha verificado.

- `image_benefit_card_service.py` — renderizador Pillow: `render_benefit_card()` → JPEG 1200×1200, `CardRenderError`. Foto em contain-fit na faixa y=0..640, bloco de texto centralizado em y=700..1110 com clamp que impede desenho fora do canvas (descarta o último bullet se não couber)
- `cover_variant_service.py` — Frente A: `generate_cover_variant()` (candidato `cover_ai`), `promote_cover()`, `_load_latest_deterministic_cover()`. `_pick_prompt()` devolve **sempre** o prompt leve — capa branca em toda categoria. `_COVER_PROMPT_RICH` fica no módulo **dormant**, para o toggle por seller no frontend; há teste que falha se alguém apagá-lo
- `specs_variant_service.py` — Frente B: `generate_specs_variant()` (candidato `specs_ai`), `promote_specs()`, `_build_specs_prompt()`. O prompt **não tem título**: a ficha sai só com bullets, e proíbe cabeçalho explicitamente — omitir sem proibir convida o motor a inventar um
- `image_position_profiles.py` — tabela `{categoria_folha: PositionProfile}` do esquema de 5 posições. Carrega canvas **e** conteúdo, então um perfil novo (Moda em 4:5) é uma linha a mais. `detail_caption_for()` deriva a legenda do SKU, não sorteia: regerar não pode trocar a legenda por baixo de uma revisão já feita
- `image_position_prompts.py` — prompts das posições 2, 3 e 4. Cláusula `CRITICAL` **idêntica** nas três, palavra por palavra

> **`build_specs_card` continua devolvendo `title`.** A remoção do cabeçalho
> vale só para o prompt da IA; o card Pillow (`card_specs`) mantém o dele.

### backend/app/api/v1/endpoints/
- `health.py`, `auth.py`, `listings.py`
- `products.py` — CRUD de produtos + upload de planilha
- `import.py` — batch import de anúncios + histórico

### backend/app/workers/tasks/
- `ai_tasks.py` — `generate_title`, `generate_description`
- `category_tasks.py` — `predict_category` (batch: atomic UPDATE + Celery chain se sem attrs pendentes)
- `image_tasks.py` — `generate_images` (`_fetch_upload_token` para refresh automático de token ML; guard de idempotência; **sempre gera**, sem reuso via ProductImage desde 2026-09-10; `ensure_dimensions` antes do upload) + `_append_benefit_cards` (3 cards depois das individuais; só para 1 SKU e só se ao menos 1 individual foi salva; nunca levanta — falha vira log e zero cards)
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

- `test_image_service.py` — `TestEnsureDimensions` (5 casos), `TestGeminiImageService429` (2 casos)
- `test_image_tasks.py` — `TestMarkFailed` (4), `TestGenerateImagesRateLimit` (2), `TestFetchUploadToken` (2), `TestGenerateImagesIdempotency` (2)
- `test_batch_chain.py` — `TestCategoryTaskChainDispatch` (3), `TestSubmitAttributesChainDispatch` (1), `TestRemovedInternalDispatch` (2)
- `test_image_card_copy_service.py` — saneamento da copy, denylist de conteúdo (true positives + **13 casos de falso positivo**: `12V`, `3,5cm`, `500ml`, `12,50 m`, `99,9%`, `5000mAh`…)
- `test_image_benefit_card_service.py` — geometria do card. Os testes de layout aferem a geometria **calculada de forma independente** das constantes, mais um caso pixel-level que garante que nada é desenhado abaixo de y=1112
- `test_image_tasks_i2i.py` — `TestBenefitCardsIntegration`: ordem dos 3 cards, `sort_order` contíguo, falha de 1 card não derruba os outros, nenhum card sem individual salva, kit não gera card
- `test_ml_oauth_state.py` — state do OAuth no Redis (incluindo o cenário "inicia num worker, completa em outro") e destino pós-callback com/sem `FRONTEND_URL`
- `test_cover_variant.py` / `test_cover_promote.py` — Frente A: capa sempre branca, prompt rico dormant, candidato reprovado guarda os bytes, promoção idempotente e autocurável
- `test_specs_variant.py` / `test_specs_promote.py` — Frente B: ficha sem título, promoção lendo o slot da galeria em vez de cravar número
- `test_specs_card_deterministic.py` — bullets do `value_name` real (200 execuções → 1 resultado) e a linguagem visual unificada do prompt
- `test_cinco_posicoes.py` — roteamento por categoria-folha, nenhuma posição nasce aprovada, canvas do perfil, falha de uma não derruba as outras, capa determinística como fallback invisível
- `test_allowed_values_por_tipo.py` — `values` é enumeração só em `value_type == "list"`; EAN do produto chegando ao GTIN
- `test_ml_replace_pictures.py` — substituição TOTAL de fotos: recusa lista vazia, ID repetido e perda de `must_keep`

> Suíte completa: **421 passed**. Os 2 warnings (`coroutine '_generate_images_async'
> was never awaited`) são pré-existentes em `test_image_tasks.py`.

### Migrations aplicadas (ordem cronológica)
- `a7519acf4e00` — schema inicial (8 tabelas)
- `08c6a96e1502` — coluna `allowed_values JSONB` em `listing_attributes`
- *(várias)* — multi-account, batch_import, product_images, products
- `d3aa35ba6d71` — `products.model` + `listings.sku_model` + fix índices
- `c1d5e8b3a207` — `validation_error` em `listing_images`
- `2f769b55c74e` — `image_bytes` + `review_seconds` em `listing_images` (head atual)

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
                                                                                                                                         └─(worker OK)──► pending_image_approval [manual]
                                                                                                                                                             └─(images/approve)──► generating_description
                                                                                                                                         └─(batch: auto-aprova)──► generating_description
                                                                                                                                                                      └─(worker OK)──► ready_to_publish [manual]
                                                                                                                                                                      └─(batch)──► publishing
                                                                                                                                                                                     └─(worker OK)──► published
Em qualquer estado: falha ──► failed ──(retry)──► generating_title
MLValidationError (400 do ML) ──► failed (sem retry automático)
```

---

## Ordem das imagens do anúncio

Há **dois caminhos**, escolhidos por categoria em `_try_i2i_generation`.

### Caminho novo — esquema de 5 posições (produto único, categoria com perfil)

Vale quando `profile_for_category(listing.ml_category_id)` acha um perfil em
`image_position_profiles.py`. Hoje: só **MLB6284**.

| # | `kind` | Origem | Entrada |
|---|---|---|---|
| 0 | `cover_ai` | IA, prompt leve (fundo branco) | capa determinística |
| 1 | `presentation` | IA | **todas** as fotos brutas do SKU |
| 2 | `benefits_ai` | IA, copy do LLM (`card_benefits`) | 1ª foto |
| 3 | `detail_ai` | IA, legenda fixa do perfil | `pick_detail_source()` (3ª foto se existir) |
| 4 | `specs_ai` | IA, bullets do `value_name` real | capa determinística |

Canvas **1200×1200**, vindo de `PositionProfile.canvas` — não de constante do
worker. Cada posição é independente, com 2 tentativas; falha em uma não derruba
as outras. A capa determinística é calculada mas **não vira linha visível**: só
é persistida se a posição 0 por IA falhar por completo.

**Todas nascem `approved=False`, e categoria com perfil nunca auto-aprova nem
em batch.** Sem esse guard o batch aprovaria as posições 1–3 e publicaria um
anúncio **sem capa e sem ficha**, porque essas duas são `CANDIDATE_KINDS` e
ficariam de fora da varredura.

> **Vertical seria destrutivo aqui.** `normalize_to_square` **recorta o
> centro**, não adiciona borda: um canvas 3:4 perderia o painel de texto das
> posições 1–3 — o texto que justifica a existência delas — e **ainda passaria
> no QA**, porque `validate_image` não exige quadrado. O ML recomenda 1200×1200
> para Beleza e Cuidado Pessoal; o 4:5 é recomendação de Moda/Vestuário.

Perfil é chaveado pela **categoria-folha, nunca pela raiz**: a raiz de MLB6284
é MLB1246 (Beleza), com 13 filhas — chavear nela aplicaria "Frasco elegante" a
esmalte e álcool em gel. Categoria sem perfil **cai no caminho antigo** e não
herda o da irmã nem o da raiz.

### Caminho antigo — demais categorias, e kits

| # | `kind` | Origem |
|---|---|---|
| 0 | `cover_deterministic` | recorte por distância de cor, sem custo de IA — só quando a foto bruta tem fundo uniforme |
| 1..n | `individual` | edição i2i da foto bruta do seller (2 variantes por foto, limitado a `[:RAW_PHOTOS_MIN]`) |
| n+1 | `card_benefits` | Pillow + copy LLM |
| n+2 | `card_usage` | Pillow + copy LLM |
| n+3 | `card_specs` | Pillow + bullets determinísticos do atributo |

Se a capa determinística falhar, tudo desloca uma posição para trás e a 1ª
individual assume o `sort_order` 0. **Cards nunca ocupam a posição 0** — o ML
não aceita texto/infográfico na capa, só da 2ª imagem em diante.

O ramo de **kit** (`len(skus) > 1`) segue inalterado e é hoje **inalcançável**:
`resolve_listing_skus` sempre devolve 1 SKU.

> **Não existe mais reuso de imagem entre anúncios do mesmo SKU** (removido em
> 2026-09-10). Antes, `_generate_images_async` copiava os `ml_picture_id` de
> `ProductImage.is_approved` para o listing novo, aprovava tudo e, em lote,
> pulava direto para `generating_description` — sem as 5 posições, sem cards
> e sem o guard de revisão. Hoje todo listing **sempre gera** as suas imagens.
> `ProductImage` continua sendo escrito (registro SKU→imagem), mas nenhum
> caminho o lê para pular geração; `tests/test_sem_reuso_de_imagem.py` trava
> isso. Cards continuam sem linha em `ProductImage`, porque a copy é derivada
> do `selected_title` e dos atributos *daquele* anúncio.

---

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
GET    /api/v1/listings                    listar com paginação/filtro
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

POST   /api/v1/listings/{id}/images/cover-ai-variant     candidato cover_ai (sort_order 90)
POST   /api/v1/listings/{id}/images/specs-ai-variant     candidato specs_ai (sort_order 91)
POST   /api/v1/listings/{id}/images/{img}/promote-cover  quem ocupa sort_order 0
POST   /api/v1/listings/{id}/images/{img}/promote-specs  quem ocupa o slot de ficha
```

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
| `/` | Anúncios (kanban, polling 8s, botão Novo anúncio) |
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

**UX:**
- Topbar removida; título em cada página; seletor de seller no rodapé da sidebar
- Transição suave entre páginas: `key={pathname}` com `animate-in fade-in duration-200`
- Títulos em PT-BR sentence case

**Arquivos frontend críticos:**
- `src/components/layout/Sidebar.tsx` — sidebar com SellerSelector embutido
- `src/components/products/ProductForm.tsx` — formulário compartilhado criar/editar produto
- `src/lib/api/client.ts` — fetch wrapper (Bearer, redirect 401)
- `src/lib/api/products.ts`, `listings.ts`, `auth.ts`, `sellers.ts`, `import.ts`
- `src/lib/download-template.ts` — ExcelJS: `downloadProductTemplate()`, `downloadListingTemplate()`
- `src/lib/utils.ts` — `formatPrice()`, `formatQuantity()` (usam `Number()` antes de `toLocaleString`)
- `src/types/product.ts`, `types/listing.ts`

> **Atenção:** `frontend/src/lib/` é capturado pelo padrão `lib/` no `.gitignore` (artefato Python). Novos arquivos neste diretório precisam de `git add -f`.

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
