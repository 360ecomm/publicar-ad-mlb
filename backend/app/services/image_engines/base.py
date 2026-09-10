class ImageEngineUnavailableError(Exception):
    """Erro de infraestrutura (timeout, HTTP 5xx ou 429) do motor de edicao
    de imagem (`OpenAIEditEngine`). Os endpoints de variante sob demanda
    (`cover-ai-variant`, `specs-ai-variant`) o traduzem em 503 em vez de
    500 generico; no pipeline de 5 posicoes, `_tentar` o trata como falha
    transiente e refaz a posicao.

    O restante deste modulo (ImageEngineProvider, ImageRateLimitError,
    PROMPT_SUFFIX) servia so ao texto-imagem antigo, removido em 2026-09-10
    junto com o subsistema de troca de motor."""
