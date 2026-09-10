"""Marca real vs placeholder, decidido em UM lugar.

Caso real (SKU 45, 2026-09-10): produto sem marca no catalogo virava
`sku_brand="Sem marca"` no listing, e a posicao 1 (apresentacao) imprimiu
"Sem marca" como segunda linha do card. O prefill de BRAND ja filtrava o
placeholder, mas so ali — cada consumidor decidia por conta propria.

Regra: `real_brand` devolve a marca limpa ou None. Todo consumidor de
`sku_brand` (prefill de atributo, prompts de titulo/descricao/copy, campos
das posicoes) passa por aqui e OMITE a marca quando None — nunca imprime
vazio nem placeholder. O lote grava "" (a coluna e' NOT NULL) quando o
produto nao tem marca; quem precisa de texto amigavel aplica no ponto de uso.
"""

_PLACEHOLDERS = frozenset({"sem marca", "no brand", "n/a", "na", "-", "none", "null"})


def real_brand(value) -> str | None:
    if value is None:
        return None
    limpo = str(value).strip()
    if not limpo or limpo.lower() in _PLACEHOLDERS:
        return None
    return limpo
