"""Copy (titulo + bullets) dos 3 cards de imagem, gerada por LLM.

Modulo separado de `image_deterministic_service.py` de proposito: aquele
modulo e sobre recorte de pixels, este e sobre texto. Nao ha relacao entre
os dois alem de ambos alimentarem o mesmo pipeline de imagens.

Roda ANTES de `generate_description` no pipeline (SPEC-006), entao a
materia-prima disponivel e so o titulo escolhido, a descricao do ERP, marca,
modelo e os atributos ja confirmados — nada de descricao gerada, nada de
preco/estoque. O prompt (`build_card_copy_prompt`) carrega a regra de nao
inventar especificacao tecnica porque esse material de origem e magro.

O servico NAO confia no LLM: toda resposta passa por saneamento estrito
antes de virar `CardCopy`. Qualquer falha — do provider, de rede, de JSON,
de formato — e engolida e vira lista vazia, nunca excecao. O anuncio tem que
seguir publicavel mesmo sem os cards.
"""
import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Ordem = sort_order das imagens de card no anuncio.
CARD_KINDS = ("card_benefits", "card_usage", "card_specs")

_KEY_BY_KIND = {
    "card_benefits": "benefits",
    "card_usage": "usage",
    "card_specs": "specs",
}

MAX_TITLE_CHARS = 40
MAX_BULLET_CHARS = 50
MIN_BULLETS = 2
MAX_BULLETS = 3

# Unidades de medida legitimas — servem so pra impedir que o padrao de preco
# coma uma especificacao real ("12,50 m", "3,50 cm", "1,25 l").
_UNIDADES = r"(?:cm|mm|km|kg|mg|ml|hz|pol|un|[mglvw]|%|\"|')"

# Conteudo que o Mercado Livre nao aceita dentro da imagem do anuncio: preco,
# contato, promessa de entrega e superlativo nao comprovavel. O prompt ja
# proibe tudo isso, mas prompt e conselho — em modo batch a imagem e
# auto-aprovada e vai pro ar sem revisao humana, entao o servico tambem
# desconfia do CONTEUDO, do mesmo jeito que ja desconfia do TAMANHO.
#
# Padroes propositalmente estreitos: derrubar um bullet legitimo ("12V",
# "3,5cm", "500ml", "1,5 m") custa mais do que deixar passar um caso de borda
# raro. Menção a concorrente nao da pra detectar por regex e continua so no
# prompt.
_BANNED_PATTERNS: tuple[tuple[str, re.Pattern], ...] = (
    ("preco_moeda", re.compile(r"r\$", re.IGNORECASE)),
    # Decimal de 2 casas que NAO e seguido de unidade: "199,90" cai,
    # "12,50 m" e "3,50cm" passam. "3,5cm" nem chega aqui (1 casa decimal).
    ("preco_numerico", re.compile(
        rf"\d{{2,}}[.,]\d{{2}}\b(?!\s*{_UNIDADES}\b)", re.IGNORECASE)),
    ("url", re.compile(r"https?://|www\.", re.IGNORECASE)),
    ("telefone", re.compile(r"\(\d{2}\)\s?\d{4,5}")),
    ("promessa_de_entrega", re.compile(r"frete\s+gr[áa]tis", re.IGNORECASE)),
    ("superlativo", re.compile(r"melhor\s+do\s+mercado", re.IGNORECASE)),
)


def _banned_reason(text: str) -> str | None:
    """Nome do padrao proibido encontrado em `text`, ou None se estiver limpo.

    Roda no texto CRU, antes do truncamento: se o LLM escreveu preco, a
    intencao dele e o que importa: truncar o preco pra fora nao pode servir
    de lavagem pro resto do bullet.
    """
    for name, pattern in _BANNED_PATTERNS:
        if pattern.search(text):
            return name
    return None


@dataclass(frozen=True)
class CardCopy:
    kind: str          # um de CARD_KINDS
    title: str
    bullets: list[str]


def _truncate(text: str, max_chars: int) -> str:
    """Corta `text` em `max_chars`, preferindo o limite de palavra.

    Se der para cortar num espaco dentro do limite, corta ali (sem deixar
    fragmento de palavra pendurado). Se a primeira "palavra" sozinha ja
    estoura o limite, corta seco — sem reticencias, pra nao deixar marcador
    de truncamento orfao no meio da palavra.
    """
    text = text.strip()
    if len(text) <= max_chars:
        return text
    truncated = text[:max_chars]
    last_space = truncated.rfind(" ")
    if last_space > 0:
        return truncated[:last_space].rstrip()
    return truncated


def _build_source(listing, attributes: list | None) -> dict:
    """Monta o dicionario de origem para o prompt de copy dos cards.

    Só o que já existe nesse ponto do pipeline: titulo escolhido, descricao
    do ERP, marca, modelo e atributos com valor confirmado. Sem preco nem
    estoque — nao tem relacao com a copy dos cards e so daria mais material
    para o LLM tentar (erradamente) transformar em "beneficio".
    """
    attrs = [
        {"attribute_name": a.attribute_name, "value_name": a.value_name}
        for a in (attributes or [])
        if getattr(a, "value_name", None)
    ]
    return {
        "selected_title": listing.selected_title,
        "sku_description": listing.sku_description,
        "sku_brand": listing.sku_brand,
        "sku_model": listing.sku_model,
        "attributes": attrs,
    }


def _sanitize_angle(kind: str, raw_angle) -> CardCopy | None:
    """Valida e normaliza um angulo (benefits/usage/specs) devolvido pelo LLM.

    Devolve None (angulo descartado) para qualquer coisa que nao vira um
    card publicavel — melhor um card a menos do que um card com titulo vazio
    ou bullet apagado.
    """
    if not isinstance(raw_angle, dict):
        logger.info("card_copy kind=%s result=dropped reason=formato_invalido", kind)
        return None

    title = raw_angle.get("title")
    if not isinstance(title, str) or not title.strip():
        logger.info("card_copy kind=%s result=dropped reason=titulo_vazio", kind)
        return None
    title = title.strip()
    # Titulo proibido derruba o angulo inteiro: nao existe card sem titulo
    # (titulo vazio ja e motivo de descarte logo acima), entao nao ha o que
    # salvar como ha nos bullets.
    padrao = _banned_reason(title)
    if padrao is not None:
        logger.info(
            "card_copy kind=%s result=dropped alvo=titulo reason=conteudo_proibido padrao=%s",
            kind, padrao,
        )
        return None
    title = _truncate(title, MAX_TITLE_CHARS)

    bullets_raw = raw_angle.get("bullets")
    bullets: list[str] = []
    if isinstance(bullets_raw, list):
        for item in bullets_raw:
            if not isinstance(item, str) or not item.strip():
                continue
            padrao = _banned_reason(item)
            if padrao is not None:
                # So o bullet cai; o angulo sobrevive se ainda sobrarem
                # MIN_BULLETS — a checagem logo abaixo cuida disso.
                logger.info(
                    "card_copy kind=%s result=dropped alvo=bullet reason=conteudo_proibido padrao=%s",
                    kind, padrao,
                )
                continue
            bullets.append(_truncate(item, MAX_BULLET_CHARS))
            if len(bullets) >= MAX_BULLETS:
                break

    if len(bullets) < MIN_BULLETS:
        logger.info(
            "card_copy kind=%s result=dropped reason=bullets_insuficientes count=%s",
            kind, len(bullets),
        )
        return None

    return CardCopy(kind=kind, title=title, bullets=bullets)


SPECS_CARD_TITLE = "Especificações Técnicas"

# Atributos que existem no anuncio mas nao descrevem o produto na vitrine:
# identificadores internos, dado fiscal/logistico e o obvio ("Condicao: Novo").
# Ocupariam um dos 3 bullets no lugar do que o comprador quer ler.
SPECS_EXCLUDED_ATTRIBUTE_IDS = frozenset({
    "SELLER_SKU",
    "GTIN",
    "EMPTY_GTIN_REASON",
    "ITEM_CONDITION",
    "IS_FLAMMABLE",
    "SELLER_PACKAGE_WEIGHT",
    "SELLER_PACKAGE_LENGTH",
    "SELLER_PACKAGE_WIDTH",
    "SELLER_PACKAGE_HEIGHT",
})

# Marca e modelo primeiro; o resto entra por ordem alfabetica de
# `attribute_id`. A ordenacao explicita nao e cosmetica: e o que torna a saida
# reproduzivel, que e o requisito desta correcao.
SPECS_PRIORITY_ATTRIBUTE_IDS = ("BRAND", "MODEL")


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


def build_specs_card(attributes: list | None) -> CardCopy | None:
    """Ficha tecnica montada a partir dos atributos, sem LLM nenhum.

    Ficha tecnica e dado estruturado — o `value_name` do atributo JA e a
    resposta certa. Deixar o LLM redigir esse texto transformava um dado exato
    num palpite: no anuncio do SKU 37 o tipo saiu "Desodorante colonia" numa
    execucao e "Agua de colonia" (o valor real) em outra, com o mesmo prompt.
    O card Pillow que vai ao ar em todo anuncio consome a mesma funcao, entao
    o risco nunca foi so do candidato de IA.

    Cada bullet e `"{attribute_name}: {value_name}"`, com o `value_name`
    LITERAL. Bullet que nao cabe em `MAX_BULLET_CHARS` e DESCARTADO, nunca
    truncado: meio valor ("Agua de colo") deixa de ser o value_name, e a
    garantia desta funcao e justamente a exatidao.

    Devolve None quando nao sobram `MIN_BULLETS` — melhor nenhum card do que
    uma ficha de uma linha so. `card_benefits` e `card_usage` continuam com o
    LLM: aquilo e narrativa, isto e dado.
    """
    candidatos = specs_candidate_attributes(attributes)

    prioridade = {aid: i for i, aid in enumerate(SPECS_PRIORITY_ATTRIBUTE_IDS)}
    candidatos.sort(
        key=lambda a: (
            prioridade.get(a.attribute_id, len(prioridade)),
            a.attribute_id,
        )
    )

    bullets: list[str] = []
    valores_ja_usados: set[str] = set()

    for attr in candidatos:
        valor = str(attr.value_name).strip()
        # Um valor repetido por outro atributo (PERFUME_NAME repetindo MODEL)
        # gastaria um bullet para dizer a mesma coisa duas vezes, empurrando
        # para fora um atributo que ainda nao apareceu.
        if valor.casefold() in valores_ja_usados:
            continue

        bullet = f"{attr.attribute_name}: {valor}"
        if len(bullet) > MAX_BULLET_CHARS:
            continue
        # O ML proibe certo conteudo dentro da imagem independentemente de quem
        # escreveu o texto — um valor de atributo nao esta isento da regra.
        if _banned_reason(bullet) is not None:
            logger.info(
                "specs_card attribute_id=%s result=dropped reason=conteudo_proibido",
                attr.attribute_id,
            )
            continue

        bullets.append(bullet)
        valores_ja_usados.add(valor.casefold())
        if len(bullets) >= MAX_BULLETS:
            break

    if len(bullets) < MIN_BULLETS:
        logger.info(
            "specs_card result=dropped reason=bullets_insuficientes count=%s",
            len(bullets),
        )
        return None

    return CardCopy(kind="card_specs", title=SPECS_CARD_TITLE, bullets=bullets)


async def generate_card_copy(listing, attributes: list | None = None) -> list[CardCopy]:
    """Copy dos 3 cards, na ordem de CARD_KINDS.

    Resiliente por angulo: um angulo ruim e descartado, os outros seguem.
    Resiliente no todo: qualquer excecao (provider, HTTP, JSON, chave
    faltando) vira log + lista vazia. Nunca levanta excecao — o pipeline de
    imagens nao pode travar por causa de copy de card.
    """
    from app.services.ai.service import get_ai_provider

    try:
        source = _build_source(listing, attributes)
        provider = get_ai_provider()
        raw = await provider.generate_card_copy(source)
    except Exception as exc:
        # exc_info: a excecao morre aqui por design, entao o traceback neste
        # log e a unica forma de saber em producao se foi provider, rede,
        # JSON ou chave faltando — `reason=%s` sozinho nao distingue.
        logger.warning(
            "card_copy listing_id=%s result=failed reason=%s",
            getattr(listing, "id", None), exc,
            exc_info=True,
        )
        return []

    if not isinstance(raw, dict):
        logger.warning(
            "card_copy listing_id=%s result=failed reason=resposta_nao_e_dict",
            getattr(listing, "id", None),
        )
        return []

    results: list[CardCopy] = []
    for kind in CARD_KINDS:
        if kind == "card_specs":
            # Ficha tecnica NAO vem do LLM: o angulo `specs` da resposta e
            # ignorado de proposito. Ver `build_specs_card`.
            angle = build_specs_card(attributes)
        else:
            angle = _sanitize_angle(kind, raw.get(_KEY_BY_KIND[kind]))
        if angle is not None:
            results.append(angle)
    return results
