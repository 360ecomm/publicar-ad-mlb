"""Trava de seguranca dos testes de Postgres real.

`test_bulk_approve_por_posicao.py` e `test_promocao_indice_unico.py` fazem
`drop_all`/`create_all` contra o banco de `TEST_DATABASE_URL` — rodar isso
sem querer contra o banco do ambiente apagaria dado de verdade. A unica
garantia aceitavel e' o nome do banco, nao a URL inteira (poderia ter
usuario/senha diferentes apontando pro mesmo host): so `publicar_test` pode
sofrer drop_all.

A validacao roda ANTES de qualquer `create_async_engine`/conexao — le so o
nome do banco embutido na URL (via `sqlalchemy.engine.make_url`, que nao
abre socket nenhum). URL errada leva a um `RuntimeError` com mensagem clara,
nunca a um erro de conexao do asyncpg — e' essa diferenca que prova que
nenhuma conexao foi aberta contra o banco errado.

`_engine_dedicado()` NAO recebe a URL como argumento — le `TEST_DATABASE_URL`
do ambiente sozinha, e nunca guarda a URL completa (que carrega a senha) numa
variavel nomeada. Isso importa porque a suite roda com `--showlocals`: um
parametro ou uma variavel local chamada `test_database_url` sobrevive no
frame ate a funcao retornar e aparece em texto claro no traceback quando a
trava dispara (foi exatamente o que aconteceu quando a funcao recebia a URL
como parametro). Sem nome nenhum pra guardar o valor, so o NOME do banco
(extraido antes de qualquer outra coisa, nunca a URL) fica preso a uma
variavel — e esse e' seguro de imprimir.

Modulo de apoio: nao tem teste nenhum aqui, so a trava, para os dois
arquivos reais importarem em vez de duplicar a checagem.
"""
import os

from sqlalchemy import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

NOME_BANCO_DEDICADO = "publicar_test"


def _engine_dedicado() -> AsyncEngine:
    """Le `TEST_DATABASE_URL` do ambiente, valida que aponta para o banco
    dedicado (`publicar_test`) e so entao cria o engine assincrono. Levanta
    `RuntimeError` — sem abrir nenhuma conexao — quando o nome do banco nao
    bate. A URL completa nunca e' atribuida a uma variavel: cada uso le
    `os.environ` na hora, pra nao deixar rastro num frame de traceback."""
    nome = make_url(os.environ.get("TEST_DATABASE_URL", "")).database
    if nome != NOME_BANCO_DEDICADO:
        raise RuntimeError(
            f"TEST_DATABASE_URL aponta para o banco '{nome}'; os testes reais "
            f"fazem drop_all e so rodam em '{NOME_BANCO_DEDICADO}'"
        )
    return create_async_engine(os.environ["TEST_DATABASE_URL"])
