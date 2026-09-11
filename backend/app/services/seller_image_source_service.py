import logging

import httpx

logger = logging.getLogger(__name__)

# Minimo OBRIGATORIO. Faltando `{sku}-1` ou `{sku}-2` (em qualquer extensao de
# RAW_PHOTO_EXTENSIONS), o SKU e tratado como "sem fotos brutas": o listing
# vai para `pending_raw_photos` e espera as fotos chegarem (ver
# `raw_photo_standby_service`). Nao existe fallback de geracao.
RAW_PHOTOS_MIN = 2

# Teto de sondagem. As fotos sao descobertas por tentativa (nao ha listagem no
# bucket publico), entao precisa de um limite para nao sondar indefinidamente.
# 10 cobre com folga o seller de operacao madura sem custar mais que 8
# requisicoes extras no pior caso.
RAW_PHOTOS_MAX = 10

# Mantido por compatibilidade: era o teto rigido antigo, hoje e so o minimo.
RAW_PHOTOS_PER_SKU = RAW_PHOTOS_MIN

# Extensoes sondadas por posicao, NESTA ordem: a primeira que existir vence e
# as demais nem sao consultadas. Mistura de formato no mesmo SKU e' permitida
# (`{sku}-1.jpg` + `{sku}-2.png`). Sao os tres formatos que o motor de edicao
# (OpenAI /v1/images/edits, GPT image models) aceita como entrada; qualquer
# outro e' convertido para JPEG na hora da chamada, em `OpenAIEditEngine`.
# Custo: um indice AUSENTE agora custa 3 requests (uma por extensao) antes de
# encerrar a descoberta, em vez de 1.
RAW_PHOTO_EXTENSIONS = ("jpg", "png", "webp")


async def resolve_listing_skus(listing) -> list[str]:
    """Resolve a lista de SKUs componentes do anúncio. Hoje um anúncio sempre
    mapeia para exatamente 1 SKU; retorna uma lista para que os chamadores já
    estejam prontos para quando um projeto de kit fizer isso retornar mais
    de um SKU."""
    return [listing.sku_external_id] if listing.sku_external_id else []


async def fetch_raw_photos(raw_base_url: str, sku: str) -> list[bytes] | None:
    """Descobre e baixa as fotos brutas de um SKU no bucket do seller.

    Sonda `{sku}-1`, `-2`, `-3`... (cada indice em `RAW_PHOTO_EXTENSIONS`,
    nesta ordem) ate o primeiro ausente ou ate
    `RAW_PHOTOS_MAX`. O bucket e publico e sem listagem, entao descobrir por
    tentativa e a unica forma de saber quantas fotos existem.

    As `RAW_PHOTOS_MIN` primeiras sao OBRIGATORIAS: faltando qualquer uma
    delas, devolve None e o SKU e tratado como "sem fotos brutas" — mesmo
    comportamento de antes. Da terceira em diante sao BONUS: a ausencia
    encerra a descoberta sem invalidar nada.

    Seller de operacao madura pode ter 3-10 fotos por SKU; o teto rigido de 2
    que existia antes ignorava todas as extras.
    """
    photos: list[bytes] = []
    async with httpx.AsyncClient(timeout=15.0) as client:
        for n in range(1, RAW_PHOTOS_MAX + 1):
            obrigatoria = n <= RAW_PHOTOS_MIN
            content = await _buscar_indice(client, raw_base_url, sku, n)
            if content is None:
                if obrigatoria:
                    return None
                # Extra ausente (ou com falha de rede em todos os formatos):
                # encerra a descoberta e usa o que ja veio — melhor que
                # derrubar um SKU que tem o minimo.
                break
            photos.append(content)

    logger.info("raw_photos sku=%s encontradas=%s", sku, len(photos))
    return photos


async def _buscar_indice(client: httpx.AsyncClient, raw_base_url: str, sku: str, n: int) -> bytes | None:
    """Foto `n` do SKU em qualquer extensao aceita, na ordem de
    `RAW_PHOTO_EXTENSIONS`: a primeira que responder 200 vence, e as demais
    nem sao sondadas. Falha de rede num formato conta como "nao e' este" e
    passa ao proximo; so devolve None se nenhum formato existir."""
    for ext in RAW_PHOTO_EXTENSIONS:
        url = f"{raw_base_url}/{sku}-{n}.{ext}"
        try:
            resp = await client.get(url)
        except httpx.HTTPError as exc:
            logger.warning(
                "raw_photos sku=%s n=%s ext=%s result=erro_rede reason=%s", sku, n, ext, exc,
            )
            continue
        if resp.status_code == 200:
            return resp.content
    return None


async def fetch_all_raw_photos(raw_base_url: str, skus: list[str]) -> dict[str, list[bytes]] | None:
    """Busca as fotos brutas de todos os SKUs da lista. Tudo ou nada: retorna
    None se QUALQUER SKU estiver sem o minimo de fotos brutas."""
    result: dict[str, list[bytes]] = {}
    for sku in skus:
        photos = await fetch_raw_photos(raw_base_url, sku)
        if photos is None:
            return None
        result[sku] = photos
    return result


# Posição 4 do esquema de 5 posições — "Detalhes".
# Ver docs/superpowers/specs/esquema-5-posicoes.md.
#
# Regra deliberadamente simples: a 3ª foto, se existir. Não há heurística de
# "qual extra é a melhor para detalhe" e não deve haver por ora — decidir isso
# sem dado real de quantos sellers têm 3+ fotos seria inventar critério. É
# revisitável quando esse dado existir.
DETAIL_SOURCE_INDEX = 2  # 0-based: a 3ª foto


def pick_detail_source(photos: list[bytes]) -> tuple[bytes, bool]:
    """Escolhe a foto de origem da posição 4 ("Detalhes").

    Devolve `(foto, veio_de_extra)`. O segundo elemento importa: `True`
    significa fonte dedicada (uma foto que o seller subiu além do mínimo e que
    NÃO alimenta a posição 2); `False` significa que só existem as 2 mínimas e
    estamos reaproveitando — o chamador pode decidir tratar diferente, ou até
    pular a posição 4, em vez de forçar um "detalhe" que a foto não mostra.

    Não faz zoom nem recorte: só seleciona a fonte. O tratamento é do chamador.
    """
    if not photos:
        raise ValueError("pick_detail_source exige ao menos uma foto")

    if len(photos) > DETAIL_SOURCE_INDEX:
        return photos[DETAIL_SOURCE_INDEX], True

    # Só o mínimo: usa a última disponível. A posição 2 tende a sair da 1ª, então
    # pegar a última evita que as duas posições mostrem exatamente a mesma foto.
    return photos[-1], False
