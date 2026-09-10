"""Configuração compartilhada da suíte.

O bloqueio de rede é o ponto central deste arquivo: nenhum teste deve sair
para a internet. Um teste que chama a API do Gemini/OpenAI/ML de verdade é
lento, custa dinheiro, falha sem internet e — pior — passa por acidente
quando deveria estar exercitando um mock. Aqui a falha é explícita e diz
qual URL escapou.

O patch é aplicado na camada de **transporte**, não no `AsyncClient`, porque
é o transporte que de fato abre o socket. Isso mantém funcionando tudo que
já é legítimo hoje:

- teste que faz `patch("...httpx.AsyncClient")` ou mocka `client.post` →
  nunca chega no transporte;
- `ASGITransport` (usado em `test_health.py` para falar com o app FastAPI
  em memória) é outra classe, não é interceptada;
- `httpx.MockTransport`, se alguém usar, idem.

Ou seja: só quebra o que realmente tentaria sair para a rede.
"""
import pytest

# ── Celery em memória ────────────────────────────────────────────────────────
#
# O `celery_app` nasce com `broker=settings.redis_url`: o Redis REAL do
# ambiente onde a suíte roda. Testes que exercitam o dispatch de verdade
# (`.delay()` / `chain(...).delay()` sem patch) publicavam tasks nesse Redis —
# 8 `generate_description` por rodada, medido com o worker parado. Em
# produção, onde a suíte roda dentro da imagem depois do deploy, o worker
# consumia essas tasks e falhava com NoResultFound (ids fictícios). Foi
# inofensivo por acaso, não por design.
#
# Transporte `memory://` do kombu: publicar continua funcionando (o código de
# produção não muda), mas a mensagem fica num dicionário deste processo e
# morre com ele. Não é `task_always_eager`: eager EXECUTARIA a task inline,
# contra os mocks do teste — o oposto do que queremos.
#
# Precisa acontecer no import do conftest, antes de qualquer módulo de teste
# importar tasks: a conexão do Celery é preguiçosa, então trocar a conf aqui
# vale para toda publicação feita depois. `test_celery_isolado.py` confere.
from app.workers.celery_app import celery_app

celery_app.conf.update(
    broker_url="memory://",
    result_backend="cache+memory://",
    task_always_eager=False,
    broker_connection_retry_on_startup=False,
)

_ALLOW_MARK = "allow_network"


class NetworkAccessAttempted(RuntimeError):
    """Levantada quando um teste tenta abrir uma conexão real."""


def _blocked(method: str, url: object) -> NetworkAccessAttempted:
    return NetworkAccessAttempted(
        f"Chamada de rede real bloqueada na suíte: {method} {url}\n"
        "Mocke o cliente HTTP no teste. Se a chamada real for mesmo "
        f"intencional, marque o teste com @pytest.mark.{_ALLOW_MARK}."
    )


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        f"{_ALLOW_MARK}: permite que o teste faça requisições de rede reais.",
    )


@pytest.fixture(autouse=True)
def block_network(request, monkeypatch):
    """Faz qualquer egresso HTTP real levantar exceção durante os testes."""
    if request.node.get_closest_marker(_ALLOW_MARK):
        return

    import httpx

    async def _async_guard(self, request_obj, *args, **kwargs):
        raise _blocked(request_obj.method, request_obj.url)

    def _sync_guard(self, request_obj, *args, **kwargs):
        raise _blocked(request_obj.method, request_obj.url)

    monkeypatch.setattr(
        httpx.AsyncHTTPTransport, "handle_async_request", _async_guard, raising=True
    )
    monkeypatch.setattr(
        httpx.HTTPTransport, "handle_request", _sync_guard, raising=True
    )

    # boto3/botocore (R2) sai por um caminho próprio, fora do httpx.
    try:
        from botocore.httpsession import URLLib3Session
    except ImportError:
        return

    def _botocore_guard(self, request_obj, *args, **kwargs):
        raise _blocked(getattr(request_obj, "method", "?"), getattr(request_obj, "url", "?"))

    monkeypatch.setattr(URLLib3Session, "send", _botocore_guard, raising=True)
