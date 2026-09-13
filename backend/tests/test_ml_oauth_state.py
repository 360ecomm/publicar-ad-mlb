"""Testes do state do OAuth do ML e do destino pos-callback.

Os dois bugs cobertos aqui so se manifestavam em producao:

- o state vivia num dict de modulo, entao `uvicorn --workers 2` mandava o
  /connect para um processo e o callback para outro, quebrando o fluxo de
  forma intermitente;
- o callback redirecionava para localhost:3000, que nao existe no servidor.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.responses import RedirectResponse


def _fake_redis():
    """Redis de mentira com dicionario compartilhado.

    O dicionario fica FORA do modulo do servico de proposito: ele representa o
    servidor Redis, alcancavel por qualquer processo, nao a memoria de um
    worker. E o que permite simular "iniciou num processo, terminou em outro".
    """
    store: dict[str, str] = {}
    client = MagicMock()

    async def setex(key, ttl, value):
        store[key] = value

    async def getdel(key):
        return store.pop(key, None)

    client.setex = AsyncMock(side_effect=setex)
    client.getdel = AsyncMock(side_effect=getdel)
    client.aclose = AsyncMock()
    return client, store


class TestStateNaoDependeDoProcesso:
    @pytest.mark.asyncio
    async def test_state_vai_para_o_redis_com_ttl(self):
        from app.services.ml_oauth_service import MLOAuthService, _STATE_TTL_SECONDS

        client, store = _fake_redis()
        with patch("app.services.ml_oauth_service._redis_client", return_value=client):
            url = await MLOAuthService().get_authorization_url(user_id="user-1")

        assert client.setex.await_count == 1
        key, ttl, value = client.setex.await_args.args
        assert key.startswith("ml_oauth_state:")
        assert ttl == _STATE_TTL_SECONDS
        assert value == "user-1"
        # o state da URL e o mesmo que foi guardado
        assert key.split(":", 1)[1] in url

    @pytest.mark.asyncio
    async def test_modulo_nao_guarda_mais_state_em_memoria(self):
        """O dict de processo tem que ter sumido, nao so deixado de ser lido."""
        import app.services.ml_oauth_service as svc

        assert not hasattr(svc, "_pending_states"), (
            "_pending_states ainda existe: o state voltaria a ser por processo"
        )

    @pytest.mark.asyncio
    async def test_fluxo_iniciado_num_processo_completa_em_outro(self):
        """O cenario que o bug quebrava.

        Duas instancias distintas do servico representam os dois workers do
        uvicorn. Com o state em memoria de processo, a segunda nao encontraria
        nada e o fluxo morreria em 400.
        """
        from app.services.ml_oauth_service import MLOAuthService, _STATE_PREFIX

        client, store = _fake_redis()

        with patch("app.services.ml_oauth_service._redis_client", return_value=client):
            # Worker A atende o /connect
            worker_a = MLOAuthService()
            url = await worker_a.get_authorization_url(user_id="user-42")
            state = url.split("state=")[1]

            # Worker B, instancia diferente, atende o callback
            worker_b = MLOAuthService()
            mock_db = AsyncMock()
            # `db.add` e sincrono no SQLAlchemy: deixar o AsyncMock cuidar dele
            # devolve uma corrotina que ninguem aguarda, e o pytest reclama.
            mock_db.add = MagicMock()
            mock_db.execute = AsyncMock(return_value=MagicMock())
            with patch.object(
                MLOAuthService, "_exchange_code", new_callable=AsyncMock,
                return_value={"access_token": "at", "refresh_token": "rt", "expires_in": 3600},
            ), patch.object(
                MLOAuthService, "_get_ml_user", new_callable=AsyncMock,
                return_value={"id": 123, "nickname": "SELLER"},
            ), patch("app.services.ml_oauth_service.encrypt_value", side_effect=lambda v: f"enc:{v}"):
                mock_db.execute.return_value.scalar_one_or_none = MagicMock(return_value=None)
                mock_db.execute.return_value.scalars = MagicMock(
                    return_value=MagicMock(all=MagicMock(return_value=[]))
                )
                await worker_b.handle_callback("code-abc", state, mock_db)

        # state consumido do "servidor" compartilhado
        assert f"{_STATE_PREFIX}{state}" not in store
        assert mock_db.commit.await_count == 1

    @pytest.mark.asyncio
    async def test_state_desconhecido_da_400(self):
        from fastapi import HTTPException
        from app.services.ml_oauth_service import MLOAuthService

        client, _ = _fake_redis()
        with patch("app.services.ml_oauth_service._redis_client", return_value=client):
            with pytest.raises(HTTPException) as exc:
                await MLOAuthService().handle_callback("code", "state-que-nunca-existiu", AsyncMock())

        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_state_so_pode_ser_usado_uma_vez(self):
        """GETDEL e atomico: replay do mesmo state nao resgata o usuario de novo."""
        from app.services.ml_oauth_service import MLOAuthService, _pop_state

        client, store = _fake_redis()
        with patch("app.services.ml_oauth_service._redis_client", return_value=client):
            url = await MLOAuthService().get_authorization_url(user_id="user-9")
            state = url.split("state=")[1]

            assert await _pop_state(state) == "user-9"
            assert await _pop_state(state) is None


class TestDestinoAposCallback:
    @pytest.mark.asyncio
    async def test_sem_frontend_url_devolve_json(self):
        from app.api.v1.endpoints.auth import ml_callback

        settings = MagicMock()
        settings.frontend_url = ""
        with patch("app.api.v1.endpoints.auth.get_settings", return_value=settings), \
             patch("app.api.v1.endpoints.auth.MLOAuthService") as mock_svc:
            mock_svc.return_value.handle_callback = AsyncMock()
            result = await ml_callback(code="c", state="s", db=AsyncMock())

        assert result == {"status": "connected"}
        assert not isinstance(result, RedirectResponse)

    @pytest.mark.asyncio
    async def test_com_frontend_url_redireciona(self):
        from app.api.v1.endpoints.auth import ml_callback

        settings = MagicMock()
        settings.frontend_url = "https://app.exemplo.com.br"
        with patch("app.api.v1.endpoints.auth.get_settings", return_value=settings), \
             patch("app.api.v1.endpoints.auth.MLOAuthService") as mock_svc:
            mock_svc.return_value.handle_callback = AsyncMock()
            result = await ml_callback(code="c", state="s", db=AsyncMock())

        assert isinstance(result, RedirectResponse)
        assert result.headers["location"] == "https://app.exemplo.com.br/contas?ml_connected=true"

    @pytest.mark.asyncio
    async def test_barra_final_no_frontend_url_nao_duplica(self):
        from app.api.v1.endpoints.auth import ml_callback

        settings = MagicMock()
        settings.frontend_url = "https://app.exemplo.com.br/"
        with patch("app.api.v1.endpoints.auth.get_settings", return_value=settings), \
             patch("app.api.v1.endpoints.auth.MLOAuthService") as mock_svc:
            mock_svc.return_value.handle_callback = AsyncMock()
            result = await ml_callback(code="c", state="s", db=AsyncMock())

        assert "//contas" not in result.headers["location"]

    @pytest.mark.asyncio
    async def test_localhost_nao_aparece_mais_no_codigo(self):
        """O redirect antigo era fixo em localhost:3000 — nao pode voltar."""
        from pathlib import Path
        import app.api.v1.endpoints.auth as mod

        fonte = Path(mod.__file__).read_text(encoding="utf-8")
        assert "localhost:3000" not in fonte
