# Editar atributos antes de publicar — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Permitir corrigir um atributo já gravado, em qualquer status seguro antes de `published`, sem avançar a etapa do anúncio, avisando quais imagens ficaram desatualizadas.

**Architecture:** Um método novo de service (`edit_attributes`) separa *gravar* de *avançar*, que hoje estão colados em `submit_attributes`. A detecção de imagem desatualizada é uma função pura sobre `build_specs_card`, sem heurística e sem chamada paga. Dois endpoints de apoio fecham as saídas que a edição abre: regeneração de posição em `ready_to_publish` e regeneração de descrição sem evento de revisão falso.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.0 async, pytest, Next.js 14 + TypeScript.

**Spec:** `docs/superpowers/specs/2026-09-15-editar-atributos-antes-de-publicar.md` — leia antes de começar.

## Global Constraints

- **Branch `feat/editar-atributos`. NUNCA fazer merge, push para `master`, nem deploy.**
- Editar **não** avança o anúncio de etapa, **não** dispara geração, **não** apaga imagem aprovada nem descrição existente.
- Toda recusa acontece **antes de qualquer escrita** (precedente: `approve_images`, `tests/test_recusa_aprovacao_vazia.py`).
- Validação continua sendo `ListingService._validar_valor`: **422** para valor fora da lista quando `attribute_type == "list"`; texto livre passa quando o tipo é `string`.
- Erros da API sempre `{"detail": "mensagem"}` com o status HTTP correto.
- Comentários e docstrings em português, **sem acentos** dentro de docstrings de código Python (convenção do arquivo `listing_service.py`); mensagens ao usuário **com** acentos.
- Campos de schema/JSON em **inglês** (`stale_positions`, `duplicated_fields`), como todo `app/schemas/`.
- Commits em Conventional Commits, em português, terminando com:
  ```
  Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01GhtK315be95Kthy77kty97
  ```
- Rodar a suíte de dentro de `backend/`: `python -m pytest -q`. Os testes com Postgres real só rodam com `TEST_DATABASE_URL` apontando para `publicar_test`; **sem** a variável eles são `skipped`, e isso é sucesso.
- `conftest.py` bloqueia rede real na suíte inteira (`NetworkAccessAttempted`). Nunca marcar `allow_network`.
- Frontend: checar com `cd frontend && npx tsc --noEmit`. **Não** instalar dependências novas.
- Não criar migration: **nenhuma tabela ou coluna nova nesta branch.**

---

## Ordem e dependências

| Task | Depende de | Arquivos principais |
|---|---|---|
| 1 | — | `app/models/listing.py`, `app/models/listing_review_event.py` |
| 2 | — | `app/services/image_card_copy_service.py`, `app/services/attribute_impact.py` |
| 3 | 1, 2 | `app/services/listing_service.py` |
| 4 | 3 | `app/schemas/listing.py`, `app/api/v1/endpoints/listings.py` |
| 5 | 1 | `app/services/listing_service.py`, `app/api/v1/endpoints/listings.py` |
| 6 | — | `app/services/listing_service.py`, `app/api/v1/endpoints/listings.py` |
| 7 | — | `app/services/listing_service.py` |
| 8 | 4, 6 | `frontend/src/**` |

---

### Task 1: Vocabulário de status editável e da ação de auditoria

**Files:**
- Modify: `backend/app/models/listing.py` (depois de `LISTING_STATUSES`, linha ~35)
- Modify: `backend/app/models/listing_review_event.py` (constantes no topo + docstring de `approved_count`)
- Test: `backend/tests/test_editar_atributos_vocabulario.py` (criar)

**Interfaces:**
- Consumes: nada.
- Produces:
  - `app.models.listing.EDITABLE_ATTRIBUTE_STATUSES: frozenset[str]`
  - `app.models.listing.REGENERABLE_POSITION_STATUSES: frozenset[str]`
  - `app.models.listing_review_event.REVIEW_ACTION_ATTRIBUTES_EDITED: str = "attributes_edited"`

- [ ] **Step 1: Escrever o teste que falha**

Criar `backend/tests/test_editar_atributos_vocabulario.py`:

```python
"""Vocabulario da edicao de atributos: quais status aceitam correcao e qual
a acao de auditoria. Sem banco (sempre roda)."""


class TestStatusEditaveis:
    def test_todos_existem_em_listing_statuses(self):
        from app.models.listing import EDITABLE_ATTRIBUTE_STATUSES, LISTING_STATUSES

        assert EDITABLE_ATTRIBUTE_STATUSES <= set(LISTING_STATUSES)

    def test_os_sete_perigosos_ficam_de_fora(self):
        """Nenhum destes pode entrar sem uma decisao nova: em cinco deles um
        worker esta lendo os atributos neste exato momento, e em
        `predicting_category` a edicao seria APAGADA em silencio pelo
        `_save_attributes`."""
        from app.models.listing import EDITABLE_ATTRIBUTE_STATUSES

        proibidos = {
            "generating_title",
            "predicting_category",
            "generating_images",
            "generating_description",
            "publishing",
            "published",
            "published_paused",
        }
        assert EDITABLE_ATTRIBUTE_STATUSES & proibidos == set()

    def test_cobre_os_nove_status_de_espera(self):
        from app.models.listing import EDITABLE_ATTRIBUTE_STATUSES

        assert EDITABLE_ATTRIBUTE_STATUSES == {
            "draft",
            "pending_title_approval",
            "pending_seller_attributes",
            "pending_description",
            "pending_raw_photos",
            "pending_ai_engine",
            "pending_image_approval",
            "ready_to_publish",
            "failed",
        }

    def test_particao_exata_de_listing_statuses(self):
        """Editaveis + proibidos = TODOS os status. Status novo obriga uma
        decisao explicita: o teste quebra ate alguem classifica-lo."""
        from app.models.listing import EDITABLE_ATTRIBUTE_STATUSES, LISTING_STATUSES

        proibidos = {
            "generating_title", "predicting_category", "generating_images",
            "generating_description", "publishing", "published",
            "published_paused",
        }
        assert EDITABLE_ATTRIBUTE_STATUSES | proibidos == set(LISTING_STATUSES)


class TestStatusDeRegeneracao:
    def test_regeneracao_aceita_os_dois_status(self):
        from app.models.listing import REGENERABLE_POSITION_STATUSES

        assert REGENERABLE_POSITION_STATUSES == {
            "pending_image_approval",
            "ready_to_publish",
        }

    def test_ambos_sao_editaveis(self):
        """Quem pode regenerar imagem tem de poder corrigir o atributo que
        gerou o texto dela — o contrario e' um beco sem saida."""
        from app.models.listing import (
            EDITABLE_ATTRIBUTE_STATUSES,
            REGENERABLE_POSITION_STATUSES,
        )

        assert REGENERABLE_POSITION_STATUSES <= EDITABLE_ATTRIBUTE_STATUSES


class TestAcaoDeAuditoria:
    def test_valor_da_acao(self):
        from app.models.listing_review_event import REVIEW_ACTION_ATTRIBUTES_EDITED

        assert REVIEW_ACTION_ATTRIBUTES_EDITED == "attributes_edited"

    def test_nao_colide_com_a_aprovacao_de_imagens(self):
        from app.models.listing_review_event import (
            REVIEW_ACTION_ATTRIBUTES_EDITED,
            REVIEW_ACTION_IMAGES_APPROVED,
        )

        assert REVIEW_ACTION_ATTRIBUTES_EDITED != REVIEW_ACTION_IMAGES_APPROVED

    def test_cabe_na_coluna(self):
        """`action` e' String(40)."""
        from app.models.listing_review_event import REVIEW_ACTION_ATTRIBUTES_EDITED

        assert len(REVIEW_ACTION_ATTRIBUTES_EDITED) <= 40
```

- [ ] **Step 2: Rodar o teste e confirmar que falha**

Run: `cd backend && python -m pytest tests/test_editar_atributos_vocabulario.py -q`
Expected: FAIL com `ImportError: cannot import name 'EDITABLE_ATTRIBUTE_STATUSES'`

- [ ] **Step 3: Adicionar as constantes de status**

Em `backend/app/models/listing.py`, logo depois do fechamento de `LISTING_STATUSES` e **antes** de `class Listing`:

```python
# Status em que um atributo JA GRAVADO pode ser corrigido
# (`ListingService.edit_attributes`). Mora aqui, ao lado de
# `LISTING_STATUSES`, pelo mesmo motivo de `POSITION_KINDS` em
# `listing_image.py`: mais de um call site precisa concordar com o MESMO
# conjunto.
#
# Os sete de fora nao sao esquecimento — em cinco deles um worker esta lendo
# os atributos neste exato momento:
#   generating_title       atributos ainda nao existem (nascem em
#                          `predicting_category`)
#   predicting_category    `_save_attributes` faz DELETE de TODOS e reinsere:
#                          a edicao sumiria EM SILENCIO. O operador salva, ve
#                          "salvo", e o valor some.
#   generating_images      o worker le os atributos uma vez POR POSICAO —
#                          editar no meio da galeria mista: posicao 1 com o
#                          volume velho, posicao 4 com a ficha nova
#   generating_description `_generate_description_async` le os atributos para
#                          montar o prompt
#   publishing             `publish_tasks` le os atributos para o payload do
#                          ML: a edicao iria ao ar sem revisao nenhuma, ou
#                          derrubaria a publicacao com 422 no momento mais caro
#   published /            editar anuncio NO AR e' pendencia futura, com
#   published_paused       regras proprias (o ML tem API de update)
EDITABLE_ATTRIBUTE_STATUSES: frozenset[str] = frozenset({
    "draft",
    "pending_title_approval",
    "pending_seller_attributes",
    "pending_description",
    "pending_raw_photos",
    "pending_ai_engine",
    "pending_image_approval",
    "ready_to_publish",
    "failed",
})

# Status que aceitam regenerar UMA posicao de imagem. `ready_to_publish`
# entrou porque e' onde o operador mais descobre o erro — na revisao final.
# Ver `ListingService.regenerate_position` para o que muda em cada um.
REGENERABLE_POSITION_STATUSES: frozenset[str] = frozenset({
    "pending_image_approval",
    "ready_to_publish",
})
```

- [ ] **Step 4: Adicionar a ação de auditoria e reescrever o comentário de `approved_count`**

Em `backend/app/models/listing_review_event.py`, na seção de vocabulário do topo, acrescentar depois de `REVIEW_ACTION_IMAGES_APPROVED`:

```python
REVIEW_ACTION_ATTRIBUTES_EDITED = "attributes_edited"
```

No docstring da classe, **substituir inteiros** os dois parágrafos que hoje começam em "`approved_count` conta coisas diferentes conforme o `mode`" e "`approved_count` e' sempre >= 1" por:

```
    `approved_count` conta os ITENS DA ACAO, e o que e' um item depende de
    `action` e de `mode`. Os numeros nao sao comparaveis entre si:

      images_approved / individual  contagem de todo id que o operador mandou
                                    e que pertence ao listing (o laco de
                                    `approve_images`) — pode incluir uma linha
                                    reprovada em QA ou uma candidata escolhida
                                    de proposito
      images_approved / bulk        `rowcount` do UPDATE em massa, restrito a
                                    `sort_order < CANDIDATE_SORT_ORDER_FLOOR
                                    AND ml_picture_id IS NOT NULL`
      attributes_edited / individual  quantos atributos MUDARAM de valor de
                                    fato em `edit_attributes` (reenviar o
                                    mesmo valor nao conta)

    `approved_count` e' sempre >= 1, em toda combinacao. Acao que nao faz
    nada nao e' acao: `approve_images` recusa com 422 quando nenhum id
    pertence ao listing, `bulk_approve_images` falha o item
    ("nenhuma imagem aprovavel") quando o UPDATE nao atinge nenhuma linha, e
    `edit_attributes` recusa com 422 quando nenhum valor mudaria. Nos tres
    casos nao ha evento, o status nao muda e nenhuma task e' disparada.
```

- [ ] **Step 5: Rodar o teste e confirmar que passa**

Run: `cd backend && python -m pytest tests/test_editar_atributos_vocabulario.py -q`
Expected: PASS (9 passed)

- [ ] **Step 6: Rodar a suíte inteira**

Run: `cd backend && python -m pytest -q`
Expected: nenhuma falha nova.

- [ ] **Step 7: Commit**

```bash
git add backend/app/models/listing.py backend/app/models/listing_review_event.py backend/tests/test_editar_atributos_vocabulario.py
git commit -m "feat(atributos): vocabulario de status editavel e da acao attributes_edited"
```

---

### Task 2: Detecção determinística de posição de imagem desatualizada

**Files:**
- Modify: `backend/app/services/image_card_copy_service.py` (extrair `specs_candidate_attributes`)
- Create: `backend/app/services/attribute_impact.py`
- Test: `backend/tests/test_impacto_edicao_atributo.py` (criar)

**Interfaces:**
- Consumes: `build_specs_card`, `SPECS_EXCLUDED_ATTRIBUTE_IDS` (já existem).
- Produces:
  - `app.services.image_card_copy_service.specs_candidate_attributes(attributes: list | None) -> list`
  - `app.services.attribute_impact.AttributeSnapshot` (frozen dataclass: `volume: str | None`, `ficha: tuple[str, ...]`, `vitrine: tuple[tuple[str, str], ...]`)
  - `app.services.attribute_impact.snapshot_attributes(attributes) -> AttributeSnapshot`
  - `app.services.attribute_impact.stale_positions(antes, depois, existentes) -> list[int]`

- [ ] **Step 1: Escrever o teste que falha**

Criar `backend/tests/test_impacto_edicao_atributo.py`:

```python
"""Deteccao de posicao de imagem desatualizada depois de uma edicao de
atributo. Determinístico, sem banco e sem chamada paga (sempre roda)."""
from types import SimpleNamespace


def _attr(attribute_id, value_name, attribute_name=None):
    return SimpleNamespace(
        attribute_id=attribute_id,
        attribute_name=attribute_name or attribute_id.title(),
        value_name=value_name,
    )


# Tres atributos de vitrine: e' o minimo para `build_specs_card` devolver
# ficha (MIN_BULLETS = 2) e ainda sobrar um fora dos 3 bullets.
def _base():
    return [
        _attr("BRAND", "Wepink", "Marca"),
        _attr("MODEL", "Fatal Black", "Modelo"),
        _attr("UNIT_VOLUME", "200 ml", "Volume"),
        _attr("FLAVOR", "Chocolate", "Sabor"),
        _attr("SELLER_SKU", "91", "SKU"),
        _attr("SELLER_PACKAGE_WEIGHT", "120 g", "Peso"),
    ]


TODAS = {0, 1, 2, 3, 4}


class TestSnapshot:
    def test_volume_sai_do_unit_volume(self):
        from app.services.attribute_impact import snapshot_attributes

        assert snapshot_attributes(_base()).volume == "200 ml"

    def test_sem_unit_volume_o_volume_e_none(self):
        from app.services.attribute_impact import snapshot_attributes

        attrs = [a for a in _base() if a.attribute_id != "UNIT_VOLUME"]
        assert snapshot_attributes(attrs).volume is None

    def test_vitrine_ignora_os_excluidos_da_ficha(self):
        """`SELLER_SKU` e `SELLER_PACKAGE_WEIGHT` estao em
        SPECS_EXCLUDED_ATTRIBUTE_IDS: nao descrevem o produto na vitrine."""
        from app.services.attribute_impact import snapshot_attributes

        ids = {aid for aid, _ in snapshot_attributes(_base()).vitrine}
        assert "SELLER_SKU" not in ids and "SELLER_PACKAGE_WEIGHT" not in ids
        assert ids == {"BRAND", "MODEL", "UNIT_VOLUME", "FLAVOR"}

    def test_vitrine_ignora_atributo_sem_valor(self):
        from app.services.attribute_impact import snapshot_attributes

        attrs = _base() + [_attr("COLOR", None, "Cor")]
        ids = {aid for aid, _ in snapshot_attributes(attrs).vitrine}
        assert "COLOR" not in ids

    def test_snapshot_e_imune_a_mutacao_posterior(self):
        """A foto do estado ANTES nao pode mudar quando o service muta os
        objetos ORM no lugar — e' exatamente o que `edit_attributes` faz."""
        from app.services.attribute_impact import snapshot_attributes

        attrs = _base()
        antes = snapshot_attributes(attrs)
        attrs[3].value_name = "Lichia"
        assert antes.vitrine == snapshot_attributes(_base()).vitrine


class TestPosicoesDesatualizadas:
    def _stale(self, mutar, existentes=TODAS):
        from app.services.attribute_impact import snapshot_attributes, stale_positions

        antes_attrs = _base()
        antes = snapshot_attributes(antes_attrs)
        depois_attrs = _base()
        mutar(depois_attrs)
        return stale_positions(antes, snapshot_attributes(depois_attrs), existentes)

    def test_sem_mudanca_nenhuma_posicao(self):
        assert self._stale(lambda a: None) == []

    def test_ficha_muda_marca_a_posicao_4(self):
        """`MODEL` e' prioridade 2 na ficha: mudar o valor muda um bullet."""
        def mutar(attrs):
            attrs[1].value_name = "Fatal Red"

        assert 4 in self._stale(mutar)

    def test_unit_volume_muda_marca_a_posicao_1(self):
        def mutar(attrs):
            attrs[2].value_name = "100 ml"

        assert 1 in self._stale(mutar)

    def test_atributo_irrelevante_nao_marca_nada(self):
        """Peso da embalagem nao entra na ficha nem na copy. E' o teste que
        prova o filtro da posicao 2: sem ele, QUALQUER edicao marcaria a 2."""
        def mutar(attrs):
            attrs[5].value_name = "500 g"

        assert self._stale(mutar) == []

    def test_sku_interno_nao_marca_nada(self):
        def mutar(attrs):
            attrs[4].value_name = "999"

        assert self._stale(mutar) == []

    def test_vitrine_muda_sem_mexer_na_ficha_marca_so_a_2(self):
        """`FLAVOR` fica fora dos 3 bullets (BRAND, MODEL, FLAVOR vem antes de
        UNIT_VOLUME na ordem alfabetica... mas o corte em MAX_BULLETS deixa
        algum de fora). Seja qual for o que sobra, a copy do LLM le todos:
        a posicao 2 tem de ser marcada mesmo quando a ficha nao muda."""
        from app.services.attribute_impact import snapshot_attributes, stale_positions

        antes_attrs = _base()
        antes = snapshot_attributes(antes_attrs)
        depois_attrs = _base()
        depois_attrs[3].value_name = "Lichia"
        depois = snapshot_attributes(depois_attrs)
        stale = stale_positions(antes, depois, TODAS)
        assert 2 in stale
        assert 1 not in stale

    def test_so_posicoes_existentes_entram(self):
        """Anuncio sem imagem gerada nao recebe aviso de imagem."""
        def mutar(attrs):
            attrs[2].value_name = "100 ml"

        assert self._stale(mutar, existentes=set()) == []
        assert self._stale(mutar, existentes={4}) == []

    def test_resultado_e_ordenado_e_sem_repeticao(self):
        def mutar(attrs):
            attrs[1].value_name = "Fatal Red"
            attrs[2].value_name = "100 ml"

        stale = self._stale(mutar)
        assert stale == sorted(set(stale))


class TestFiltroCompartilhadoComAFicha:
    def test_build_specs_card_usa_o_mesmo_filtro(self):
        """Uma definicao so: se `specs_candidate_attributes` mudar, a ficha
        muda junto. Duas copias do filtro divergiriam em silencio."""
        from app.services.image_card_copy_service import (
            build_specs_card,
            specs_candidate_attributes,
        )

        attrs = _base()
        candidatos = specs_candidate_attributes(attrs)
        ficha = build_specs_card(attrs)
        assert ficha is not None
        nomes = {c.attribute_name for c in candidatos}
        for bullet in ficha.bullets:
            assert bullet.split(":")[0] in nomes
```

- [ ] **Step 2: Rodar o teste e confirmar que falha**

Run: `cd backend && python -m pytest tests/test_impacto_edicao_atributo.py -q`
Expected: FAIL com `ModuleNotFoundError: No module named 'app.services.attribute_impact'`

- [ ] **Step 3: Extrair o filtro de candidatos da ficha**

Em `backend/app/services/image_card_copy_service.py`, **acima** de `build_specs_card`:

```python
def specs_candidate_attributes(attributes: list | None) -> list:
    """Atributos que DESCREVEM O PRODUTO na vitrine.

    Unica definicao do recorte. A ficha (`build_specs_card`) monta os bullets
    a partir daqui, e `attribute_impact` usa o mesmo conjunto para decidir se
    a copy da posicao 2 ficou desatualizada — a copy do LLM le os atributos
    do produto, nao o SKU interno nem o peso da caixa. Duas copias do filtro
    divergiriam em silencio.
    """
    return [
        a for a in (attributes or [])
        if getattr(a, "value_name", None)
        and getattr(a, "attribute_id", None) not in SPECS_EXCLUDED_ATTRIBUTE_IDS
    ]
```

Dentro de `build_specs_card`, **substituir** o bloco:

```python
    candidatos = [
        a for a in (attributes or [])
        if getattr(a, "value_name", None)
        and getattr(a, "attribute_id", None) not in SPECS_EXCLUDED_ATTRIBUTE_IDS
    ]
```

por:

```python
    candidatos = specs_candidate_attributes(attributes)
```

- [ ] **Step 4: Criar `attribute_impact.py`**

Criar `backend/app/services/attribute_impact.py`:

```python
"""Qual posicao do esquema de 5 ficou desatualizada depois de uma edicao de
atributo.

Editar um atributo ja gravado deixa banco e IMAGEM divergentes, e a imagem e'
o que o comprador ve. Tres das cinco posicoes imprimem atributo:

    1  presentation_ai  `UNIT_VOLUME`, impresso no painel
    2  benefits_ai      copy do LLM, redigida a partir dos atributos
    4  specs_ai         bullets `"{attribute_name}: {value_name}"`, literais

A deteccao e' DETERMINISTICA e de graca: `build_specs_card` ja e' uma funcao
pura, entao comparar a ficha antes e depois responde pela posicao 4 com
exatidao, sem heuristica e sem gastar chamada paga. Nada aqui regenera coisa
alguma — quem decide e' o operador (opcao A do spec).

A posicao 2 usa `specs_candidate_attributes`, nao a lista inteira de
atributos: a copy do LLM de fato le todos, mas um alarme que dispara quando
`EMPTY_GTIN_REASON` muda nao e' lido por ninguem. O recorte da ficha e'
"atributo que descreve o produto na vitrine", que e' o mesmo material que a
copy usa de verdade.

Spec: docs/superpowers/specs/2026-09-15-editar-atributos-antes-de-publicar.md
"""
from dataclasses import dataclass
from typing import Iterable

from app.services.image_card_copy_service import (
    build_specs_card,
    specs_candidate_attributes,
)

# O volume e' o UNICO atributo impresso na posicao 1. `nome` e `marca` dessa
# posicao vem de `listing.sku_model` / `listing.sku_brand`, COLUNAS do
# listing — corrigir o atributo MODEL nao muda a apresentacao. Duplicacao
# conhecida, registrada como pendencia no spec.
VOLUME_ATTRIBUTE_ID = "UNIT_VOLUME"

POSICAO_APRESENTACAO = 1
POSICAO_BENEFICIOS = 2
POSICAO_FICHA = 4


@dataclass(frozen=True)
class AttributeSnapshot:
    """Foto do que as imagens consomem dos atributos, num instante.

    So tipos imutaveis: o service muta os objetos ORM NO LUGAR, entao guardar
    referencia para eles faria a foto do "antes" mudar junto com o "depois".
    """

    volume: str | None
    ficha: tuple[str, ...]
    vitrine: tuple[tuple[str, str], ...]


def snapshot_attributes(attributes: Iterable | None) -> AttributeSnapshot:
    attrs = list(attributes or [])
    ficha = build_specs_card(attrs)
    volume = next(
        (
            a.value_name
            for a in attrs
            if getattr(a, "attribute_id", None) == VOLUME_ATTRIBUTE_ID
            and getattr(a, "value_name", None)
        ),
        None,
    )
    vitrine = tuple(sorted(
        (a.attribute_id, str(a.value_name)) for a in specs_candidate_attributes(attrs)
    ))
    return AttributeSnapshot(
        volume=volume,
        # Ficha ausente (`None`, menos de MIN_BULLETS) vira tupla vazia: o que
        # importa e' se MUDOU, e "sumiu" e' uma mudanca tao real quanto
        # "trocou de texto".
        ficha=tuple(ficha.bullets) if ficha is not None else (),
        vitrine=vitrine,
    )


def stale_positions(
    antes: AttributeSnapshot,
    depois: AttributeSnapshot,
    existentes: Iterable[int],
) -> list[int]:
    """Posicoes cujo texto impresso nao corresponde mais ao banco.

    `existentes` sao as posicoes que TEM linha em `listing_images`: anuncio
    sem imagem gerada nao recebe aviso sobre imagem.
    """
    marcadas: set[int] = set()
    if antes.volume != depois.volume:
        marcadas.add(POSICAO_APRESENTACAO)
    if antes.vitrine != depois.vitrine:
        marcadas.add(POSICAO_BENEFICIOS)
    if antes.ficha != depois.ficha:
        marcadas.add(POSICAO_FICHA)
    return sorted(marcadas & set(existentes))
```

- [ ] **Step 5: Rodar o teste e confirmar que passa**

Run: `cd backend && python -m pytest tests/test_impacto_edicao_atributo.py -q`
Expected: PASS (16 passed)

Se `test_vitrine_muda_sem_mexer_na_ficha_marca_so_a_2` falhar porque `FLAVOR` acabou **dentro** dos 3 bullets (a ordem é `BRAND`, `MODEL`, depois alfabética: `FLAVOR` vem antes de `UNIT_VOLUME`), troque no teste o atributo mutado por um que caia fora do corte — acrescente `_attr("COLOR", "Preto", "Cor")` ao `_base()` e mute `UNIT_VOLUME`... **não**: isso marcaria a posição 1. Em vez disso, acrescente ao `_base()` um quinto atributo de vitrine `_attr("VOLUME_CAPACITY", "200 ml", "Capacidade")` e mute **ele** — vem depois de `UNIT_VOLUME` na ordem alfabética, logo fora dos 3 bullets. Ajuste o `assert` de `test_vitrine_ignora_os_excluidos_da_ficha` para incluir o id novo.

- [ ] **Step 6: Rodar os testes da ficha para garantir que a extração não mudou comportamento**

Run: `cd backend && python -m pytest tests/test_specs_card_deterministic.py tests/test_image_card_copy_service.py -q`
Expected: PASS, mesmo número de testes de antes.

- [ ] **Step 7: Rodar a suíte inteira**

Run: `cd backend && python -m pytest -q`
Expected: nenhuma falha nova.

- [ ] **Step 8: Commit**

```bash
git add backend/app/services/attribute_impact.py backend/app/services/image_card_copy_service.py backend/tests/test_impacto_edicao_atributo.py
git commit -m "feat(atributos): deteccao deterministica de imagem desatualizada por edicao"
```

---

### Task 3: `edit_attributes` no service

**Files:**
- Modify: `backend/app/services/listing_service.py`
- Test: `backend/tests/test_editar_atributos_service.py` (criar)

**Interfaces:**
- Consumes: `EDITABLE_ATTRIBUTE_STATUSES` (Task 1), `snapshot_attributes` / `stale_positions` (Task 2), `_validar_valor` e `recusar_se_regeneracao_em_andamento` (já existem).
- Produces: `ListingService.edit_attributes(listing, submitted: list[dict], *, user_id: UUID) -> tuple[list[int], list[str]]` — devolve `(stale_positions, duplicated_fields)`.

- [ ] **Step 1: Escrever o teste que falha**

Criar `backend/tests/test_editar_atributos_service.py`:

```python
"""Correcao de atributo ja gravado — guardas e semantica do service.
Sem banco (sempre roda). O comportamento com linhas reais esta em
`test_editar_atributos_pg.py`."""
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException


def _listing(status="ready_to_publish"):
    listing = MagicMock()
    listing.id = uuid.uuid4()
    listing.seller_id = uuid.uuid4()
    listing.status = status
    listing.sku_external_id = "91"
    return listing


def _attr(attribute_id, value_name, *, tipo="string", required=False, allowed=None):
    return SimpleNamespace(
        attribute_id=attribute_id,
        attribute_name=attribute_id.title(),
        value_id=None,
        value_name=value_name,
        attribute_type=tipo,
        is_required=required,
        allowed_values=allowed,
        source="ai",
    )


def _db(atributos, *, generating=(), posicoes=(0, 1, 2, 3, 4)):
    """`db.execute` responde, em ordem: placeholders `generating`, atributos,
    posicoes existentes."""
    db = AsyncMock()
    respostas = []

    def _scalars(valores):
        r = MagicMock()
        r.scalars.return_value.all.return_value = list(valores)
        return r

    respostas.append(_scalars(generating))
    respostas.append(_scalars(atributos))
    respostas.append(_scalars(posicoes))
    db.execute = AsyncMock(side_effect=respostas)
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    return db


class TestGuardaDeStatus:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", [
        "generating_title",
        "predicting_category",
        "generating_images",
        "generating_description",
        "publishing",
        "published",
        "published_paused",
    ])
    async def test_recusa_status_perigoso_com_409_antes_de_consultar(self, status):
        from app.services.listing_service import ListingService

        db = _db([])
        with pytest.raises(HTTPException) as exc:
            await ListingService(db).edit_attributes(
                _listing(status), [{"attribute_id": "FLAVOR", "value_name": "Lichia"}],
                user_id=uuid.uuid4(),
            )
        assert exc.value.status_code == 409
        assert status in exc.value.detail
        db.execute.assert_not_awaited()
        db.add.assert_not_called()
        db.commit.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", [
        "draft", "pending_title_approval", "pending_seller_attributes",
        "pending_description", "pending_raw_photos", "pending_ai_engine",
        "pending_image_approval", "ready_to_publish", "failed",
    ])
    async def test_aceita_todo_status_editavel(self, status):
        from app.services.listing_service import ListingService

        attrs = [_attr("FLAVOR", "Chocolate")]
        db = _db(attrs)
        await ListingService(db).edit_attributes(
            _listing(status), [{"attribute_id": "FLAVOR", "value_name": "Lichia"}],
            user_id=uuid.uuid4(),
        )
        assert attrs[0].value_name == "Lichia"


class TestRegeneracaoEmAndamento:
    @pytest.mark.asyncio
    async def test_recusa_com_409_enquanto_ha_placeholder(self):
        from app.services.listing_service import ListingService

        db = _db([_attr("FLAVOR", "Chocolate")], generating=[2])
        with pytest.raises(HTTPException) as exc:
            await ListingService(db).edit_attributes(
                _listing(), [{"attribute_id": "FLAVOR", "value_name": "Lichia"}],
                user_id=uuid.uuid4(),
            )
        assert exc.value.status_code == 409
        assert "aguarde a conclusão antes de aprovar" in exc.value.detail
        db.add.assert_not_called()
        db.commit.assert_not_awaited()


class TestRecusaAntesDeEscrever:
    @pytest.mark.asyncio
    async def test_valor_fora_da_lista_em_tipo_list_recusa_com_422_sem_escrever(self):
        from app.services.listing_service import ListingService

        alvo = _attr("FLAVOR", "Chocolate", tipo="list",
                     allowed=[{"id": "1", "name": "Chocolate"}, {"id": "2", "name": "Baunilha"}])
        outro = _attr("COLOR", "Preto")
        db = _db([alvo, outro])
        with pytest.raises(HTTPException) as exc:
            await ListingService(db).edit_attributes(
                _listing(),
                [
                    {"attribute_id": "COLOR", "value_name": "Branco"},
                    {"attribute_id": "FLAVOR", "value_name": "Lichia"},
                ],
                user_id=uuid.uuid4(),
            )
        assert exc.value.status_code == 422
        # NENHUM dos dois foi escrito, nem o valido que vinha antes na lista.
        assert alvo.value_name == "Chocolate"
        assert outro.value_name == "Preto"
        db.add.assert_not_called()
        db.commit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_obrigatorio_esvaziado_recusa_com_422_sem_escrever(self):
        from app.services.listing_service import ListingService

        alvo = _attr("BRAND", "Wepink", required=True)
        db = _db([alvo])
        with pytest.raises(HTTPException) as exc:
            await ListingService(db).edit_attributes(
                _listing(), [{"attribute_id": "BRAND", "value_name": "   "}],
                user_id=uuid.uuid4(),
            )
        assert exc.value.status_code == 422
        assert "Brand" in exc.value.detail
        assert alvo.value_name == "Wepink"
        db.add.assert_not_called()
        db.commit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_opcional_pode_ser_esvaziado(self):
        from app.services.listing_service import ListingService

        alvo = _attr("FLAVOR", "Chocolate")
        db = _db([alvo])
        await ListingService(db).edit_attributes(
            _listing(), [{"attribute_id": "FLAVOR", "value_name": ""}],
            user_id=uuid.uuid4(),
        )
        assert alvo.value_name is None and alvo.value_id is None

    @pytest.mark.asyncio
    async def test_edicao_que_nao_muda_nada_recusa_com_422(self):
        """Mesma regra de `approve_images`: acao que nao faz nada nao e' acao,
        e o evento de auditoria exige `approved_count >= 1`."""
        from app.services.listing_service import ListingService

        db = _db([_attr("FLAVOR", "Chocolate")])
        with pytest.raises(HTTPException) as exc:
            await ListingService(db).edit_attributes(
                _listing(), [{"attribute_id": "FLAVOR", "value_name": "Chocolate"}],
                user_id=uuid.uuid4(),
            )
        assert exc.value.status_code == 422
        db.add.assert_not_called()
        db.commit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_atributo_de_outro_anuncio_e_ignorado(self):
        from app.services.listing_service import ListingService

        alvo = _attr("FLAVOR", "Chocolate")
        db = _db([alvo])
        await ListingService(db).edit_attributes(
            _listing(),
            [
                {"attribute_id": "NAO_EXISTE", "value_name": "x"},
                {"attribute_id": "FLAVOR", "value_name": "Lichia"},
            ],
            user_id=uuid.uuid4(),
        )
        assert alvo.value_name == "Lichia"


class TestNaoAvancaNemDispara:
    @pytest.mark.asyncio
    async def test_status_intocado_e_nenhuma_task_enfileirada(self):
        from app.services.listing_service import ListingService

        listing = _listing("ready_to_publish")
        db = _db([_attr("FLAVOR", "Chocolate")])
        with patch("app.workers.tasks.image_tasks.generate_images") as gi, \
             patch("app.workers.tasks.ai_tasks.generate_description") as gd, \
             patch("app.workers.tasks.publish_tasks.publish_listing") as pl:
            await ListingService(db).edit_attributes(
                listing, [{"attribute_id": "FLAVOR", "value_name": "Lichia"}],
                user_id=uuid.uuid4(),
            )
        assert listing.status == "ready_to_publish"
        gi.delay.assert_not_called()
        gd.delay.assert_not_called()
        pl.delay.assert_not_called()

    @pytest.mark.asyncio
    async def test_nao_apaga_imagem_nem_descricao(self):
        """Nenhum DELETE e nenhum UPDATE em massa: a unica escrita e' nos
        objetos de atributo carregados, mais o INSERT do evento."""
        from app.services.listing_service import ListingService

        db = _db([_attr("FLAVOR", "Chocolate")])
        await ListingService(db).edit_attributes(
            _listing(), [{"attribute_id": "FLAVOR", "value_name": "Lichia"}],
            user_id=uuid.uuid4(),
        )
        sql = " ".join(str(c.args[0]) for c in db.execute.await_args_list).lower()
        assert "delete" not in sql
        assert "update" not in sql


class TestEvento:
    @pytest.mark.asyncio
    async def test_grava_um_evento_attributes_edited_com_autor_e_contagem(self):
        from app.models.listing_review_event import (
            REVIEW_ACTION_ATTRIBUTES_EDITED,
            REVIEW_MODE_INDIVIDUAL,
        )
        from app.services.listing_service import ListingService

        user_id = uuid.uuid4()
        listing = _listing()
        db = _db([_attr("FLAVOR", "Chocolate"), _attr("COLOR", "Preto")])
        await ListingService(db).edit_attributes(
            listing,
            [
                {"attribute_id": "FLAVOR", "value_name": "Lichia"},
                {"attribute_id": "COLOR", "value_name": "Branco"},
            ],
            user_id=user_id,
        )
        assert db.add.call_count == 1
        evento = db.add.call_args.args[0]
        assert evento.listing_id == listing.id
        assert evento.user_id == user_id
        assert evento.action == REVIEW_ACTION_ATTRIBUTES_EDITED
        assert evento.mode == REVIEW_MODE_INDIVIDUAL
        assert evento.approved_count == 2
        assert evento.review_seconds is None

    @pytest.mark.asyncio
    async def test_contagem_ignora_valor_reenviado_igual(self):
        from app.services.listing_service import ListingService

        db = _db([_attr("FLAVOR", "Chocolate"), _attr("COLOR", "Preto")])
        await ListingService(db).edit_attributes(
            _listing(),
            [
                {"attribute_id": "FLAVOR", "value_name": "Lichia"},
                {"attribute_id": "COLOR", "value_name": "Preto"},
            ],
            user_id=uuid.uuid4(),
        )
        assert db.add.call_args.args[0].approved_count == 1


class TestRetorno:
    @pytest.mark.asyncio
    async def test_marca_a_posicao_1_quando_o_volume_muda(self):
        from app.services.listing_service import ListingService

        db = _db([_attr("UNIT_VOLUME", "200 ml"), _attr("BRAND", "Wepink"),
                  _attr("MODEL", "Fatal Black")])
        stale, _dup = await ListingService(db).edit_attributes(
            _listing(), [{"attribute_id": "UNIT_VOLUME", "value_name": "100 ml"}],
            user_id=uuid.uuid4(),
        )
        assert 1 in stale

    @pytest.mark.asyncio
    async def test_duplicated_fields_avisa_model_e_brand(self):
        """MODEL/BRAND existem em duplicata: o atributo alimenta a ficha, a
        COLUNA do listing alimenta a apresentacao. Corrigir um nao corrige o
        outro — a tela precisa dizer isso."""
        from app.services.listing_service import ListingService

        db = _db([_attr("MODEL", "Lichia"), _attr("BRAND", "Wepink"),
                  _attr("FLAVOR", "Chocolate")])
        _stale, dup = await ListingService(db).edit_attributes(
            _listing(),
            [
                {"attribute_id": "MODEL", "value_name": "Fatal Black"},
                {"attribute_id": "FLAVOR", "value_name": "Lichia"},
            ],
            user_id=uuid.uuid4(),
        )
        assert dup == ["MODEL"]
```

- [ ] **Step 2: Rodar o teste e confirmar que falha**

Run: `cd backend && python -m pytest tests/test_editar_atributos_service.py -q`
Expected: FAIL com `AttributeError: 'ListingService' object has no attribute 'edit_attributes'`

- [ ] **Step 3: Adicionar os imports e a constante de duplicação**

Em `backend/app/services/listing_service.py`, na linha do import do model de listing, trocar:

```python
from app.models.listing import LISTING_STATUSES, Listing
```

por:

```python
from app.models.listing import (
    EDITABLE_ATTRIBUTE_STATUSES,
    LISTING_STATUSES,
    REGENERABLE_POSITION_STATUSES,
    Listing,
)
```

No import de `listing_review_event`, acrescentar `REVIEW_ACTION_ATTRIBUTES_EDITED` à lista de nomes.

Logo depois de `_mensagem_regeneracao_em_andamento`, acrescentar:

```python
# Atributos do ML que existem EM DUPLICATA no sistema: o atributo alimenta a
# ficha tecnica (posicao 4) e a coluna homonima do listing
# (`sku_model`/`sku_brand`, vinda do catalogo) alimenta a apresentacao
# (posicao 1). Corrigir um NAO corrige o outro, e nada no codigo os
# sincroniza. Enquanto a duplicacao existir, a tela precisa avisar.
DUPLICATED_CATALOG_ATTRIBUTE_IDS = frozenset({"BRAND", "MODEL"})
```

- [ ] **Step 4: Implementar `edit_attributes`**

Em `backend/app/services/listing_service.py`, **logo depois** de `submit_attributes` (antes de `trigger_image_generation`):

```python
    @staticmethod
    def _valor_limpo(item: dict) -> dict:
        """Campo apagado pelo operador vira APAGAR, nao string vazia.

        Sem isso, `_validar_valor` trataria `""` como um valor a procurar na
        enumeracao e devolveria um 422 incompreensivel ("Valor '' nao e'
        valido") quando a intencao era limpar o campo.
        """
        nome = item.get("value_name")
        if nome is None or not str(nome).strip():
            return {"attribute_id": item["attribute_id"], "value_id": None, "value_name": None}
        return item

    async def edit_attributes(
        self,
        listing: Listing,
        submitted: list[dict],
        *,
        user_id: UUID,
    ) -> tuple[list[int], list[str]]:
        """Corrige atributo JA gravado e para por ai.

        Nao toca `listing.status`, nao enfileira task, nao apaga imagem
        aprovada nem descricao. `submit_attributes` continua sendo o passo de
        PREENCHIMENTO — ele tolera obrigatorio vazio (e' assim que o anuncio
        entra em `pending_seller_attributes`) e decide a etapa seguinte. Este
        e' o de CORRECAO, e recusa obrigatorio vazio. Sao duas semanticas
        opostas no mesmo dado; por isso dois metodos, e nao um `if` por
        status dentro de um so.

        Tudo que recusa, recusa ANTES de qualquer escrita — mesmo principio
        de `approve_images`.

        Devolve `(stale_positions, duplicated_fields)`: as posicoes de imagem
        cujo texto impresso nao corresponde mais ao banco, e os atributos
        editados que existem em duplicata no catalogo. Avisa; nao regenera
        nada (opcao A do spec) — regenerar por conta propria gastaria chamada
        paga sem decisao humana.

        Spec: docs/superpowers/specs/2026-09-15-editar-atributos-antes-de-publicar.md
        """
        if listing.status not in EDITABLE_ATTRIBUTE_STATUSES:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Correção de atributos indisponível no status '{listing.status}'",
            )
        # Mesma trava das aprovacoes e das promocoes: o worker da regeneracao
        # vai reler os atributos para montar a posicao, entao editar agora
        # decide por sorteio qual versao do texto entra na imagem.
        await self.recusar_se_regeneracao_em_andamento(listing)

        atributos = (await self.db.execute(
            select(ListingAttribute).where(ListingAttribute.listing_id == listing.id)
        )).scalars().all()
        por_id = {a.attribute_id: a for a in atributos}

        antes = snapshot_attributes(atributos)

        # 1) Resolve TUDO antes de escrever: um 422 no terceiro item nao pode
        #    deixar os dois primeiros gravados.
        resolvidos = []
        for item in submitted:
            attr = por_id.get(item.get("attribute_id"))
            if attr is None:
                continue  # atributo de outra categoria: ignorado, como no PUT
            value_id, value_name = self._validar_valor(attr, self._valor_limpo(item))
            resolvidos.append((attr, value_id, value_name))

        # 2) So o que MUDA de fato. A comparacao e' pelo `value_name` e nao
        #    tambem pelo `value_id` de proposito: em atributo de texto livre
        #    (`string`) o formulario reenvia o nome sem o id, e o id que o ML
        #    resolveu esta gravado. Comparar os dois marcaria isso como
        #    mudanca e APAGARIA um `value_id` valido.
        mudancas = [
            (attr, value_id, value_name)
            for attr, value_id, value_name in resolvidos
            if attr.value_name != value_name
        ]

        # 3) Obrigatorio nao pode ficar vazio depois da edicao.
        esvaziados = sorted(
            attr.attribute_name
            for attr, _vid, value_name in mudancas
            if attr.is_required and value_name is None
        )
        if esvaziados:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    "Atributo obrigatório não pode ficar vazio: "
                    + ", ".join(esvaziados)
                ),
            )

        # 4) Acao que nao faz nada nao e' acao (ver o docstring de
        #    `ListingReviewEvent.approved_count`).
        if not mudancas:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Nenhum atributo foi alterado",
            )

        # 5) Escreve. O antes/depois vive so no log: ainda nao sabemos que
        #    perguntas faremos a esse historico, e tabela agora seria apostar
        #    no formato antes de conhecer o uso.
        for attr, value_id, value_name in mudancas:
            anterior = attr.value_name
            attr.value_id = value_id
            attr.value_name = value_name
            attr.source = "seller"
            logger.info(
                "attribute_edit listing_id=%s sku=%s user_id=%s attribute_id=%s de=%r para=%r",
                listing.id, listing.sku_external_id, user_id,
                attr.attribute_id, anterior, value_name,
            )

        posicoes_existentes = (await self.db.execute(
            select(ListingImage.sort_order).where(
                ListingImage.listing_id == listing.id,
                ListingImage.sort_order.in_(tuple(POSITION_KINDS)),
            )
        )).scalars().all()
        stale = stale_positions(antes, snapshot_attributes(atributos), posicoes_existentes)

        self.db.add(ListingReviewEvent(
            listing_id=listing.id,
            user_id=user_id,
            action=REVIEW_ACTION_ATTRIBUTES_EDITED,
            mode=REVIEW_MODE_INDIVIDUAL,
            approved_count=len(mudancas),
            # Nao ha cronometro nesta tela, e estimar seria inventar dado.
            review_seconds=None,
        ))

        duplicados = sorted({
            attr.attribute_id
            for attr, _vid, _vn in mudancas
            if attr.attribute_id in DUPLICATED_CATALOG_ATTRIBUTE_IDS
        })
        logger.info(
            "attribute_edit listing_id=%s sku=%s user_id=%s alterados=%d stale_positions=%s",
            listing.id, listing.sku_external_id, user_id, len(mudancas), stale,
        )
        await self.db.commit()
        return stale, duplicados
```

No topo do arquivo, junto dos outros imports de `app.services`, acrescentar:

```python
from app.services.attribute_impact import snapshot_attributes, stale_positions
```

> Se esse import no topo causar ciclo (`attribute_impact` → `image_card_copy_service` → ...), mova-o para dentro de `edit_attributes`, seguindo o padrão já usado no arquivo para `raw_photo_standby_service`.

- [ ] **Step 5: Rodar o teste e confirmar que passa**

Run: `cd backend && python -m pytest tests/test_editar_atributos_service.py -q`
Expected: PASS (26 passed)

- [ ] **Step 6: Rodar a suíte inteira**

Run: `cd backend && python -m pytest -q`
Expected: nenhuma falha nova.

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/listing_service.py backend/tests/test_editar_atributos_service.py
git commit -m "feat(atributos): edit_attributes grava sem avancar etapa nem disparar geracao"
```

---

### Task 4: Endpoint `PATCH /listings/{id}/attributes`

**Files:**
- Modify: `backend/app/schemas/listing.py` (acrescentar `AttributesEditResponse` no fim)
- Modify: `backend/app/api/v1/endpoints/listings.py` (depois do `PUT` de atributos, ~linha 229)
- Test: `backend/tests/test_editar_atributos_rota.py` (criar)
- Test: `backend/tests/test_editar_atributos_pg.py` (criar — Postgres real)

**Interfaces:**
- Consumes: `ListingService.edit_attributes` (Task 3).
- Produces: `app.schemas.listing.AttributesEditResponse` com `listing: ListingSummary`, `stale_positions: list[int]`, `duplicated_fields: list[str]`.

- [ ] **Step 1: Escrever o teste de rota que falha**

Criar `backend/tests/test_editar_atributos_rota.py`:

```python
"""Rota PATCH de correcao de atributos. Sem banco (sempre roda)."""


def _rotas():
    from app.api.v1.endpoints import listings

    return listings.router.routes


class TestRota:
    def test_patch_de_atributos_existe(self):
        alvos = [
            r for r in _rotas()
            if getattr(r, "path", None) == "/{listing_id}/attributes"
            and "PATCH" in getattr(r, "methods", set())
        ]
        assert len(alvos) == 1

    def test_put_de_atributos_continua_existindo(self):
        """O PUT e' o passo de PREENCHIMENTO e nao muda nesta branch."""
        alvos = [
            r for r in _rotas()
            if getattr(r, "path", None) == "/{listing_id}/attributes"
            and "PUT" in getattr(r, "methods", set())
        ]
        assert len(alvos) == 1

    def test_patch_devolve_o_schema_de_edicao(self):
        from app.schemas.listing import AttributesEditResponse

        alvo = next(
            r for r in _rotas()
            if getattr(r, "path", None) == "/{listing_id}/attributes"
            and "PATCH" in getattr(r, "methods", set())
        )
        assert alvo.response_model is AttributesEditResponse


class TestSchema:
    def test_campos_e_defaults(self):
        from app.schemas.listing import AttributesEditResponse

        campos = AttributesEditResponse.model_fields
        assert set(campos) == {"listing", "stale_positions", "duplicated_fields"}

    def test_listas_vazias_sao_validas(self):
        from app.schemas.listing import AttributesEditResponse, ListingSummary

        assert "stale_positions" in AttributesEditResponse.model_fields
        assert AttributesEditResponse.model_fields["listing"].annotation is ListingSummary
```

- [ ] **Step 2: Rodar o teste e confirmar que falha**

Run: `cd backend && python -m pytest tests/test_editar_atributos_rota.py -q`
Expected: FAIL com `ImportError: cannot import name 'AttributesEditResponse'`

- [ ] **Step 3: Criar o schema**

No fim de `backend/app/schemas/listing.py`:

```python
class AttributesEditResponse(BaseModel):
    """Resposta do PATCH de correcao de atributos.

    Mora aqui, e nao em `schemas/attribute.py`, porque embute
    `ListingSummary` — e `attribute.py` nao importa este modulo hoje.

    `stale_positions`: posicoes do esquema de 5 cujo texto IMPRESSO NA IMAGEM
    nao corresponde mais ao banco. So avisa (opcao A do spec): regenerar
    sozinho gastaria chamada paga sem decisao humana. Vazio quando o anuncio
    ainda nao tem imagem gerada.

    `duplicated_fields`: atributos editados que existem em duplicata no
    sistema (`BRAND`/`sku_brand`, `MODEL`/`sku_model`). O atributo alimenta a
    ficha (posicao 4); a coluna do listing alimenta a apresentacao (posicao
    1). Corrigir um nao corrige o outro — a tela avisa.
    """

    listing: ListingSummary
    stale_positions: list[int] = []
    duplicated_fields: list[str] = []
```

- [ ] **Step 4: Criar o endpoint**

Em `backend/app/api/v1/endpoints/listings.py`, acrescentar `AttributesEditResponse` ao import vindo de `app.schemas.listing`, e inserir **logo depois** da função `submit_attributes` (antes de `generate_images`):

```python
@router.patch("/{listing_id}/attributes", response_model=AttributesEditResponse)
async def edit_attributes(
    listing_id: UUID,
    body: AttributesSubmitRequest,
    active_seller=Depends(get_active_seller),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Corrige atributo já gravado, SEM avançar a etapa do anúncio.

    O `PUT` da mesma rota é o passo de preenchimento (tolera obrigatório
    vazio e decide o status seguinte); este é o de correção, e recusa
    obrigatório vazio. 409 fora dos status editáveis ou com regeneração de
    imagem em andamento; 422 para valor fora da enumeração, obrigatório
    esvaziado ou edição que não muda nada. Nenhuma task é enfileirada.
    Ver `ListingService.edit_attributes`.
    """
    svc = ListingService(db)
    listing = await svc.get_or_404(listing_id, active_seller.id)
    stale, duplicados = await svc.edit_attributes(
        listing, [a.model_dump() for a in body.attributes], user_id=current_user.id
    )
    return AttributesEditResponse(
        listing=await svc.summary_after_commit(listing),
        stale_positions=stale,
        duplicated_fields=duplicados,
    )
```

- [ ] **Step 5: Rodar o teste de rota e confirmar que passa**

Run: `cd backend && python -m pytest tests/test_editar_atributos_rota.py -q`
Expected: PASS (5 passed)

- [ ] **Step 6: Escrever o teste de Postgres real**

Criar `backend/tests/test_editar_atributos_pg.py`. Copiar o cabeçalho de fixtures/skip de `backend/tests/test_recusa_aprovacao_vazia.py` (mesma montagem de engine, `drop_all`/`create_all`, `pytestmark` de skip sem `TEST_DATABASE_URL`, criação de user/seller/listing). Os casos:

```python
class TestEdicaoRealNoBanco:
    async def test_persiste_o_valor_e_marca_source_seller(self, ...):
        """Edita FLAVOR de Chocolate para Lichia; rele do banco numa sessao
        NOVA e confere value_name e source."""

    async def test_grava_exatamente_um_evento_attributes_edited(self, ...):
        """Conta linhas de `listing_review_events` antes e depois: +1, com
        action='attributes_edited', mode='individual', approved_count=1,
        review_seconds IS NULL e user_id do autor."""

    async def test_nao_muda_o_status_nem_apaga_imagem_aprovada_nem_descricao(self, ...):
        """Listing em ready_to_publish com 5 imagens aprovadas e uma
        ListingDescription. Depois da edicao: status ainda ready_to_publish,
        5 imagens ainda approved=True, descricao intacta (mesmo id e mesmo
        HTML)."""

    async def test_ficha_alterada_marca_a_posicao_4(self, ...):
        """Cria as 5 posicoes em listing_images; edita MODEL; a resposta traz
        4 em stale_positions."""

    async def test_anuncio_sem_imagem_nao_recebe_aviso(self, ...):
        """Mesmo listing sem nenhuma linha em listing_images: stale_positions
        volta vazio mesmo editando MODEL."""

    async def test_422_de_obrigatorio_vazio_nao_escreve_nada(self, ...):
        """Tenta esvaziar BRAND (is_required=True) junto com uma edicao valida
        de FLAVOR. Espera 422; rele do banco: BRAND e FLAVOR intactos e ZERO
        eventos gravados."""
```

- [ ] **Step 7: Rodar os testes de Postgres**

Run (sem a variável): `cd backend && python -m pytest tests/test_editar_atributos_pg.py -q`
Expected: 6 skipped.

Run (com banco): `cd backend && TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5433/publicar_test python -m pytest tests/test_editar_atributos_pg.py -q`
Expected: PASS (6 passed)

> Se `TEST_DATABASE_URL` não estiver disponível na sua máquina, reporte isso em DONE_WITH_CONCERNS — não invente um resultado de teste que você não rodou.

- [ ] **Step 8: Rodar a suíte inteira**

Run: `cd backend && python -m pytest -q`
Expected: nenhuma falha nova.

- [ ] **Step 9: Commit**

```bash
git add backend/app/schemas/listing.py backend/app/api/v1/endpoints/listings.py backend/tests/test_editar_atributos_rota.py backend/tests/test_editar_atributos_pg.py
git commit -m "feat(atributos): PATCH /listings/{id}/attributes corrige sem avancar etapa"
```

---

### Task 5: Regenerar posição em `ready_to_publish`

**Files:**
- Modify: `backend/app/services/listing_service.py` (`regenerate_position`, ~linha 393)
- Modify: `backend/app/api/v1/endpoints/listings.py` (docstring de `regenerate_image_position`, ~linha 296)
- Modify: `backend/tests/test_regenerar_posicao_service.py` (um teste existente precisa mudar de status)
- Test: `backend/tests/test_regenerar_posicao_ready_to_publish.py` (criar)

**Interfaces:**
- Consumes: `REGENERABLE_POSITION_STATUSES` (Task 1).
- Produces: `regenerate_position` passa a aceitar `ready_to_publish`; nesse status o anúncio volta para `pending_image_approval`.

**Por que:** em `ready_to_publish` as 5 posições estão `approved=True`. Só relaxar a guarda de status entregaria um endpoint que devolve 409 em 100% dos casos. A regeneração ali **desaprova a posição e devolve o anúncio à revisão** — a invariante "publica só imagem aprovada" continua intacta, e o worker (`image_tasks.py:708`) exige `pending_image_approval` para não pular a task.

- [ ] **Step 1: Ajustar o teste existente que usa `ready_to_publish` como status recusado**

Em `backend/tests/test_regenerar_posicao_service.py`, no teste `test_recusa_fora_de_pending_image_approval_antes_de_consultar`: trocar `_listing("ready_to_publish")` por `_listing("pending_description")` e o assert `"ready_to_publish" in exc.value.detail` por `"pending_description" in exc.value.detail`. Renomear para `test_recusa_status_nao_regeneravel_antes_de_consultar`. Ajustar o assert de mensagem para `"pending_image_approval" in exc.value.detail`.

- [ ] **Step 2: Escrever o teste novo que falha**

Criar `backend/tests/test_regenerar_posicao_ready_to_publish.py`:

```python
"""Regenerar UMA posicao a partir de `ready_to_publish`. Sem banco.

Em `ready_to_publish` as 5 posicoes estao aprovadas. Regenerar ali DESAPROVA
a posicao pedida e devolve o anuncio a `pending_image_approval` — sem isso o
endpoint devolveria 409 em 100% dos casos, e o worker
(`image_tasks._regenerate_position_async`) pularia a task, deixando o
placeholder preso para sempre."""
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException


def _listing(status):
    listing = MagicMock()
    listing.id = uuid.uuid4()
    listing.status = status
    listing.sku_external_id = "91"
    return listing


def _linha(approved, status="approved"):
    img = MagicMock()
    img.approved = approved
    img.status = status
    return img


def _db(ocupantes):
    db = AsyncMock()
    r = MagicMock()
    r.scalars.return_value.all.return_value = list(ocupantes)
    db.execute = AsyncMock(return_value=r)
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    db.refresh = AsyncMock()
    db.delete = AsyncMock()
    return db


class TestReadyToPublish:
    @pytest.mark.asyncio
    async def test_desaprova_a_posicao_e_volta_para_revisao(self):
        from app.services.listing_service import ListingService

        aprovada = _linha(approved=True)
        listing = _listing("ready_to_publish")
        db = _db([aprovada])
        with patch("app.workers.tasks.image_tasks.regenerate_position") as task:
            await ListingService(db).regenerate_position(listing, 4)
        assert aprovada.approved is False
        assert aprovada.status == "uploaded"
        assert listing.status == "pending_image_approval"
        task.delay.assert_called_once()

    @pytest.mark.asyncio
    async def test_volta_para_revisao_mesmo_com_a_posicao_ja_nao_aprovada(self):
        """Anuncio que chegou a ready_to_publish com 4 de 5 aprovadas: sem
        esta regra o status ficaria em ready_to_publish, o worker pularia a
        task pelo guard de status e o placeholder ficaria preso."""
        from app.services.listing_service import ListingService

        listing = _listing("ready_to_publish")
        db = _db([_linha(approved=False, status="uploaded")])
        with patch("app.workers.tasks.image_tasks.regenerate_position"):
            await ListingService(db).regenerate_position(listing, 3)
        assert listing.status == "pending_image_approval"

    @pytest.mark.asyncio
    async def test_cria_o_placeholder_e_enfileira(self):
        from app.models.listing_image import GENERATING_STATUS
        from app.services.listing_service import ListingService

        db = _db([_linha(approved=True)])
        with patch("app.workers.tasks.image_tasks.regenerate_position") as task:
            placeholder = await ListingService(db).regenerate_position(
                _listing("ready_to_publish"), 0
            )
        assert placeholder.status == GENERATING_STATUS
        assert placeholder.approved is False
        assert placeholder.sort_order == 0
        task.delay.assert_called_once()


class TestPendingImageApprovalNaoMuda:
    @pytest.mark.asyncio
    async def test_posicao_aprovada_continua_recusada_com_409(self):
        from app.services.listing_service import ListingService

        aprovada = _linha(approved=True)
        listing = _listing("pending_image_approval")
        db = _db([aprovada])
        with patch("app.workers.tasks.image_tasks.regenerate_position") as task:
            with pytest.raises(HTTPException) as exc:
                await ListingService(db).regenerate_position(listing, 4)
        assert exc.value.status_code == 409
        assert "já está aprovada" in exc.value.detail
        assert aprovada.approved is True
        assert listing.status == "pending_image_approval"
        db.add.assert_not_called()
        task.delay.assert_not_called()

    @pytest.mark.asyncio
    async def test_status_intocado_no_caminho_normal(self):
        from app.services.listing_service import ListingService

        listing = _listing("pending_image_approval")
        db = _db([_linha(approved=False, status="uploaded")])
        with patch("app.workers.tasks.image_tasks.regenerate_position"):
            await ListingService(db).regenerate_position(listing, 2)
        assert listing.status == "pending_image_approval"


class TestStatusRecusados:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("st", ["draft", "pending_description", "publishing", "published"])
    async def test_recusa_com_409_antes_de_consultar(self, st):
        from app.services.listing_service import ListingService

        db = _db([])
        with pytest.raises(HTTPException) as exc:
            await ListingService(db).regenerate_position(_listing(st), 2)
        assert exc.value.status_code == 409
        db.execute.assert_not_awaited()
```

- [ ] **Step 3: Rodar os testes e confirmar que falham**

Run: `cd backend && python -m pytest tests/test_regenerar_posicao_ready_to_publish.py -q`
Expected: FAIL — a guarda de status ainda recusa `ready_to_publish`.

- [ ] **Step 4: Alterar `regenerate_position`**

Em `backend/app/services/listing_service.py`, no método `regenerate_position`:

**4a.** No docstring, substituir a frase `So em\n        \`pending_image_approval\`; o anuncio nao muda de status` por:

```
        Aceita `pending_image_approval` e `ready_to_publish`
        (`REGENERABLE_POSITION_STATUSES`) e trata os dois de forma diferente:

          pending_image_approval  o anuncio NAO muda de status, e posicao
                                  aprovada e' recusada com 409 — imagem
                                  aprovada nao e' substituida por tras do
                                  operador.
          ready_to_publish        as 5 posicoes estao aprovadas ali, entao
                                  recusar posicao aprovada devolveria 409 em
                                  100% dos casos. Regenerar DESAPROVA a
                                  posicao pedida e devolve o anuncio a
                                  `pending_image_approval`. Nao e'
                                  substituicao por tras de ninguem: e' o
                                  clique do proprio operador, e a invariante
                                  "publica so imagem aprovada" fica intacta.
                                  O retorno a revisao tambem e' o que faz o
                                  guard de status do worker
                                  (`_regenerate_position_async`) aceitar a
                                  task.
```

**4b.** Substituir a guarda de status:

```python
        if listing.status != "pending_image_approval":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Regeneração de imagem disponível apenas no status "
                    f"'pending_image_approval' (atual: '{listing.status}')"
                ),
            )
```

por:

```python
        if listing.status not in REGENERABLE_POSITION_STATUSES:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Regeneração de imagem disponível apenas nos status "
                    "'pending_image_approval' e 'ready_to_publish' "
                    f"(atual: '{listing.status}')"
                ),
            )
```

**4c.** Substituir o bloco da posição aprovada:

```python
        if any(img.approved for img in ocupantes):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"A posição {posicao} já está aprovada; imagem aprovada não é regenerada",
            )
```

por:

```python
        if listing.status == "ready_to_publish":
            # Desaprova SO esta posicao e devolve o anuncio a revisao, na
            # mesma transacao do placeholder. `status="uploaded"` (e nao
            # "rejected") porque a linha antiga volta a ser exatamente o que
            # era: gerada e ainda nao julgada — se a regeneracao falhar, ela
            # continua sendo a candidata daquela posicao.
            #
            # A volta acontece MESMO quando a posicao ja nao estava aprovada:
            # um anuncio pode chegar a `ready_to_publish` com 4 de 5, e sem
            # a mudanca de status o worker pularia a task pelo proprio guard
            # (`image_tasks._regenerate_position_async`), deixando o
            # placeholder preso para sempre.
            for img in ocupantes:
                if img.approved:
                    img.approved = False
                    img.status = "uploaded"
            listing.status = "pending_image_approval"
        elif any(img.approved for img in ocupantes):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"A posição {posicao} já está aprovada; imagem aprovada não é regenerada",
            )
```

- [ ] **Step 5: Atualizar a docstring do endpoint**

Em `backend/app/api/v1/endpoints/listings.py`, na docstring de `regenerate_image_position`, trocar `409 fora de\n    \`pending_image_approval\`, em posição aprovada ou com regeneração já em\n    andamento` por:

```
    409 fora de `pending_image_approval`/`ready_to_publish`, em posição já
    aprovada quando o anúncio está em `pending_image_approval`, ou com
    regeneração já em andamento. A partir de `ready_to_publish` a posição é
    desaprovada e o anúncio volta para `pending_image_approval`.
```

- [ ] **Step 6: Rodar os testes e confirmar que passam**

Run: `cd backend && python -m pytest tests/test_regenerar_posicao_ready_to_publish.py tests/test_regenerar_posicao_service.py -q`
Expected: PASS, sem falhas.

- [ ] **Step 7: Rodar a suíte inteira**

Run: `cd backend && python -m pytest -q`
Expected: nenhuma falha nova. Se `tests/test_regenerar_posicao_pg.py` tiver um caso que afirma o 409 de `ready_to_publish`, ajuste-o para o novo contrato e diga no relatório qual foi.

- [ ] **Step 8: Commit**

```bash
git add backend/app/services/listing_service.py backend/app/api/v1/endpoints/listings.py backend/tests/test_regenerar_posicao_ready_to_publish.py backend/tests/test_regenerar_posicao_service.py
git commit -m "feat(imagens): regenerar posicao em ready_to_publish desaprova e volta para revisao"
```

---

### Task 6: `POST /listings/{id}/pipeline/regenerate_description`

**Files:**
- Modify: `backend/app/services/listing_service.py` (depois de `regenerate_position`)
- Modify: `backend/app/api/v1/endpoints/listings.py` (depois de `regenerate_image_position`)
- Test: `backend/tests/test_regerar_descricao.py` (criar)

**Interfaces:**
- Consumes: task `app.workers.tasks.ai_tasks.generate_description` (já existe).
- Produces: `ListingService.regenerate_description(listing) -> None`.

**Por que:** hoje a única forma de refazer a descrição é reaprovar as imagens, e cada reaprovação grava um `listing_review_events` afirmando revisão humana de imagens que ninguém olhou. O dado criado para **provar** revisão vira prova falsa.

- [ ] **Step 1: Escrever o teste que falha**

Criar `backend/tests/test_regerar_descricao.py`:

```python
"""Regerar a descricao sem passar pela aprovacao de imagens. Sem banco.

Existe por causa de uma corrupcao de auditoria: ate aqui a unica saida era
reaprovar as imagens, e cada reaprovacao grava um `listing_review_events`
afirmando revisao humana que nao aconteceu."""
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException


def _listing(status="ready_to_publish"):
    listing = MagicMock()
    listing.id = uuid.uuid4()
    listing.status = status
    listing.sku_external_id = "91"
    return listing


def _db(rowcount=1):
    db = AsyncMock()
    r = MagicMock()
    r.rowcount = rowcount
    db.execute = AsyncMock(return_value=r)
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    return db


class TestGuardas:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("st", [
        "draft", "pending_image_approval", "generating_description",
        "publishing", "published", "failed",
    ])
    async def test_recusa_fora_de_ready_to_publish_antes_de_consultar(self, st):
        from app.services.listing_service import ListingService

        db = _db()
        with pytest.raises(HTTPException) as exc:
            await ListingService(db).regenerate_description(_listing(st))
        assert exc.value.status_code == 409
        assert st in exc.value.detail
        db.execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_corrida_perdida_devolve_409(self):
        from app.services.listing_service import ListingService

        db = _db(rowcount=0)
        with patch("app.workers.tasks.ai_tasks.generate_description") as task:
            with pytest.raises(HTTPException) as exc:
                await ListingService(db).regenerate_description(_listing())
        assert exc.value.status_code == 409
        task.delay.assert_not_called()


class TestCaminhoNormal:
    @pytest.mark.asyncio
    async def test_muda_para_generating_description_e_enfileira(self):
        from app.services.listing_service import ListingService

        listing = _listing()
        db = _db()
        with patch("app.workers.tasks.ai_tasks.generate_description") as task:
            await ListingService(db).regenerate_description(listing)
        assert listing.status == "generating_description"
        task.delay.assert_called_once_with(str(listing.id))

    @pytest.mark.asyncio
    async def test_commit_antes_de_enfileirar(self):
        """Mesma ordem de `regenerate_position`: o worker le do banco."""
        from app.services.listing_service import ListingService

        ordem = []
        db = _db()
        db.commit = AsyncMock(side_effect=lambda: ordem.append("commit"))
        with patch("app.workers.tasks.ai_tasks.generate_description") as task:
            task.delay = MagicMock(side_effect=lambda *_a: ordem.append("delay"))
            await ListingService(db).regenerate_description(_listing())
        assert ordem.index("commit") < ordem.index("delay")

    @pytest.mark.asyncio
    async def test_nao_grava_evento_de_revisao(self):
        """O ponto todo desta tarefa: regerar descricao NAO e' revisao humana
        de imagem, e nao pode gravar um evento dizendo que foi."""
        from app.services.listing_service import ListingService

        db = _db()
        with patch("app.workers.tasks.ai_tasks.generate_description"):
            await ListingService(db).regenerate_description(_listing())
        db.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_nao_dispara_imagem_nem_publicacao(self):
        from app.services.listing_service import ListingService

        db = _db()
        with patch("app.workers.tasks.ai_tasks.generate_description"), \
             patch("app.workers.tasks.image_tasks.generate_images") as gi, \
             patch("app.workers.tasks.publish_tasks.publish_listing") as pl:
            await ListingService(db).regenerate_description(_listing())
        gi.delay.assert_not_called()
        pl.delay.assert_not_called()


class TestBrokerFora:
    @pytest.mark.asyncio
    async def test_devolve_o_status_e_responde_503(self):
        from app.services.listing_service import ListingService

        listing = _listing()
        db = _db()
        with patch("app.workers.tasks.ai_tasks.generate_description") as task:
            task.delay = MagicMock(side_effect=OSError("redis fora"))
            with pytest.raises(HTTPException) as exc:
                await ListingService(db).regenerate_description(listing)
        assert exc.value.status_code == 503
        assert listing.status == "ready_to_publish"


class TestRota:
    def test_rota_existe_e_devolve_listing_summary(self):
        from app.api.v1.endpoints import listings
        from app.schemas.listing import ListingSummary

        alvo = [
            r for r in listings.router.routes
            if getattr(r, "path", None) == "/{listing_id}/pipeline/regenerate_description"
        ]
        assert len(alvo) == 1
        assert "POST" in alvo[0].methods
        assert alvo[0].response_model is ListingSummary
```

- [ ] **Step 2: Rodar o teste e confirmar que falha**

Run: `cd backend && python -m pytest tests/test_regerar_descricao.py -q`
Expected: FAIL com `AttributeError: 'ListingService' object has no attribute 'regenerate_description'`

- [ ] **Step 3: Implementar o método**

Em `backend/app/services/listing_service.py`, **logo depois** de `recusar_se_regeneracao_em_andamento`:

```python
    async def regenerate_description(self, listing: Listing) -> None:
        """Refaz a descricao de um anuncio ja pronto, sem tocar nas imagens.

        Existe por causa de uma corrupcao de auditoria. `generate_description`
        so era disparada por `approve_images`/`bulk_approve_images`, entao a
        UNICA forma de refazer a descricao depois de corrigir um atributo era
        reaprovar as imagens — e cada reaprovacao grava um
        `listing_review_events` afirmando revisao humana de imagens que
        ninguem olhou. O dado criado para PROVAR revisao virava prova falsa.

        Este caminho NAO grava evento: nao houve revisao de imagem nenhuma.

        O status muda para `generating_description` e volta sozinho para
        `ready_to_publish` no fim da task. Mudar e' o correto aqui: o anuncio
        ESTA gerando descricao, e esconder isso deixaria a tela mentindo. A
        regra "editar nao avanca etapa" vale para a edicao de atributos, nao
        para esta acao explicita do operador.
        """
        if listing.status != "ready_to_publish":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Regeração da descrição indisponível no status '{listing.status}'",
            )
        # UPDATE atomico + dispatch, o padrao dos gatilhos do projeto: duas
        # requisicoes simultaneas nao podem enfileirar duas geracoes.
        result = await self.db.execute(
            sa_update(Listing)
            .where(Listing.id == listing.id, Listing.status == "ready_to_publish")
            .values(status="generating_description")
            .execution_options(synchronize_session=False)
        )
        await self.db.commit()
        if result.rowcount != 1:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Este anúncio acabou de mudar de estado; recarregue a página.",
            )
        listing.status = "generating_description"

        from app.workers.tasks.ai_tasks import generate_description
        try:
            generate_description.delay(str(listing.id))
        except Exception as exc:
            # Broker fora: sem a task, o anuncio ficaria preso em
            # `generating_description` para sempre — status que nenhum beat
            # varre e de onde nenhuma acao humana sai.
            await self.db.execute(
                sa_update(Listing)
                .where(Listing.id == listing.id, Listing.status == "generating_description")
                .values(status="ready_to_publish")
                .execution_options(synchronize_session=False)
            )
            await self.db.commit()
            listing.status = "ready_to_publish"
            logger.error(
                "regen_descricao listing_id=%s result=fila_indisponivel reason=%s",
                listing.id, exc,
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    "Fila de processamento indisponível no momento; o pedido não foi "
                    "registrado. Tente de novo em instantes."
                ),
            )
```

- [ ] **Step 4: Criar o endpoint**

Em `backend/app/api/v1/endpoints/listings.py`, depois de `regenerate_image_position`:

```python
@router.post("/{listing_id}/pipeline/regenerate_description", response_model=ListingSummary)
async def regenerate_description(
    listing_id: UUID,
    active_seller=Depends(get_active_seller),
    db: AsyncSession = Depends(get_db),
):
    """Refaz a descrição de um anúncio em `ready_to_publish`.

    Usado depois de corrigir um atributo: a descrição é gerada uma única vez
    e fica desatualizada. Antes deste endpoint a única saída era reaprovar as
    imagens, o que gravava um evento de revisão humana que não aconteceu.
    409 fora de `ready_to_publish`; 503 com a fila fora.
    """
    svc = ListingService(db)
    listing = await svc.get_or_404(listing_id, active_seller.id)
    await svc.regenerate_description(listing)
    return await svc.summary_after_commit(listing)
```

- [ ] **Step 5: Rodar o teste e confirmar que passa**

Run: `cd backend && python -m pytest tests/test_regerar_descricao.py -q`
Expected: PASS (13 passed)

- [ ] **Step 6: Rodar a suíte inteira**

Run: `cd backend && python -m pytest -q`
Expected: nenhuma falha nova.

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/listing_service.py backend/app/api/v1/endpoints/listings.py backend/tests/test_regerar_descricao.py
git commit -m "feat(descricao): endpoint de regerar descricao sem evento de revisao falso"
```

---

### Task 7: `bulk_fill_attribute` passa a validar

**Files:**
- Modify: `backend/app/services/listing_service.py` (`bulk_fill_attribute`, ~linha 868)
- Test: `backend/tests/test_bulk_fill_attribute_valida.py` (criar)

**Interfaces:**
- Consumes: `ListingService._validar_valor` (já existe).
- Produces: nada novo — muda o comportamento interno de `bulk_fill_attribute`.

**Por que:** é a única porta que grava valor de atributo **sem validar**, e ainda deixa `source` no valor antigo. Um valor inválido gravado por aqui só aparece na publicação, como `Attribute [X] is not valid`.

- [ ] **Step 1: Escrever o teste que falha**

Criar `backend/tests/test_bulk_fill_attribute_valida.py`:

```python
"""A grade de atributos em massa passa a validar, como o caminho individual.
Sem banco (sempre roda)."""
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


def _attr(attribute_id="FLAVOR", *, tipo="list", allowed=None, value_name=None):
    return SimpleNamespace(
        attribute_id=attribute_id,
        attribute_name="Sabor",
        value_id=None,
        value_name=value_name,
        attribute_type=tipo,
        is_required=False,
        allowed_values=allowed if allowed is not None else [
            {"id": "1", "name": "Chocolate"},
            {"id": "2", "name": "Baunilha"},
        ],
        source="ai",
    )


def _listing():
    listing = MagicMock()
    listing.id = uuid.uuid4()
    listing.status = "pending_seller_attributes"
    return listing


def _db(listing, attr, *, obrigatorios_vazios=()):
    """execute responde: listing, atributo, obrigatorios sem valor."""
    db = AsyncMock()

    def _one(v):
        r = MagicMock()
        r.scalar_one_or_none.return_value = v
        return r

    def _all(v):
        r = MagicMock()
        r.scalars.return_value.all.return_value = list(v)
        return r

    db.execute = AsyncMock(side_effect=[_one(listing), _one(attr), _all(obrigatorios_vazios)])
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    return db


class TestValidacao:
    @pytest.mark.asyncio
    async def test_valor_fora_da_enumeracao_falha_o_item_sem_gravar(self):
        from app.services.listing_service import ListingService

        listing, attr = _listing(), _attr()
        db = _db(listing, attr)
        svc = ListingService(db, seller_id=uuid.uuid4())
        res = await svc.bulk_fill_attribute([listing.id], "FLAVOR", "Lichia", None)
        assert res.failed == 1 and res.processed == 0
        assert res.results[0].success is False
        assert attr.value_name is None
        assert attr.source == "ai"

    @pytest.mark.asyncio
    async def test_mensagem_de_erro_cabe_na_tela(self):
        """`sanitizeBulkError` troca por mensagem generica acima de 200
        chars — a lista inteira de aceitos nunca chegaria ao operador."""
        from app.services.listing_service import ListingService

        listing, attr = _listing(), _attr(
            allowed=[{"id": str(i), "name": f"Sabor numero {i}"} for i in range(40)]
        )
        db = _db(listing, attr)
        svc = ListingService(db, seller_id=uuid.uuid4())
        res = await svc.bulk_fill_attribute([listing.id], "FLAVOR", "Lichia", None)
        erro = res.results[0].error
        assert erro and len(erro) <= 200
        for marcador in ("sql", "traceback", "sqlalchemy"):
            assert marcador not in erro.lower()

    @pytest.mark.asyncio
    async def test_valor_valido_grava_e_marca_source_seller(self):
        from app.services.listing_service import ListingService

        listing, attr = _listing(), _attr()
        db = _db(listing, attr)
        svc = ListingService(db, seller_id=uuid.uuid4())
        res = await svc.bulk_fill_attribute([listing.id], "FLAVOR", "Chocolate", None)
        assert res.processed == 1 and res.failed == 0
        assert attr.value_name == "Chocolate"
        assert attr.value_id == "1"  # resolvido a partir do nome
        assert attr.source == "seller"

    @pytest.mark.asyncio
    async def test_texto_livre_passa_em_tipo_string(self):
        """Mesma regra do caminho individual: em `string` a lista do ML e' de
        sugestoes, nao enumeracao."""
        from app.services.listing_service import ListingService

        listing, attr = _listing(), _attr(tipo="string")
        db = _db(listing, attr)
        svc = ListingService(db, seller_id=uuid.uuid4())
        res = await svc.bulk_fill_attribute([listing.id], "FLAVOR", "Lichia", None)
        assert res.processed == 1
        assert attr.value_name == "Lichia"

    @pytest.mark.asyncio
    async def test_atributo_inexistente_continua_falhando_o_item(self):
        from app.services.listing_service import ListingService

        listing = _listing()
        db = _db(listing, None)
        svc = ListingService(db, seller_id=uuid.uuid4())
        res = await svc.bulk_fill_attribute([listing.id], "FLAVOR", "Chocolate", None)
        assert res.failed == 1
        assert "atributo não encontrado" in res.results[0].error


class TestComportamentoPreservado:
    @pytest.mark.asyncio
    async def test_avanca_para_pending_description_quando_nao_resta_obrigatorio(self):
        """`bulk_fill_attribute` e' o gemeo em LOTE de `submit_attributes`
        (preenchimento), nao da correcao: continua avancando a etapa."""
        from app.services.listing_service import ListingService

        listing, attr = _listing(), _attr()
        db = _db(listing, attr, obrigatorios_vazios=())
        svc = ListingService(db, seller_id=uuid.uuid4())
        await svc.bulk_fill_attribute([listing.id], "FLAVOR", "Chocolate", None)
        assert listing.status == "pending_description"

    @pytest.mark.asyncio
    async def test_nao_grava_evento_de_auditoria(self):
        """Preenchimento nao gera evento — `submit_attributes` tambem nao."""
        from app.services.listing_service import ListingService

        listing, attr = _listing(), _attr()
        db = _db(listing, attr)
        db.add = MagicMock()
        svc = ListingService(db, seller_id=uuid.uuid4())
        await svc.bulk_fill_attribute([listing.id], "FLAVOR", "Chocolate", None)
        db.add.assert_not_called()
```

- [ ] **Step 2: Rodar o teste e confirmar que falha**

Run: `cd backend && python -m pytest tests/test_bulk_fill_attribute_valida.py -q`
Expected: FAIL — hoje o valor inválido é gravado sem reclamar.

- [ ] **Step 3: Reescrever o miolo de `bulk_fill_attribute`**

Em `backend/app/services/listing_service.py`, **substituir** o bloco que hoje vai do `attr_r = await self.db.execute(sa_update(ListingAttribute)...)` até o `continue` do `if attr_r.rowcount == 0:` por:

```python
                # Carrega a linha em vez de dar UPDATE cego: `_validar_valor`
                # precisa de `allowed_values` e `attribute_type`. Ate aqui
                # esta era a UNICA porta que gravava valor de atributo sem
                # validar — um valor invalido so aparecia na publicacao, como
                # `Attribute [X] is not valid`, depois de ja ter gasto
                # geracao de imagem e descricao.
                attr = (await self.db.execute(
                    select(ListingAttribute).where(
                        ListingAttribute.listing_id == lid,
                        ListingAttribute.attribute_id == attribute_id,
                    )
                )).scalar_one_or_none()
                if attr is None:
                    results.append(BulkItemResult(listing_id=lid, success=False, error="atributo não encontrado"))
                    continue
                try:
                    novo_id, novo_nome = self._validar_valor(
                        attr,
                        {"attribute_id": attribute_id, "value_id": value_id, "value_name": value_name},
                    )
                except HTTPException:
                    # Mensagem CURTA de proposito: a de `_validar_valor` lista
                    # ate 15 valores aceitos e passa dos 200 chars que o
                    # `sanitizeBulkError` do frontend usa como corte para
                    # "erro tecnico" — o operador veria a mensagem generica no
                    # lugar do motivo. Um item invalido tambem nao pode
                    # derrubar o lote inteiro com 422.
                    results.append(BulkItemResult(
                        listing_id=lid,
                        success=False,
                        error=f"valor {value_name!r} não é válido para '{attr.attribute_name}' nesta categoria"[:200],
                    ))
                    continue
                attr.value_id = novo_id
                attr.value_name = novo_nome
                attr.source = "seller"
```

- [ ] **Step 4: Rodar o teste e confirmar que passa**

Run: `cd backend && python -m pytest tests/test_bulk_fill_attribute_valida.py -q`
Expected: PASS (7 passed)

- [ ] **Step 5: Rodar a suíte inteira**

Run: `cd backend && python -m pytest -q`
Expected: nenhuma falha nova.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/listing_service.py backend/tests/test_bulk_fill_attribute_valida.py
git commit -m "fix(atributos): grade em massa valida o valor antes de gravar"
```

---

### Task 8: Frontend — corrigir atributos e regerar descrição

**Files:**
- Modify: `frontend/src/types/listing.ts`
- Modify: `frontend/src/lib/api/listings.ts`
- Create: `frontend/src/lib/attribute-edit.ts`
- Modify: `frontend/src/components/listings/AttributeForm.tsx`
- Modify: `frontend/src/app/(dashboard)/listings/[id]/attributes/page.tsx`
- Modify: `frontend/src/app/(dashboard)/listings/[id]/page.tsx`
- Create: `frontend/src/components/listings/AttributeEditWarning.tsx`

**Interfaces:**
- Consumes: `PATCH /api/v1/listings/{id}/attributes` (Task 4), `POST /api/v1/listings/{id}/pipeline/regenerate_description` (Task 6).
- Produces: nada consumido por outra task.

**Contexto crítico:** o `AttributeForm` de hoje **filtra do payload todo atributo com valor vazio**. Em modo de correção isso tornaria impossível apagar um campo: o backend nunca veria o apagamento. Em modo `edit` o payload precisa incluir os campos esvaziados.

- [ ] **Step 1: Escrever o teste que falha**

O projeto não tem jest nem vitest, e **nada deve ser instalado**. Seguir o padrão já usado na branch da fila: teste com `node:test` e `tsx` do próprio projeto. Criar `frontend/src/lib/attribute-edit.ts` com a lógica pura e testá-la.

Criar `frontend/src/lib/__tests__/attribute-edit.test.ts`:

```ts
import { strict as assert } from "node:assert"
import { test } from "node:test"
import { buildEditPayload, isEditableStatus } from "../attribute-edit"
import type { AttributeOut } from "@/types/listing"

const attr = (id: string, value_name: string | null): AttributeOut =>
  ({
    attribute_id: id,
    attribute_name: id,
    value_id: null,
    value_name,
    attribute_type: "string",
    is_required: false,
    allowed_values: null,
    tags: null,
    is_editable: true,
  }) as AttributeOut

test("payload de edição inclui campo esvaziado", () => {
  const atual = { FLAVOR: { value_name: "" } }
  const payload = buildEditPayload([attr("FLAVOR", "Chocolate")], atual)
  assert.deepEqual(payload, [{ attribute_id: "FLAVOR", value_id: undefined, value_name: "" }])
})

test("payload de edição omite o que não mudou", () => {
  const atual = { FLAVOR: { value_name: "Chocolate" } }
  assert.deepEqual(buildEditPayload([attr("FLAVOR", "Chocolate")], atual), [])
})

test("payload de edição inclui valor trocado", () => {
  const atual = { FLAVOR: { value_name: "Lichia" } }
  const payload = buildEditPayload([attr("FLAVOR", "Chocolate")], atual)
  assert.equal(payload.length, 1)
  assert.equal(payload[0].value_name, "Lichia")
})

test("valor nulo no servidor conta como vazio", () => {
  const atual = { FLAVOR: { value_name: "" } }
  assert.deepEqual(buildEditPayload([attr("FLAVOR", null)], atual), [])
})

test("status editáveis batem com o backend", () => {
  for (const s of [
    "draft", "pending_title_approval", "pending_seller_attributes",
    "pending_description", "pending_raw_photos", "pending_ai_engine",
    "pending_image_approval", "ready_to_publish", "failed",
  ]) assert.equal(isEditableStatus(s as never), true, s)

  for (const s of [
    "generating_title", "predicting_category", "generating_images",
    "generating_description", "publishing", "published", "published_paused",
  ]) assert.equal(isEditableStatus(s as never), false, s)
})
```

Rodar com o `tsconfig` estendido do scratchpad, como na branch da fila (ver memória do projeto `project_frontend_prova_sem_infra`). Se o runner não estiver disponível, **diga isso no relatório** e valide com `npx tsc --noEmit` — não invente um resultado.

- [ ] **Step 2: Criar `attribute-edit.ts`**

```ts
import type { AttributeOut, ListingStatus } from "@/types/listing"

/**
 * Espelha `EDITABLE_ATTRIBUTE_STATUSES` em `backend/app/models/listing.py`.
 * A lista não tem CHECK no banco nem contrato compartilhado, então mudou lá,
 * muda aqui — o teste acima é o que cobra.
 */
export const EDITABLE_ATTRIBUTE_STATUSES: readonly ListingStatus[] = [
  "draft",
  "pending_title_approval",
  "pending_seller_attributes",
  "pending_description",
  "pending_raw_photos",
  "pending_ai_engine",
  "pending_image_approval",
  "ready_to_publish",
  "failed",
]

export function isEditableStatus(status: ListingStatus): boolean {
  return EDITABLE_ATTRIBUTE_STATUSES.includes(status)
}

export interface AttributeValue {
  value_id?: string
  value_name: string
}

export interface AttributeEditItem {
  attribute_id: string
  value_id?: string
  value_name: string
}

/**
 * Payload do PATCH de correção: só o que MUDOU, e **incluindo o que foi
 * esvaziado**.
 *
 * O envio do modo de preenchimento filtra todo valor vazio, e é o certo lá —
 * campo escondido simplesmente não entra e nada é apagado. Em correção esse
 * mesmo filtro tornaria impossível limpar um campo: o operador apagaria o
 * texto, o item sumiria do payload e o backend nunca saberia.
 */
export function buildEditPayload(
  attributes: AttributeOut[],
  values: Record<string, AttributeValue>,
): AttributeEditItem[] {
  const items: AttributeEditItem[] = []
  for (const attr of attributes) {
    const atual = values[attr.attribute_id]
    if (!atual) continue
    const antes = (attr.value_name ?? "").trim()
    const depois = (atual.value_name ?? "").trim()
    if (antes === depois) continue
    items.push({
      attribute_id: attr.attribute_id,
      value_id: atual.value_id,
      value_name: atual.value_name ?? "",
    })
  }
  return items
}
```

- [ ] **Step 3: Tipos e clientes de API**

Em `frontend/src/types/listing.ts`, acrescentar:

```ts
/** Espelho de `AttributesEditResponse` em `backend/app/schemas/listing.py`. */
export interface AttributesEditResponse {
  listing: ListingSummary
  /** Posições do esquema de 5 cujo texto impresso na imagem ficou velho. */
  stale_positions: number[]
  /** Atributos editados que existem em duplicata (`BRAND`, `MODEL`). */
  duplicated_fields: string[]
}
```

Em `frontend/src/lib/api/listings.ts`, depois de `submitAttributes`:

```ts
export async function editAttributes(
  listingId: string,
  attributes: AttributeInput[]
): Promise<AttributesEditResponse> {
  return apiFetch<AttributesEditResponse>(`/api/v1/listings/${listingId}/attributes`, {
    method: "PATCH",
    body: JSON.stringify({ attributes }),
  })
}

export async function regenerateDescription(id: string): Promise<ListingSummary> {
  return apiFetch<ListingSummary>(
    `/api/v1/listings/${id}/pipeline/regenerate_description`,
    { method: "POST" }
  )
}
```

Acrescentar `AttributesEditResponse` ao import de tipos no topo do arquivo.

- [ ] **Step 4: `AttributeForm` ganha modo de correção**

Em `frontend/src/components/listings/AttributeForm.tsx`:

1. `interface Props` recebe `mode?: "submit" | "edit"` e `onEdited?: (r: AttributesEditResponse) => void`.
2. Importar `buildEditPayload` de `@/lib/attribute-edit` e `editAttributes` de `@/lib/api/listings`.
3. Na `mutationFn`, ramificar:

```tsx
    mutationFn: () => {
      if (mode === "edit") {
        // Em correção o payload leva o que foi ESVAZIADO. O filtro de valor
        // vazio do modo de preenchimento tornaria impossível limpar um campo.
        return editAttributes(listingId, buildEditPayload(attributes, values))
      }
      const payload = attributes
        .filter((attr) => values[attr.attribute_id]?.value_name?.trim())
        .map((attr) => ({
          attribute_id: attr.attribute_id,
          value_id: values[attr.attribute_id]?.value_id,
          value_name: values[attr.attribute_id]?.value_name ?? "",
        }))
      return submitAttributes(listingId, payload)
    },
```

4. No `onSuccess`, ramificar: em `edit`, invalidar as queries, chamar `onEdited(result)` e **ficar na página** (o operador precisa ler o aviso); o `toast.success` passa a ser `"Correção salva. A etapa do anúncio não mudou."`. Em `submit`, manter o comportamento atual (`router.push`).
5. O texto do botão (linha ~191) passa a ser `mode === "edit" ? "Salvar correção" : "Salvar e continuar"`, e em modo `edit` acrescentar abaixo do botão:

```tsx
{mode === "edit" && (
  <p className="text-xs text-slate-500 mt-2 text-center">
    Salvar não avança a etapa do anúncio.
  </p>
)}
```

- [ ] **Step 5: Criar `AttributeEditWarning.tsx`**

```tsx
"use client"

import Link from "next/link"
import { AlertTriangle } from "lucide-react"
import type { AttributesEditResponse } from "@/types/listing"

const NOME_DA_POSICAO: Record<number, string> = {
  0: "capa",
  1: "apresentação",
  2: "benefícios",
  3: "detalhe",
  4: "ficha técnica",
}

/**
 * Aviso depois de uma correção: o que ficou divergente entre banco e imagem.
 *
 * Só avisa — regenerar por conta própria gastaria chamada paga sem decisão
 * humana. O botão de regenerar posição mora na tela de revisão de imagens,
 * que ainda não existe (bloco B); até lá o caminho é o link da galeria.
 */
export function AttributeEditWarning({
  listingId,
  result,
}: {
  listingId: string
  result: AttributesEditResponse
}) {
  const { stale_positions: stale, duplicated_fields: dup } = result
  if (stale.length === 0 && dup.length === 0) return null

  return (
    <div className="mt-6 rounded-lg border border-amber-200 bg-amber-50 p-4">
      <div className="flex items-start gap-2">
        <AlertTriangle className="w-4 h-4 text-amber-600 mt-0.5 shrink-0" />
        <div className="text-sm text-amber-900 space-y-3">
          {stale.length > 0 && (
            <div>
              <p className="font-medium">
                Esta correção mudou texto impresso {stale.length === 1 ? "na imagem" : "nas imagens"}{" "}
                {stale.map((p) => `${p} (${NOME_DA_POSICAO[p] ?? "posição"})`).join(", ")}.
              </p>
              <p className="mt-1">
                A imagem é o que o comprador vê. Regere{" "}
                {stale.length === 1 ? "essa posição" : "essas posições"} antes de publicar.
              </p>
              <Link
                href={`/listings/${listingId}/images`}
                className="inline-block mt-2 underline underline-offset-2 font-medium"
              >
                Abrir as imagens do anúncio
              </Link>
            </div>
          )}
          {dup.length > 0 && (
            <p>
              {dup.join(" e ")} {dup.length === 1 ? "existe" : "existem"} em duplicata: esta
              correção muda a ficha técnica, mas <strong>não</strong> muda a imagem de
              apresentação, que lê o valor do catálogo de produtos. Corrija também no
              catálogo se o valor estiver errado lá.
            </p>
          )}
        </div>
      </div>
    </div>
  )
}
```

- [ ] **Step 6: Página de atributos**

Em `frontend/src/app/(dashboard)/listings/[id]/attributes/page.tsx`:

1. `const mode = listing.status === "pending_seller_attributes" ? "submit" : "edit"`.
2. Se `!isEditableStatus(listing.status)`, renderizar um cartão de aviso no lugar do formulário: "Este anúncio está em `<status>` e não aceita correção de atributos agora." com link de volta ao detalhe. Isso evita o operador preencher a tela inteira para receber um 409.
3. Título: `mode === "edit" ? "Corrigir atributos" : "Atributos do produto"`; o parágrafo de apoio em modo `edit` vira "Corrija um valor já gravado. Salvar não avança a etapa do anúncio."
4. Estado `const [aviso, setAviso] = useState<AttributesEditResponse | null>(null)`, passado a `onEdited`, e `{aviso && <AttributeEditWarning listingId={id} result={aviso} />}` abaixo do `Card`.
5. Passar `mode` e `onEdited` ao `<AttributeForm />`.

- [ ] **Step 7: Detalhe do anúncio**

Em `frontend/src/app/(dashboard)/listings/[id]/page.tsx`:

1. Importar `isEditableStatus` e acrescentar, **depois** do bloco `status === "pending_seller_attributes"`, um cartão que aparece quando `isEditableStatus(status) && status !== "pending_seller_attributes"`:

```tsx
{isEditableStatus(status) && status !== "pending_seller_attributes" && (
  <Card>
    <CardHeader>
      <CardTitle className="text-base">Atributos do produto</CardTitle>
    </CardHeader>
    <CardContent>
      <p className="text-sm text-slate-600 mb-4">
        Corrija um valor já gravado. A correção não avança a etapa do anúncio.
      </p>
      <Button asChild variant="outline" className="w-full">
        <Link href={`/listings/${id}/attributes`}>Corrigir atributos</Link>
      </Button>
    </CardContent>
  </Card>
)}
```

2. Acrescentar uma mutation `regenerateDescriptionMutation` (mesmo formato de `generateImagesMutation`: `invalidateQueries` de `["listing", id]` e `["listings"]`, `toast.success("Descrição sendo refeita.")`, `toast.error` no erro) e, no bloco de `ready_to_publish`, um botão secundário "Regerar descrição" com `disabled` durante o `isPending`, e a linha de apoio "Use depois de corrigir um atributo: a descrição foi escrita uma vez só."

- [ ] **Step 8: Verificar tipos**

Run: `cd frontend && npx tsc --noEmit`
Expected: sem erros.

- [ ] **Step 9: Build**

Run: `cd frontend && npm run build`
Expected: build conclui sem erro.

- [ ] **Step 10: Commit**

```bash
git add frontend/src
git commit -m "feat(frontend): corrigir atributos em qualquer status editavel e regerar descricao"
```

---

## Self-review (feito)

**Cobertura do spec:** seção 1 → Task 1; seção 2 → Tasks 3 e 4; seção 3 → Task 2; seção 4 → Task 3 (`duplicated_fields`); seção 5 → Task 5; seção 6 → Task 6; seção 7 → Task 1 (docstring) e Task 3 (gravação); seção 8 → Task 3 (log); seção 9 → Task 7; seção 10 → Task 8. Os 10 testes obrigatórios listados no spec estão distribuídos em Tasks 1–8, cada um nomeado.

**Consistência de tipos:** `snapshot_attributes`/`stale_positions` (Task 2) são consumidos com a mesma assinatura na Task 3. `EDITABLE_ATTRIBUTE_STATUSES` e `REGENERABLE_POSITION_STATUSES` (Task 1) são consumidos nas Tasks 3 e 5. `AttributesEditResponse` (Task 4) é espelhado no TS da Task 8 com os mesmos três campos. `edit_attributes` devolve `tuple[list[int], list[str]]` na Task 3 e é desempacotado assim na Task 4.

**Conflito conhecido entre tasks:** a Task 5 altera um teste que a Task 3 não toca (`test_regenerar_posicao_service.py`) — está declarado no Step 1 da Task 5. A Task 7 e a Task 3 editam o mesmo arquivo (`listing_service.py`) em regiões distintas; executar na ordem do plano evita conflito.
