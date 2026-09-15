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
