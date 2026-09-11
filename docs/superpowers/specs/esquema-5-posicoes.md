# Esquema de 5 posições (piloto, branch `feature/descoberta-fotos-brutas`)

Estrutura-alvo, em discussão, para anúncio de produto único (não kit):

1. **Capa** — determinística (Pillow, já em produção) por padrão. Variante
   ambientada por IA disponível sob demanda (Frente A desta branch), nunca
   automática. Usuário escolhe qual promove.
2. **Apresentação** — foto de produto tratada via i2i (equivalente ao que hoje
   chamamos de "individual"), a partir da 1ª ou 2ª foto bruta.
3. **Características/benefícios** — card com IA, composição rica (evolução do
   piloto já aprovado), copy vem da DESCRIÇÃO real do produto.
4. **Detalhes** — close-up/textura do produto, SEM callouts/setas (decisão
   tomada: apontamento preciso via IA não é confiável o suficiente). Fonte: se
   houver mais de 2 fotos brutas disponíveis (ver Passo 0.1 desta branch), usa
   uma foto extra (ex: a 3ª) como fonte, com tratamento i2i similar à posição
   2. Se só houver as 2 mínimas, usa fallback genérico a partir do que existe —
   não inventa detalhe que a foto não mostra.
5. **Ficha técnica** — dado permanece determinístico (Pillow, já em produção:
   `card_specs`). Variante gerada por IA disponível sob demanda para teste A/B
   de custo de curadoria (Frente B desta branch) — nunca substitui a versão
   Pillow automaticamente.

**Callouts/setas:** não existem no código, é conceito descartado nesta mesma
discussão — não implementar.

Este documento descreve uma estrutura **em avaliação**, não o comportamento de
produção atual. O pipeline em produção (`_try_i2i_generation`, capa
determinística + até 4 individuais + até 3 cards) continua sendo a fonte de
verdade até decisão explícita de substituição.

---

## Estado da implementação (atualizado conforme a branch avança)

| Item | Estado |
|---|---|
| Passo 0.1 — descoberta de fotos além do mínimo | ✅ `fetch_raw_photos` sonda até `RAW_PHOTOS_MAX` (10); as 2 primeiras seguem obrigatórias |
| Passo 0.2 — fonte da posição 4 | ✅ `pick_detail_source()`, função pura, sem efeito colateral |
| Posição 1 — variante de capa sob demanda | ✅ disponível sob demanda (Frente A): `POST /api/v1/listings/{id}/images/cover-ai-variant` gera o candidato `cover_ai`; `POST /api/v1/listings/{id}/images/{image_id}/promote-cover` decide qual imagem (`cover_deterministic` ou `cover_ai`) ocupa a capa publicada. Pipeline automático continua produzindo só a capa determinística. |
| Posição 5 — ficha técnica sob demanda | ✅ disponível sob demanda (Frente B): `POST /api/v1/listings/{id}/images/specs-ai-variant` gera o candidato `specs_ai`, renderizado por IA a partir dos bytes salvos da capa determinística, para comparação A/B com o `card_specs` (Pillow) já produzido pelo pipeline. Nunca substitui o `card_specs` automaticamente. |
| Posições 2, 3 | 🔲 não implementadas — o pipeline de produção continua como está |

> **O ✅ da Posição 1 é sobre o endpoint existir, não sobre a imagem prestar.**
> A variante de capa tem dois prompts, escolhidos pela categoria
> (`_pick_prompt` em `cover_variant_service.py`):
>
> - **Prompt leve** (categoria que exige fundo branco): validado visualmente
>   no SKU 37 / MLB6284 — passou no QA e a imagem foi revisada.
> - **Prompt rico** (demais categorias): ⚠️ **NÃO VALIDADO VISUALMENTE.**
>   Herda a linguagem aprovada nos cards do piloto, mas essa linguagem foi
>   aprovada num **card**, não numa **capa**, e nenhuma imagem saída deste
>   prompt foi revisada por ninguém. Ele não roda no SKU 37, porque MLB6284
>   exige fundo branco. Validar depende de um SKU de outra categoria.
>
> Até esse teste acontecer, resultado do prompt rico é candidato a revisar,
> não caminho confiável — e nada aqui deve ser lido como "confirmado
> funcionando".

### Quem pode ocupar a posição 1 (`sort_order=0`)

A posição de capa é **reservada** a `kind` de capa (`cover_deterministic` ou
`cover_ai`). Isso é invariante estrutural, garantido em dois pontos que
precisam concordar:

- `ListingService.approve_images` numera as imagens aprovadas na ordem
  escolhida pelo operador, mas **pula o 0 se a primeira aprovada não for uma
  capa** — a numeração começa em 1 e o 0 fica vago até existir uma capa. O 0
  vago não altera o anúncio: `publish_service` monta o array de fotos
  ordenando por `sort_order`, e o ML usa a **posição no array**, não o número.
- `cover_variant_service.promote_cover` rebaixa apenas linhas de kind de capa
  que estejam em 0, para nunca despublicar uma foto `individual` já aprovada.

**As duas metades se sustentam mutuamente.** Só a segunda (que veio primeiro)
deixava duas linhas empatadas em `sort_order=0` após uma promoção, e a
ordenação da publicação não tem critério de desempate — a capa que ia ao ar
era arbitrária, ou seja, a promoção podia ficar inerte. Afrouxar qualquer um
dos dois lados reabre um dos dois defeitos.

### Limitação conhecida: corrida entre promoções de alvos diferentes

Aceita como está nesta branch de piloto, **não** é bug desconhecido.

`promote_cover` usa `with_for_update()`, que serializa apenas promoções que
disputem as mesmas linhas. Duas promoções **simultâneas de alvos diferentes**
no mesmo anúncio (duas abas escolhendo capas distintas) travam linhas
disjuntas: cada transação lê a lista de rebaixáveis antes da outra escrever,
nenhuma enxerga o alvo da outra, e ambas terminam em `sort_order=0`.

- **Janela:** milissegundos, e exige ação humana concorrente no mesmo anúncio.
- **Autocura:** a promoção seguinte rebaixa a lista inteira e volta ao estado
  correto — o estado corrompido não é absorvente.
- **Correção definitiva:** índice único parcial
  (`UNIQUE (listing_id) WHERE sort_order = 0 AND kind IN (...)`) via migration.
  Não se justifica antes de o piloto ser aprovado.

### Isolamento em relação à produção

`fetch_raw_photos` é **compartilhada** entre o piloto e o pipeline de produção.
Como ela passou a devolver mais fotos, o loop de individuais em
`_try_i2i_generation` passaria a consumir todas — um seller com 5 fotos geraria
**10 individuais em vez de 4**, 2,5× o custo de IA, e um total de 14 imagens
contra o teto de **12** do Mercado Livre.

Por isso aquele loop tem um corte explícito em `[:RAW_PHOTOS_MIN]`. O piloto
enxerga as fotos extras; a produção não. **Se alguém remover esse corte achando
que é redundante, reintroduz a explosão de custo e o estouro do teto.**


---

## Estado final: as 5 posições são o padrão de PRODUTO ÚNICO

Vale para anúncio de produto único em **toda categoria**, desde 2026-09-10:
`profile_for_category` nunca devolve None. Categoria-folha com perfil próprio
cadastrado em `image_position_profiles.py` usa o dela (hoje só `MLB6284`,
Perfumes, perfil `PERFIL_PERFUMARIA`); qualquer outra usa `PERFIL_PADRAO`
(mesmo canvas 1200×1200, legendas de detalhe neutras).

| Posição | `kind` | Origem | Entrada |
|---|---|---|---|
| 0 | `cover_ai` | IA, prompt leve (fundo branco) | capa determinística |
| 1 | `presentation_ai` | IA | **todas** as fotos brutas do SKU |
| 2 | `benefits_ai` | IA, copy do LLM (`card_benefits`) | 1ª foto |
| 3 | `detail_ai` | IA, legenda fixa do perfil | `pick_detail_source()` |
| 4 | `specs_ai` | IA, bullets do `value_name` real | capa determinística |

Canvas **1200×1200 quadrado**, vindo de `PositionProfile.canvas` — não de
constante do worker. É o que permite um perfil futuro (Moda em 4:5) pedir
outro formato sem tocar na orquestração.

> **Vertical seria destrutivo neste pipeline.** `normalize_to_square`
> **recorta o centro**, não adiciona borda. Um canvas 3:4 perderia o painel
> de texto das posições 1–3 — o texto que justifica a existência delas — e
> ainda passaria no QA, porque `validate_image` não exige quadrado. O ML
> recomenda 1200×1200 para Beleza e Cuidado Pessoal; o 4:5 é recomendação de
> Moda/Vestuário.

### Chaveamento por folha, nunca por raiz

A raiz de `MLB6284` é `MLB1246 Beleza e Cuidado Pessoal`, com 13 filhas —
Maquiagem, Manicure, Farmácia, Depilação. Perfil chaveado na raiz aplicaria
"Frasco elegante" a esmalte e álcool em gel. Categoria sem perfil próprio
**recebe o `PERFIL_PADRAO`**, e não herda o da irmã nem o da raiz. Não existe
mais pipeline antigo.

`MLB178938` (`Pet Shop > … > Perfumes`, perfume para cães) **não** está
cadastrada de propósito: vocabulário de atributos próprio, nenhum SKU
testado, e é a origem do caso "Colônia" documentado no `CLAUDE.md`.

### Revisão humana é obrigatória, sem exceção

As 5 nascem `approved=False`, em toda categoria, também em lote. Não existe
varredura de aprovação automática: o listing para em `pending_image_approval`
e só avança quando um humano aciona `approve_images` ou
`bulk_approve_images`. A aprovação em massa aprova as 5 posições (0–4) e
exclui as candidatas das Frentes A e B **pela posição**
(`sort_order >= CANDIDATE_SORT_ORDER_FLOOR`, hoje 90), nunca pelo `kind`:
`cover_ai` e `specs_ai` são também os kinds das posições oficiais 0 e 4, e um
filtro por kind deixaria a capa e a ficha de fora — publicaria 3 de 5 imagens,
sem a capa de fundo branco.

### Resiliência

Cada posição é independente, com 2 tentativas. Falha em uma não derruba as
outras. Reprovada no QA, a linha é gravada com os bytes do que a IA produziu
— candidato existe para alguém julgar.

A capa determinística é calculada mas **não vira linha visível**: serve de
base para as posições 0 e 4, e só é persistida se a posição 0 por IA falhar
por completo.

### Kits: inalterados

`len(skus) > 1` continua exatamente como estava. Hoje é inalcançável —
`resolve_listing_skus` sempre devolve 1 SKU —, mas não foi este trabalho que
mudou isso. O corte `[:RAW_PHOTOS_MIN]` também segue de pé no caminho antigo;
ele **não se aplica** às posições 1–3, que são uma chamada só com acesso a
todas as fotos.

### ⚠️ Fidelidade de texto: mitigação, não garantia

No teste do SKU 38 a posição 0 saiu com `FOR·HER` onde o rótulo original diz
`FOR HER` — um separador inserido pelo modelo, apesar da cláusula CRITICAL.
Passou no QA e só apareceu na ampliação lado a lado. É a razão de a revisão
humana ser obrigatória: o prompt reduz o risco, não o elimina.
