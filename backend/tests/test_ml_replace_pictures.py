"""Substituicao da lista de fotos de um item JA publicado no ML.

Ate aqui o codigo so sabia CRIAR item (`publish`, um POST) e mudar status
(`activate_listing`/`_aguardar_validacao`, PUT de um campo so). Trocar foto de
anuncio no ar era script manual.

A regra que estes testes travam: o PUT de `pictures` no ML e SUBSTITUICAO
TOTAL, nao merge. Mandar 2 IDs num item de 8 fotos nao troca 2 — deixa o
anuncio com 2. Por isso a funcao exige a lista completa e recusa qualquer
coisa que cheire a lista parcial, em vez de confiar em quem chama.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import HTTPException


def _resp(status_code=200, text="{}"):
    r = MagicMock()
    r.status_code = status_code
    r.text = text
    r.json.return_value = {}
    return r


def _client_mock(response):
    client = AsyncMock()
    client.put = AsyncMock(return_value=response)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=client)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx, client


class TestReplaceItemPictures:
    @pytest.mark.asyncio
    async def test_sends_the_full_list_in_order(self):
        from app.services.publish_service import replace_item_pictures

        ctx, client = _client_mock(_resp())
        ids = ["a", "b", "c", "d", "e", "f", "g", "h"]
        with patch("httpx.AsyncClient", return_value=ctx):
            await replace_item_pictures("MLB1", ids, "tok")

        body = client.put.await_args.kwargs["json"]
        assert body == {"pictures": [{"id": i} for i in ids]}, body

    @pytest.mark.asyncio
    async def test_targets_the_right_item_and_authenticates(self):
        from app.services.publish_service import replace_item_pictures

        ctx, client = _client_mock(_resp())
        with patch("httpx.AsyncClient", return_value=ctx):
            await replace_item_pictures("MLB5145387291", ["a", "b"], "tok-xyz")

        assert client.put.await_args.args[0].endswith("/items/MLB5145387291")
        headers = client.put.await_args.kwargs["headers"]
        assert headers["Authorization"] == "Bearer tok-xyz"

    @pytest.mark.asyncio
    async def test_empty_list_is_refused_before_any_request(self):
        """Lista vazia apagaria todas as fotos do anuncio no ar."""
        from app.services.publish_service import replace_item_pictures

        ctx, client = _client_mock(_resp())
        with patch("httpx.AsyncClient", return_value=ctx):
            with pytest.raises(ValueError):
                await replace_item_pictures("MLB1", [], "tok")

        client.put.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_duplicate_ids_are_refused(self):
        """ID repetido e sintoma de lista montada errado — e publicaria a
        mesma foto duas vezes, ocupando o lugar de outra."""
        from app.services.publish_service import replace_item_pictures

        ctx, client = _client_mock(_resp())
        with patch("httpx.AsyncClient", return_value=ctx):
            with pytest.raises(ValueError):
                await replace_item_pictures("MLB1", ["a", "b", "a"], "tok")

        client.put.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_expected_keep_guard_blocks_a_partial_list(self):
        """O guard do enunciado: nenhum ID que deve permanecer pode sumir da
        lista. Sem ele, um erro de montagem despublica fotos silenciosamente —
        o PUT e substituicao total."""
        from app.services.publish_service import replace_item_pictures

        ctx, client = _client_mock(_resp())
        with patch("httpx.AsyncClient", return_value=ctx):
            with pytest.raises(ValueError) as exc:
                await replace_item_pictures(
                    "MLB1", ["a", "b"], "tok", must_keep=["a", "b", "c"]
                )

        assert "c" in str(exc.value)
        client.put.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_expected_keep_guard_passes_when_all_present(self):
        from app.services.publish_service import replace_item_pictures

        ctx, client = _client_mock(_resp())
        with patch("httpx.AsyncClient", return_value=ctx):
            await replace_item_pictures(
                "MLB1", ["a", "b", "novo"], "tok", must_keep=["a", "b"]
            )

        client.put.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_ml_error_becomes_502_with_the_ml_message(self):
        from app.services.publish_service import replace_item_pictures

        ctx, _ = _client_mock(_resp(400, '{"message":"invalid picture"}'))
        with patch("httpx.AsyncClient", return_value=ctx):
            with pytest.raises(HTTPException) as exc:
                await replace_item_pictures("MLB1", ["a", "b"], "tok")

        assert exc.value.status_code == 502
        assert "invalid picture" in exc.value.detail


class TestFetchItem:
    @pytest.mark.asyncio
    async def test_get_is_authenticated(self):
        """A chamada publica devolve 403 desde 2026 — o GET de conferencia
        precisa do token do seller."""
        from app.services.publish_service import fetch_item

        client = AsyncMock()
        resposta = MagicMock()
        resposta.status_code = 200
        resposta.json.return_value = {"id": "MLB1", "pictures": [{"id": "a"}]}
        client.get = AsyncMock(return_value=resposta)
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=client)
        ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=ctx):
            data = await fetch_item("MLB1", "tok-xyz")

        assert client.get.await_args.kwargs["headers"]["Authorization"] == "Bearer tok-xyz"
        assert data["pictures"] == [{"id": "a"}]

    @pytest.mark.asyncio
    async def test_error_becomes_502(self):
        from app.services.publish_service import fetch_item

        client = AsyncMock()
        resposta = MagicMock()
        resposta.status_code = 403
        resposta.text = "forbidden"
        client.get = AsyncMock(return_value=resposta)
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=client)
        ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=ctx):
            with pytest.raises(HTTPException) as exc:
                await fetch_item("MLB1", "tok")

        assert exc.value.status_code == 502
