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

    A sondagem em si (`_descobrir`) e' a MESMA que `discover_raw_photo_urls`
    usa: o que muda e' so o que se guarda de cada acerto (bytes aqui, URL la).
    """
    async with httpx.AsyncClient(timeout=15.0) as client:
        achados = await _descobrir(client, raw_base_url, sku, baixar=True)

    # Parar no primeiro indice ausente significa que `achados` e' sempre o
    # prefixo 1..k; faltou uma obrigatoria <=> k < RAW_PHOTOS_MIN.
    if len(achados) < RAW_PHOTOS_MIN:
        return None
    photos = [content for _url, content in achados]
    logger.info("raw_photos sku=%s encontradas=%s", sku, len(photos))
    return photos


async def discover_raw_photo_urls(raw_base_url: str, sku: str) -> list[str]:
    """URLs das fotos brutas de um SKU, em ordem, SEM baixar nenhuma: o bucket
    e' publico e quem le a imagem e' o navegador (botao "ver original" da
    revisao). Nao exige minimo — devolve o que existir, inclusive nada.

    Usa a mesma sondagem de `fetch_raw_photos` (`_descobrir`): mesmo teto
    `RAW_PHOTOS_MAX`, mesmas `RAW_PHOTO_EXTENSIONS` na mesma ordem. Cada
    acerto custa 1 requisicao (a primeira extensao que existe vence); o
    indice ausente que encerra custa `len(RAW_PHOTO_EXTENSIONS)`. A sondagem
    e' GET em streaming fechado logo apos o status, sem ler o corpo.

    A descoberta por sondagem e' herdada de `seller_image_source_service` e
    tem dois limites conhecidos: custa ate `RAW_PHOTOS_MAX` x
    `len(RAW_PHOTO_EXTENSIONS)` requisicoes ao bucket, e para no primeiro
    indice ausente — um SKU com `-1`, `-2` e `-4` nunca mostra a quarta foto.
    Listar de verdade e' possivel pela API S3 do R2 com credencial de leitura
    (o projeto ja usa `boto3` para o bucket de ativos), e ficou para uma
    tarefa propria: ela toca o caminho de geracao de imagens em producao e
    nao deve entrar de carona aqui.
    """
    async with httpx.AsyncClient(timeout=15.0) as client:
        achados = await _descobrir(client, raw_base_url, sku, baixar=False)
    urls = [url for url, _content in achados]
    logger.info("raw_photo_urls sku=%s encontradas=%s", sku, len(urls))
    return urls


async def _descobrir(
    client: httpx.AsyncClient, raw_base_url: str, sku: str, *, baixar: bool
) -> list[tuple[str, bytes | None]]:
    """Sonda `{sku}-1`, `-2`... ate o primeiro indice ausente ou ate
    `RAW_PHOTOS_MAX`, e devolve `[(url, bytes | None)]` na ordem. E' o UNICO
    lugar que conhece o teto e o laco de indices; `_sondar_indice` e' o unico
    que conhece as extensoes."""
    achados: list[tuple[str, bytes | None]] = []
    for n in range(1, RAW_PHOTOS_MAX + 1):
        achado = await _sondar_indice(client, raw_base_url, sku, n, baixar=baixar)
        if achado is None:
            break
        achados.append(achado)
    return achados


async def _buscar_indice(client: httpx.AsyncClient, raw_base_url: str, sku: str, n: int) -> bytes | None:
    """Foto `n` do SKU (bytes) em qualquer extensao aceita — ver `_sondar_indice`."""
    achado = await _sondar_indice(client, raw_base_url, sku, n, baixar=True)
    return achado[1] if achado else None


async def _sondar_indice(
    client: httpx.AsyncClient, raw_base_url: str, sku: str, n: int, *, baixar: bool
) -> tuple[str, bytes | None] | None:
    """Foto `n` do SKU em qualquer extensao aceita, na ordem de
    `RAW_PHOTO_EXTENSIONS`: a primeira que responder 200 vence, e as demais
    nem sao sondadas. Falha de rede num formato conta como "nao e' este" e
    passa ao proximo; so devolve None se nenhum formato existir.

    `baixar=True` faz GET e devolve `(url, bytes)`; `baixar=False` abre o GET
    em streaming e fecha logo apos o status, devolvendo `(url, None)` — mesma
    semantica de existencia, sem transferir a foto."""
    for ext in RAW_PHOTO_EXTENSIONS:
        url = f"{raw_base_url}/{sku}-{n}.{ext}"
        try:
            if baixar:
                resp = await client.get(url)
                if resp.status_code == 200:
                    return url, resp.content
            else:
                async with client.stream("GET", url) as resp:
                    if resp.status_code == 200:
                        return url, None
        except httpx.HTTPError as exc:
            logger.warning(
                "raw_photos sku=%s n=%s ext=%s result=erro_rede reason=%s", sku, n, ext, exc,
            )
            continue
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
