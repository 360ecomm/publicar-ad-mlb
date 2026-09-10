"""A suíte nunca fala com o Redis real — autoverificação do conftest.

Se alguém remover o `celery_app.conf.update(broker_url="memory://")` do
conftest, este arquivo falha antes que o vazamento volte a aparecer como
NoResultFound no worker de produção. Ver o comentário no conftest.
"""
import pytest


class TestCeleryIsolado:
    def test_broker_e_backend_em_memoria(self):
        from app.workers.celery_app import celery_app

        assert celery_app.conf.broker_url.startswith("memory://"), celery_app.conf.broker_url
        assert celery_app.conf.result_backend.startswith("cache+memory://"), celery_app.conf.result_backend
        assert celery_app.conf.task_always_eager is False, "eager executaria a task inline contra os mocks"

    def test_conexao_de_escrita_usa_o_transporte_em_memoria(self):
        """Confere a conexão de fato, não só a conf: é ela que publicaria."""
        from app.workers.celery_app import celery_app

        with celery_app.connection_for_write() as conn:
            assert conn.transport_cls == "memory", conn.as_uri()

    def test_delay_real_nao_sai_do_processo(self):
        """Publica de verdade (sem patch) e prova que a mensagem ficou na
        memória do processo — o que antes ia para o Redis real."""
        from app.workers.tasks.ai_tasks import generate_description
        from app.workers.celery_app import celery_app

        result = generate_description.delay("00000000-0000-0000-0000-000000000000")

        assert result.id
        with celery_app.connection_for_write() as conn:
            assert conn.transport_cls == "memory"
