"""Modelo Gemini fixo em nome explicito + log de custo por chamada de IA.

Por que o nome explicito: `gemini-flash-latest` e um alias trocado a quente
pelo Google a cada release. Em 2026-09 ele passou a apontar para
`gemini-3.8-flash` (comprovado pelo `modelVersion` da resposta) sem nenhuma
mudanca nossa, e o titulo em lote quebrou. Documentacao oficial
(ai.google.dev/gemini-api/docs/models): "Latest aliases ... are hot-swapped
with each new release". Modelo estavel nao muda.

Por que o log: dado real de custo por SKU comeca a se acumular hoje, dos dois
provedores, sem migracao. Uma linha `ai_cost key=value ...` por chamada, com
provedor, tarefa, modelo, unidades cobradas e o listing/SKU em contexto.
"""
import logging
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _campos(record: logging.LogRecord) -> dict:
    """Desmonta `ai_cost k=v k=v ...` num dict — o formato e' o contrato."""
    msg = record.getMessage()
    assert msg.startswith("ai_cost "), msg
    return dict(par.split("=", 1) for par in msg[len("ai_cost "):].split(" "))


def _registros(caplog) -> list[dict]:
    return [_campos(r) for r in caplog.records if r.name == "ai_cost"]


@pytest.fixture(autouse=True)
def _contexto_limpo():
    from app.services.ai.cost_log import set_cost_context
    set_cost_context(listing_id=None, sku=None)
    yield
    set_cost_context(listing_id=None, sku=None)


class TestModeloExplicito:
    def test_default_nao_e_alias_latest(self):
        from app.config import Settings
        default = Settings.model_fields["gemini_model"].default
        assert default == "gemini-3.8-flash"
        assert "latest" not in default


class TestCostLogModulo:
    def test_linha_com_contexto(self, caplog):
        from app.services.ai.cost_log import log_ai_cost, set_cost_context
        caplog.set_level(logging.INFO, logger="ai_cost")
        set_cost_context(listing_id="lid-1", sku="T37")

        log_ai_cost(provider="gemini", task="title", model="gemini-3.8-flash", input_tokens=120, output_tokens=19)

        (c,) = _registros(caplog)
        assert c == {"provider": "gemini", "task": "title", "model": "gemini-3.8-flash",
                     "listing_id": "lid-1", "sku": "T37", "input_tokens": "120", "output_tokens": "19"}

    def test_sem_contexto_nao_quebra(self, caplog):
        from app.services.ai.cost_log import log_ai_cost
        caplog.set_level(logging.INFO, logger="ai_cost")

        log_ai_cost(provider="openai", task="image_edit", model="gpt-image-2", images=1)

        (c,) = _registros(caplog)
        assert c["listing_id"] == "None" and c["sku"] == "None" and c["images"] == "1"


def _resposta_gemini(text, usage, finish="STOP", model_version="gemini-3.8-flash"):
    r = MagicMock()
    r.raise_for_status = MagicMock()
    r.json.return_value = {
        "candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": finish}],
        "usageMetadata": usage,
        "modelVersion": model_version,
    }
    return r


_USAGE = {"promptTokenCount": 120, "candidatesTokenCount": 19, "totalTokenCount": 139}


class TestGeminiLogaCusto:
    @pytest.mark.asyncio
    async def test_titulo_em_lote(self, caplog):
        from app.services.ai.cost_log import set_cost_context
        from app.services.ai.gemini import GeminiProvider
        caplog.set_level(logging.INFO, logger="ai_cost")
        set_cost_context(listing_id="lid-1", sku="T37")
        mock_post = AsyncMock(return_value=_resposta_gemini('{"title": "Martin Colônia 100ml Wepink"}', _USAGE))

        with patch("httpx.AsyncClient") as cls:
            cls.return_value.__aenter__.return_value.post = mock_post
            await GeminiProvider().generate_titles(sku_description="d", sku_brand="b", condition="new", batch_mode=True)

        (c,) = _registros(caplog)
        assert c["provider"] == "gemini" and c["task"] == "title"
        assert c["model"] == "gemini-3.8-flash", "modelVersion da resposta, nao o nome pedido"
        assert (c["input_tokens"], c["output_tokens"], c["thought_tokens"], c["total_tokens"]) == ("120", "19", "0", "139")
        assert c["listing_id"] == "lid-1" and c["sku"] == "T37"

    @pytest.mark.asyncio
    async def test_descricao(self, caplog):
        from app.services.ai.gemini import GeminiProvider
        caplog.set_level(logging.INFO, logger="ai_cost")
        mock_post = AsyncMock(return_value=_resposta_gemini("<p>ok</p>", _USAGE))

        with patch("httpx.AsyncClient") as cls:
            cls.return_value.__aenter__.return_value.post = mock_post
            await GeminiProvider().generate_description({"selected_title": "t", "sku_brand": "b",
                                                         "sku_description": "d", "condition": "new", "attributes": []})

        (c,) = _registros(caplog)
        assert c["task"] == "description"

    @pytest.mark.asyncio
    async def test_resposta_cortada_ainda_loga_o_custo(self, caplog):
        """O dinheiro foi gasto mesmo quando a resposta e' inutil: o log sai
        ANTES do erro, com os 482 tokens de thought que causaram o corte."""
        from app.services.ai.gemini import GeminiProvider
        caplog.set_level(logging.INFO, logger="ai_cost")
        usage = {"promptTokenCount": 120, "candidatesTokenCount": 14, "thoughtsTokenCount": 482, "totalTokenCount": 616}
        mock_post = AsyncMock(return_value=_resposta_gemini('{"title": "Martin', usage, finish="MAX_TOKENS"))

        with patch("httpx.AsyncClient") as cls:
            cls.return_value.__aenter__.return_value.post = mock_post
            with pytest.raises(RuntimeError, match="MAX_TOKENS"):
                await GeminiProvider().generate_titles(sku_description="d", sku_brand="b", condition="new", batch_mode=True)

        (c,) = _registros(caplog)
        assert c["thought_tokens"] == "482" and c["total_tokens"] == "616"


def _resposta_openai(usage):
    import base64
    r = MagicMock()
    r.is_success = True
    r.json.return_value = {"data": [{"b64_json": base64.b64encode(b"img").decode()}], "usage": usage}
    return r


_USAGE_IMG = {"input_tokens": 523, "output_tokens": 1056, "total_tokens": 1579}


class TestOpenAILogaCusto:
    @pytest.mark.asyncio
    async def test_edit(self, caplog):
        from app.services.ai.cost_log import set_cost_context
        from app.services.image_engines.openai_edit_engine import OpenAIEditEngine
        caplog.set_level(logging.INFO, logger="ai_cost")
        set_cost_context(listing_id="lid-2", sku="T38")
        mock_post = AsyncMock(return_value=_resposta_openai(_USAGE_IMG))

        with patch("httpx.AsyncClient") as cls:
            cls.return_value.__aenter__.return_value.post = mock_post
            engine = OpenAIEditEngine()
            with patch.object(engine.settings, "openai_image_model", "gpt-image-2"):
                await engine.edit(images=[b"a", b"b"], prompt="p", n=1, size="1200x1200")

        (c,) = _registros(caplog)
        assert c["provider"] == "openai" and c["task"] == "image_edit" and c["model"] == "gpt-image-2"
        assert c["images"] == "1" and c["input_images"] == "2" and c["size"] == "1200x1200" and c["quality"] == "medium"
        assert (c["input_tokens"], c["output_tokens"], c["total_tokens"]) == ("523", "1056", "1579")
        assert c["listing_id"] == "lid-2" and c["sku"] == "T38"

    @pytest.mark.asyncio
    async def test_sem_usage_na_resposta_loga_mesmo_assim(self, caplog):
        """Modelo antigo sem `usage`: a linha sai com as unidades que existem
        (imagens, tamanho) e tokens vazios — melhor que nenhuma linha."""
        from app.services.image_engines.openai_edit_engine import OpenAIEditEngine
        caplog.set_level(logging.INFO, logger="ai_cost")
        mock_post = AsyncMock(return_value=_resposta_openai(usage=None))

        with patch("httpx.AsyncClient") as cls:
            cls.return_value.__aenter__.return_value.post = mock_post
            await OpenAIEditEngine().edit(images=[b"a"], prompt="p", n=1)

        (c,) = _registros(caplog)
        assert c["images"] == "1" and c["input_tokens"] == "None"


@asynccontextmanager
async def _sessao(mock_db):
    yield mock_db


class TestWorkersFixamContexto:
    @pytest.mark.asyncio
    async def test_generate_description_fixa_listing_e_sku(self):
        from app.services.ai.cost_log import cost_context
        from app.workers.tasks.ai_tasks import _generate_description_async

        listing = MagicMock()
        listing.id = "lid-9"; listing.sku_external_id = "T37"; listing.status = "generating_description"
        listing.selected_title = "t"; listing.sku_brand = "b"; listing.sku_description = "d"
        listing.condition = "new"; listing.created_via = "manual"
        n = [0]

        async def execute(stmt):
            n[0] += 1
            r = MagicMock()
            if n[0] == 1:
                r.scalar_one = MagicMock(return_value=listing)
            elif n[0] == 2:
                r.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
            else:
                r.scalar_one_or_none = MagicMock(return_value=None)
            return r

        db = AsyncMock(); db.execute = execute; db.add = MagicMock(); db.commit = AsyncMock()
        with patch("app.database.worker_session", lambda: _sessao(db)), \
             patch("app.services.ai.service.get_ai_provider",
                   return_value=AsyncMock(generate_description=AsyncMock(return_value="<p>x</p>"))):
            await _generate_description_async("lid-9")

        assert cost_context() == {"listing_id": "lid-9", "sku": "T37"}

    @pytest.mark.asyncio
    async def test_generate_images_fixa_listing_e_sku(self):
        from app.services.ai.cost_log import cost_context
        from app.workers.tasks.image_tasks import _generate_images_async

        listing = MagicMock()
        listing.id = "lid-8"; listing.sku_external_id = ""; listing.status = "generating_images"
        listing.seller_id = "sid"; listing.created_via = "manual"

        db = AsyncMock()
        db.execute = AsyncMock(return_value=MagicMock(scalar_one=MagicMock(return_value=listing)))
        db.commit = AsyncMock()
        with patch("app.database.worker_session", lambda: _sessao(db)),              patch("app.workers.tasks.image_tasks._fetch_upload_token", new_callable=AsyncMock, return_value="tok"),              patch("app.workers.tasks.image_tasks._try_i2i_generation", new_callable=AsyncMock, return_value=None):
            await _generate_images_async("lid-8")

        assert cost_context() == {"listing_id": "lid-8", "sku": ""}
