"""Perfil de conteudo por categoria-FOLHA para o esquema de 5 posicoes.

Cada entrada carrega DUAS coisas: o formato do canvas e o conteudo especifico
daquela vertical. E o que permite acrescentar um perfil novo (Moda em 4:5,
por exemplo) como uma linha a mais, sem reescrever a orquestracao — o worker
le `canvas` e as legendas do perfil em vez de ter valores cravados.

TODA categoria tem perfil. `profile_for_category` NUNCA devolve None (desde
2026-09-10): categoria sem perfil proprio cai em `PERFIL_PADRAO`, o generico
— mesmo canvas quadrado, legendas de detalhe neutras. Antes, None acionava o
pipeline antigo (individuais + cards Pillow), que em LOTE auto-aprovava e
publicava; esse caminho foi removido, nao deixado dormente.

CHAVEADO PELA FOLHA, NAO PELA RAIZ. A raiz de MLB6284 e
`MLB1246 Beleza e Cuidado Pessoal`, que tem 13 filhas — Maquiagem, Cuidados
com o Cabelo, Manicure, Farmacia, Depilacao... Um perfil "Perfumaria"
chaveado na raiz aplicaria "Frasco elegante" a esmalte e alcool em gel.
Categoria sem perfil cadastrado NAO herda o da irma nem o da raiz: recebe o
generico, e ganha perfil proprio quando houver SKU real para lapidar.

Existe uma segunda categoria-folha chamada "Perfumes" no ML, a MLB178938
(`Pet Shop > Caes > Higiene e Limpeza > Artigos para os Pelos > Perfumes`).
Ela NAO esta cadastrada de proposito: e perfume para caes, tem vocabulario de
atributos proprio — e a origem do caso "Colonia" documentado no CLAUDE.md,
valor que existe la e nao em MLB6284 — e nenhum SKU dela foi testado. Hoje
cai no generico; cadastra-se um perfil quando houver um SKU real para validar.
"""
from dataclasses import dataclass

# Canvas do esquema. Quadrado, e nao vertical, porque o Mercado Livre
# recomenda 1200x1200 para a maioria das categorias (Beleza inclusive); o 4:5
# vertical e recomendacao de Moda/Vestuario, outra categoria. Como
# `normalize_to_square` RECORTA o centro (nao adiciona borda), um canvas
# vertical perderia silenciosamente o painel de texto das posicoes 2 a 4 — o
# texto que justifica a existencia delas — e ainda passaria no QA.
CANVAS_QUADRADO = "1200x1200"


@dataclass(frozen=True)
class PositionProfile:
    """Formato + conteudo de uma vertical.

    `detail_captions` alimenta a posicao 4. Sao legendas GENERICAS e fixas, de
    proposito: a posicao 4 e um close de textura/acabamento, e uma legenda
    especifica ("tampa dourada") viraria afirmacao sobre o produto que nada no
    catalogo sustenta. Escolha deterministica (ver `detail_caption_for`), nao
    LLM e nao aleatoria — duas execucoes do mesmo SKU tem de dar a mesma
    legenda, senao a revisao humana nao vale para a proxima geracao.
    """

    nome: str
    canvas: str
    detail_captions: tuple[str, ...]


PERFIL_PERFUMARIA = PositionProfile(
    nome="Perfumaria/Body Splash",
    canvas=CANVAS_QUADRADO,
    detail_captions=(
        "Frasco elegante",
        "Acabamento refinado",
        "Textura premium",
        "Visual sofisticado",
    ),
)

# Generico: vale para qualquer produto fisico, de farol a caderno. Nenhuma
# palavra de vertical (frasco, fragrancia, tecido...) — a legenda so pode
# afirmar o que um close de qualquer objeto mostra.
PERFIL_PADRAO = PositionProfile(
    nome="Padrao",
    canvas=CANVAS_QUADRADO,
    detail_captions=(
        "Detalhe do produto",
        "Acabamento em destaque",
        "Vista aproximada",
        "Textura e materiais",
    ),
)


PROFILES_BY_LEAF_CATEGORY: dict[str, PositionProfile] = {
    "MLB6284": PERFIL_PERFUMARIA,
}


def profile_for_category(category_id: str | None) -> PositionProfile:
    """Perfil da categoria-folha; `PERFIL_PADRAO` se ela nao tiver um proprio.

    Nunca None: toda categoria passa pelo esquema de 5 posicoes. O perfil
    especifico, quando existe, e' um refinamento por vertical — nao uma
    condicao para gerar.
    """
    if not category_id:
        return PERFIL_PADRAO
    return PROFILES_BY_LEAF_CATEGORY.get(category_id, PERFIL_PADRAO)


def detail_caption_for(profile: PositionProfile, sku: str) -> str:
    """Legenda da posicao 4, estavel para o mesmo SKU.

    Deriva o indice do proprio SKU em vez de sortear: regenerar as imagens de
    um anuncio nao pode trocar a legenda por baixo de uma revisao humana que
    ja aconteceu. SKUs diferentes recebem legendas diferentes, o que evita
    todo anuncio da vitrine sair com a mesma frase.
    """
    if not profile.detail_captions:
        raise ValueError("perfil sem legendas de detalhe")
    indice = sum(ord(c) for c in str(sku)) % len(profile.detail_captions)
    return profile.detail_captions[indice]
