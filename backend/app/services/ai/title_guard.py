"""Titulo nunca termina com palavra ou unidade partida ao meio.

O que havia antes: `parsed.get("title", "").strip()[:60]` nos dois provedores.
Uma fatia cega. Um titulo de 61 caracteres — UM a mais que o alvo — virava um
de 60 com a ultima palavra mutilada, sem log, sem erro, sem sinal nenhum.

Vitima real: `MLB7638983316` (SKU 31, categoria MLB6284) foi ao ar com
"Body Splash Desodorante Colônia Liberté Exclusif Wepink 200m" — o "l" de
"200ml" comido pela fatia. O modelo tinha escrito o titulo certo.

A politica, em duas etapas:

1. Estourou? Pede de novo ao modelo UMA vez, dizendo por quantos caracteres
   passou. E a unica opcao que devolve um titulo BOM: no caso do SKU 31,
   "Body Splash Desodorante Colônia Liberté Wepink 200ml" (52) preserva os
   tres termos de tipo de produto, a marca e o volume.
2. Estourou de novo? Corta na ultima palavra INTEIRA e registra em log. Fica
   mais curto — o SKU 31 perderia o "200ml" — mas nunca fragmentado.

Nenhuma etapa inventa texto: o corte so remove, e o resultado e' sempre um
prefixo da origem.

## Por que 60 e nao o limite do ML

60 e' ALVO DE SEO NOSSO, nao limite do Mercado Livre. O limite real e' por
categoria, em `settings.max_title_length`: 60 na maioria, mas **150 em
MLB6284 (Perfumes)**, que e' a categoria da maior parte destes produtos.

Nao da' para usar o limite real na geracao: o titulo e' gerado ANTES da
categoria ser prevista, e a categoria e' prevista A PARTIR do titulo
(`category_service._discover`). Usar `max_title_length` exigiria gerar,
prever e gerar de novo — outra chamada paga por anuncio. Decisao de 2026-09-15
(Daniel): 60 fica como alvo; reavaliar quando houver volume para comparar
venda. Ha 90 caracteres de folga nao usados em perfumaria.
"""
import logging

logger = logging.getLogger(__name__)

# Alvo de geracao. Ver o cabecalho: NAO e' o limite do ML.
TITLE_TARGET_CHARS = 60

# Separadores que nao podem sobrar pendurados no fim depois do corte. O espaco
# entra no conjunto de proposito: um `rstrip` so resolve os dois casos.
_SOBRAS = " -–—,;:/|"


def excedente(titulo: str, limite: int = TITLE_TARGET_CHARS) -> int:
    """Quantos caracteres passam do limite. Zero (ou menos) quando cabe."""
    return len((titulo or "").strip()) - limite


def cortar_na_ultima_palavra(titulo: str, limite: int = TITLE_TARGET_CHARS) -> str:
    """Encurta ate `limite` sem nunca partir uma palavra.

    Corta na ultima fronteira de palavra que couber e limpa separador solto no
    fim ("... Wepink -" nao e' titulo). O resultado e' sempre um PREFIXO da
    origem: esta funcao so remove, nunca acrescenta.
    """
    texto = (titulo or "").strip()
    if len(texto) <= limite:
        return texto

    cortado = texto[:limite]
    # O caractere seguinte ao corte ser espaco significa que a fatia caiu
    # exatamente numa fronteira: a ultima palavra ja esta inteira.
    if not texto[limite].isspace():
        fronteira = cortado.rfind(" ")
        if fronteira == -1:
            # Nao ha onde cortar: o titulo e' uma palavra so, maior que o
            # limite. Unico caso em que sobra fragmento. Entrada patologica —
            # avisa alto em vez de derrubar o anuncio inteiro.
            logger.warning(
                "titulo_cortado result=sem_fronteira limite=%d de=%r",
                limite, texto,
            )
            return cortado
        cortado = cortado[:fronteira]

    limpo = cortado.rstrip(_SOBRAS)
    return limpo or cortado


def _texto(item) -> str:
    if not isinstance(item, dict):
        return ""
    valor = item.get("title")
    return valor.strip() if isinstance(valor, str) else ""


def _algum_excede(titulos, limite: int) -> bool:
    return any(excedente(_texto(t), limite) > 0 for t in titulos)


async def aplicar_limite(titulos: list, *, retentar=None, limite: int = TITLE_TARGET_CHARS) -> list:
    """Garante que nenhum titulo passe de `limite` nem termine partido.

    `retentar` e' uma corrotina SEM argumentos que devolve uma lista nova de
    titulos (mesma forma) ou `None`. Ela so e' chamada quando algum titulo
    estoura — o caminho comum nao paga chamada nenhuma. E' chamada no maximo
    UMA vez por geracao: o custo extra e' limitado e previsivel.

    Falha do `retentar` (provedor fora, JSON invalido) nao propaga: cai no
    corte, que e' o piso garantido. Um titulo curto e' melhor que um anuncio
    em `failed`.
    """
    if not _algum_excede(titulos, limite):
        return titulos

    if retentar is not None:
        novos = None
        try:
            novos = await retentar()
        except Exception as exc:
            logger.warning("titulo_retentativa result=falhou reason=%s", exc)
        if novos:
            if not _algum_excede(novos, limite):
                logger.info("titulo_retentativa result=coube")
                return novos
            # A segunda resposta tambem estourou: e' nela que o corte age —
            # e' a tentativa mais recente do modelo, com o aviso na mao.
            titulos = novos

    ajustados = []
    for item in titulos:
        texto = _texto(item)
        excedeu = excedente(texto, limite)
        if excedeu <= 0:
            ajustados.append(item)
            continue
        cortado = cortar_na_ultima_palavra(texto, limite)
        logger.warning(
            "titulo_cortado excedeu=%d de=%r para=%r",
            excedeu, texto, cortado,
        )
        ajustados.append({**item, "title": cortado})
    return ajustados
