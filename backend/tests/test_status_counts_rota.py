"""Testes sem banco (sempre rodam) para a Task 1 do plano
`2026-09-11-listagem-em-escala`: a lista canonica de status
(`LISTING_STATUSES`) e o roteamento da rota `GET /listings/status-counts`.

Nao precisa de Postgres: e' so metadado do FastAPI (ordem de `app.routes`) e
a constante de modulo. Roda sempre, mesmo sem `TEST_DATABASE_URL`.
"""


def test_status_counts_declarada_antes_do_detalhe():
    """FastAPI casa rotas na ordem em que foram declaradas no router. Se
    `GET /listings/{listing_id}` viesse ANTES de `GET /listings/status-counts`,
    uma requisicao para `/listings/status-counts` cairia no parametro
    `{listing_id}` (tentando converter a string "status-counts" para UUID) e
    devolveria 422 em vez do resumo por status. Este teste trava a ordem de
    declaracao em `endpoints/listings.py` para que uma reordenacao futura
    quebre a suite em vez de quebrar em producao."""
    from app.main import app

    caminhos_get = [
        route.path
        for route in app.routes
        if getattr(route, "path", None) and "GET" in getattr(route, "methods", set())
    ]

    idx_status_counts = caminhos_get.index("/api/v1/listings/status-counts")
    idx_detalhe = caminhos_get.index("/api/v1/listings/{listing_id}")

    assert idx_status_counts < idx_detalhe, (
        "status-counts precisa ser declarada ANTES de /{listing_id}, "
        "senao FastAPI tenta converter 'status-counts' em UUID e devolve 422"
    )


def test_lista_canonica_tem_16_status_sem_repeticao():
    """`LISTING_STATUSES` e' a lista que a barra de resumo da fila usa: cada
    status aparece SEMPRE na contagem, com zero quando nao ha anuncio, pra
    barra nao mudar de tamanho a cada atualizacao. Precisa ter exatamente os
    16 status do pipeline, sem repeticao e sem faltar nenhum."""
    from app.models.listing import LISTING_STATUSES

    esperado = {
        "draft",
        "generating_title",
        "pending_title_approval",
        "predicting_category",
        "pending_seller_attributes",
        "pending_description",
        "generating_images",
        "pending_raw_photos",
        "pending_ai_engine",
        "pending_image_approval",
        "generating_description",
        "ready_to_publish",
        "publishing",
        "published",
        "published_paused",
        "failed",
    }

    assert len(LISTING_STATUSES) == 16, LISTING_STATUSES
    assert len(set(LISTING_STATUSES)) == 16, "nao pode ter status repetido"
    assert set(LISTING_STATUSES) == esperado, set(LISTING_STATUSES) ^ esperado
