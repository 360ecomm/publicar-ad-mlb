# Editar atributos antes de publicar

**Branch:** `feat/editar-atributos` (de `master` `de3e497`) · **Data:** 2026-09-15
**Status:** aprovado no Passo 0 pelo Daniel · **Não fazer merge nem deploy.**

Segunda das quatro correções que bloqueiam publicação honesta.

---

## O problema

`submit_attributes` (`listing_service.py:286`) recusa com 409 qualquer status
que não seja `pending_seller_attributes`. Depois de enviar uma vez, não há
volta: o anúncio segue para descrição, imagens e publicação com o que foi
gravado.

Caso real: o SKU 91 tem `FLAVOR = "Chocolate"` (a tela antiga forçou escolher
errado) e `MODEL = "Lichia"` (erro no catálogo). Os dois estão gravados e um
deles está impresso na ficha técnica da imagem já gerada.

---

## O que o Passo 0 apurou

### `submit_attributes` faz duas coisas coladas

1. Guarda de status
2. Grava valores com `_validar_valor`, marcando `source="seller"`
3. Consulta se existe imagem aprovada **e** descrição
4. Decide `ready_to_publish` ou `pending_description` e grava o status
5. Em lote: `UPDATE` atômico para `generating_images` + `generate_images.delay`

Só o passo 2 é "gravar". Os passos 3–5 são "avançar".

### Já existe um segundo caminho de escrita, e ele é pior

`bulk_fill_attribute` (`listing_service.py:868`, usado pela grade
`/listings/attributes`) grava com `UPDATE` direto — **sem `_validar_valor`**,
sem `source="seller"`, aceitando `pending_seller_attributes` **e**
`pending_description`. A regra "só no status de espera" já está furada hoje, e
o furo é justamente o caminho que não valida.

### Onde um atributo aparece dentro de uma imagem

| Posição | Kind | O que consome de atributo |
|---|---|---|
| 1 | `presentation_ai` | **`UNIT_VOLUME`**, impresso (`build_presentation_prompt(nome, marca, volume)`) |
| 2 | `benefits_ai` | **todos** os atributos com valor, via `_build_source` → prompt do LLM |
| 4 | `specs_ai` | até 3 bullets `"{attribute_name}: {value_name}"`, literais |

`nome` e `marca` da posição 1 vêm de `listing.sku_model` e `listing.sku_brand`
— **colunas do listing, não dos atributos `MODEL`/`BRAND`**. Corrigir o
atributo `MODEL` muda a ficha (posição 4) e **não** muda a apresentação
(posição 1). Duplicação registrada como pendência, fora do escopo.

### Regra da ficha (`build_specs_card`, `image_card_copy_service.py:205`)

1. Descarta sem `value_name` e os 9 de `SPECS_EXCLUDED_ATTRIBUTE_IDS`
2. Ordena `BRAND`, `MODEL`, resto alfabético por `attribute_id`
3. `"{attribute_name}: {value_name}"`, descarta (não trunca) acima de
   `MAX_BULLET_CHARS` (50) e o que bater na denylist do ML
4. Descarta valor repetido (`casefold`)
5. Para em `MAX_BULLETS` (3); abaixo de `MIN_BULLETS` (2) devolve `None`

Função pura e determinística — é o que permite detectar divergência sem
heurística.

### O que mais um atributo editado afeta

| Artefato | Afeta? | Efeito |
|---|---|---|
| Publicação no ML | ✅ e é bom | `publish_tasks` lê os atributos **no momento de publicar** |
| Título | ❌ | `_generate_title_async` não lê `ListingAttribute` |
| Categoria | ❌ | Predição é anterior e não consulta atributos |
| Descrição | ⚠️ sim | `_generate_description_async` monta o prompt com todos os atributos com valor; o HTML é gravado **uma vez** |

`generate_description.delay` só é chamado em `approve_images` e
`bulk_approve_images`. **Não existe endpoint que regere a descrição.** Hoje a
única saída é reaprovar as imagens — que grava um `listing_review_events`
afirmando revisão humana de imagens que ninguém olhou, corrompendo o dado
criado justamente para provar revisão. É o motivo de o endpoint entrar nesta
tarefa.

---

## Decisões (Daniel, Passo 0)

| # | Decisão |
|---|---|
| D1 | Endpoint separado `PATCH`, não `PUT` com comportamento por status |
| D2 | Status editáveis: a lista do Passo 0 (os 5 perigosos ficam de fora) |
| D3 | Opção A para a ficha: **avisar**, nunca regerar sozinho |
| D4 | **Liberar** regeneração de posição em `ready_to_publish` |
| D5 | Posição 2: limitar a checagem aos atributos que sobreviveriam ao filtro da ficha |
| D6 | Endpoint de regerar descrição **entra** nesta tarefa |
| D7 | Histórico antes/depois em **log estruturado**, sem tabela |
| D8 | `bulk_fill_attribute` corrigido junto, em **commit separado** |
| D9 | Estender `recusar_se_regeneracao_em_andamento` ao `PATCH` |
| D10 | `approved_count` **reinterpretado** como "itens da ação"; ação `attributes_edited` |

---

## Desenho

### 1. Status editáveis

Constante nova em `app/models/listing.py`, ao lado de `LISTING_STATUSES` (mesmo
princípio de `POSITION_KINDS` em `listing_image.py`: vocabulário mora no model
porque mais de um call site precisa concordar com ele).

```python
EDITABLE_ATTRIBUTE_STATUSES: frozenset[str] = frozenset({
    "draft", "pending_title_approval", "pending_seller_attributes",
    "pending_description", "pending_raw_photos", "pending_ai_engine",
    "pending_image_approval", "ready_to_publish", "failed",
})
```

**Recusados com 409, um por um, e por quê:**

| Status | Motivo |
|---|---|
| `generating_title` | Atributos ainda não existem — nascem em `predicting_category` |
| `predicting_category` | `_save_attributes` faz `DELETE` de todos (`category_service.py:177`) e reinsere. A edição **sumiria em silêncio**: o operador salva, vê "salvo", e o valor some |
| `generating_images` | O worker lê os atributos uma vez por posição. Galeria mista: posição 1 com o volume velho, posição 4 com a ficha nova |
| `generating_description` | `_generate_description_async` lê os atributos para o prompt. Mesma corrida |
| `publishing` | `publish_tasks` lê os atributos para o payload. A edição iria ao ar **sem revisão nenhuma**, ou derrubaria a publicação com 422 do ML no momento mais caro |
| `published` / `published_paused` | Pendência futura (editar anúncio no ar) |

### 2. `PATCH /api/v1/listings/{id}/attributes`

Corpo: `AttributesSubmitRequest` (reuso, sem mudança).
Resposta: `AttributesEditResponse` — `listing: ListingSummary`,
`stale_positions: list[int]`, `duplicated_fields: list[str]`.

Campos de schema em inglês, como todo o resto de `app/schemas/`
(`validation_error`, `is_candidate`, `approved_count`). O `{posicao}` em PT na
rota de regeneração é o outlier, não o padrão.

Ordem de execução em `ListingService.edit_attributes(listing, submitted, *, user_id)`:

1. 409 se `listing.status not in EDITABLE_ATTRIBUTE_STATUSES`
2. `recusar_se_regeneracao_em_andamento(listing)` → 409 (D9)
3. Carrega **todos** os atributos do listing numa consulta
4. Fotografa o estado: `ficha_antes = build_specs_card(attrs)`, valor de
   `UNIT_VOLUME`, e o conjunto `{attribute_id: value_name}` dos atributos que
   passam o **filtro de candidatos da ficha** (tem `value_name`, fora de
   `SPECS_EXCLUDED_ATTRIBUTE_IDS`) — a base da posição 2 (D5)
5. Resolve **todos** os pares `(value_id, value_name)` com `_validar_valor`
   **antes de qualquer escrita** (422 para valor fora da lista em tipo `list`)
6. Simula o estado resultante: se algum `is_required` ficaria sem
   `value_name`, **422 antes de qualquer escrita**, nomeando os atributos
7. Se nenhum valor mudaria de fato: **422**, sem evento, sem escrita
8. Aplica os valores, marcando `source="seller"`
9. Recalcula e compara → `stale_positions`
10. Grava 1 `ListingReviewEvent` na **mesma transação**
11. Log estruturado, uma linha por atributo alterado + uma de resumo
12. `commit`. **Não** toca `listing.status`. **Não** enfileira task. **Não**
    apaga imagem nem descrição.

Os passos 5–7 antes de qualquer escrita seguem o precedente de `approve_images`
(`test_recusa_aprovacao_vazia.py`): recusa que não escreve nada.

O passo 7 mantém a invariante de `approved_count >= 1` do model de evento —
edição que não muda nada não é edição, do mesmo jeito que aprovação que não
aprova nada não é aprovação.

### 3. Detecção de posição desatualizada

Tudo determinístico, custo zero, nenhuma chamada paga, dentro da mesma
transação:

| Posição | Desatualizada quando |
|---|---|
| 1 | o `value_name` de `UNIT_VOLUME` mudou |
| 2 | mudou o `value_name` de **algum atributo que passa o filtro de candidatos da ficha** (D5) |
| 4 | `build_specs_card(antes).bullets != build_specs_card(depois).bullets` |

A posição 2 usa o filtro de candidatos, não os 3 bullets finais: a copy do LLM
lê todos os atributos com valor, mas alarme que dispara com
`EMPTY_GTIN_REASON` não é lido. O filtro da ficha é o recorte "atributo que
descreve o produto na vitrine", que é exatamente o que a copy usa.

Só entram posições que **existem** em `listing_images` — anúncio sem imagem
gerada não recebe aviso.

### 4. `duplicated_fields`

Se a edição tocou `MODEL` ou `BRAND`, devolver o(s) id(s). A tela avisa que a
apresentação (posição 1) lê `listing.sku_model` / `listing.sku_brand`, colunas
do catálogo, e **não** muda com esta correção. Pendência registrada, não
corrigida.

### 5. Regeneração de posição em `ready_to_publish` (D4)

**Obstáculo descoberto na investigação:** em `ready_to_publish` as 5 posições
estão `approved=True`, e `regenerate_position` recusa posição aprovada com 409
("imagem aprovada não é regenerada"). Só relaxar a guarda de status entregaria
um endpoint que 409 em 100% dos casos — ou seja, nada.

**Resolução:** em `ready_to_publish`, regenerar uma posição aprovada
**desaprova aquela posição e devolve o anúncio a `pending_image_approval`**, na
mesma transação do placeholder.

- A invariante "publica só imagem aprovada" continua intacta — é o ponto.
- Não é substituição "por trás do operador": é o clique dele.
- Devolver a `pending_image_approval` não é avançar etapa; é desfazer a
  aprovação que a regeneração invalidou.
- A reaprovação subsequente dispara `generate_description`, que é desejável
  depois de uma edição de atributo — e o `listing_review_events` gravado ali é
  **verdadeiro**: um humano reviu as imagens de novo.
- Falha da regeneração não perde dado: a linha antiga sobrevive
  (`approved=False`) e o operador reaprova.

Em `pending_image_approval` nada muda: posição aprovada continua recusada com
409, status continua intocado.

### 6. `POST /api/v1/listings/{id}/pipeline/regenerate_description` (D6)

Só em `ready_to_publish`. Em `pending_image_approval` não há descrição ainda —
ela nasce da aprovação. Em `failed` existe `pipeline/retry`.

Reusa a task `generate_description` que já existe, com o padrão de dispatch
atômico do projeto:

```python
UPDATE listings SET status='generating_description'
 WHERE id=:id AND status='ready_to_publish'
```

`rowcount == 1` → `generate_description.delay`. `rowcount == 0` → 409. Broker
fora ao enfileirar → devolve o status para `ready_to_publish` e 503, como
`regenerate_position` já faz com o placeholder.

O status muda temporariamente — e deve: o anúncio **está** gerando descrição. A
restrição "editar não avança etapa" vale para a edição de atributos, não para
esta ação explícita.

**Não grava `ListingReviewEvent`.** É o ponto todo do D6.

### 7. Evento de auditoria (D10)

Reinterpretar `approved_count` como "quantidade de itens da ação", reescrevendo
o comentário do model. O precedente existe: o docstring atual já diz que o
número conta coisas diferentes por `mode` e que "os dois números não são
comparáveis entre si".

- `action = "attributes_edited"` (constante nova
  `REVIEW_ACTION_ATTRIBUTES_EDITED` em `listing_review_event.py`)
- `mode = REVIEW_MODE_INDIVIDUAL`
- `approved_count` = número de atributos efetivamente alterados (≥ 1, pelo
  passo 7)
- `review_seconds = None` — não há cronômetro nesta tela

### 8. Log estruturado (D7)

Sem tabela. Formato seguindo a convenção do projeto (`specs_card
attribute_id=%s result=dropped`), em `logger.info`:

```
attribute_edit listing_id=%s sku=%s user_id=%s attribute_id=%s de=%r para=%r
attribute_edit listing_id=%s sku=%s user_id=%s alterados=%d stale_positions=%s
```

`%r` para não perder aspas de string vazia ou `None`.

### 9. `bulk_fill_attribute` (D8, commit separado)

Ganha **só validação**: passa a resolver o valor por `_validar_valor` e a
marcar `source="seller"`. Valor inválido em tipo `list` vira item falho do
`BulkResult` (não 422 global — derrubaria o lote inteiro por causa de uma
linha), com a mensagem de valores aceitos.

**Mantém** o avanço de status para `pending_description` quando todos os
obrigatórios ficam preenchidos: é o gêmeo em lote de `submit_attributes`
(preenchimento), não da edição. **Não** grava evento, pelo mesmo motivo —
`submit_attributes` também não grava.

### 10. Frontend

- `/listings/[id]/attributes` passa a receber `mode: "submit" | "edit"`,
  derivado do status do anúncio.
- Detalhe do anúncio: link "Corrigir atributos" em qualquer status editável
  (hoje o link só aparece em `pending_seller_attributes`).
- Botão em modo edição: **"Salvar correção"**, com a linha de apoio "não
  avança a etapa do anúncio".
- Depois de salvar em modo edição: painel com as posições desatualizadas
  ("Esta correção mudou texto impresso nas imagens 1 e 4") e, se veio
  `duplicated_fields`, o aviso da duplicação `MODEL`/`BRAND`.
- `ready_to_publish` ganha, no detalhe, o botão "Regerar descrição".

**Fora do escopo:** o botão de regenerar posição. A tela de revisão de imagens
por posição (bloco B) não existe ainda, e é ela que deve oferecê-lo. O painel
de aviso leva para `/listings/[id]/images`.

---

## Testes obrigatórios

Além do óbvio, exigidos pelo Daniel:

- edição em cada status **recusado**, um por um, com 409 (6 casos)
- edição **não** avança status, **não** dispara task, **não** apaga imagem
  aprovada nem descrição
- obrigatório esvaziado → 422, sem escrita
- ficha muda → posição 4 marcada
- `UNIT_VOLUME` muda → posição 1 marcada
- atributo irrelevante muda → **nenhuma** posição marcada (prova o filtro da
  posição 2)
- regerar descrição **não** grava evento de revisão de imagem
- evento `attributes_edited` com autor, modo e contagem
- `bulk_fill_attribute`: valor inválido em tipo `list` → recusado

Testes com Postgres real só rodam com `TEST_DATABASE_URL` (banco `publicar_test`,
exclusivo). `conftest.py` bloqueia rede real na suíte inteira.

---

## Pendências registradas, não corrigidas

1. **`MODEL`/`sku_model` e `BRAND`/`sku_brand` em duplicata.** Os atributos
   alimentam a ficha (posição 4); as colunas do listing alimentam a
   apresentação (posição 1). Corrigir um não corrige o outro. Erro invisível.
2. **Botão de regenerar posição** depende da tela de revisão (bloco B).
3. **Placeholder `generating` preso** por worker morto continua sem saída
   self-service — agora também bloqueia o `PATCH`.
