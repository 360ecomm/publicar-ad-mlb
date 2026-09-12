"""Custo da regeneracao de UMA posicao sai no log `ai_cost` com
`task=image_edit_regen`, separado do `image_edit` do lote — e' o numero que
decide entre melhorar o prompt ou seguir regenerando (decisao 6 da spec
2026-09-12-regenerar-posicao).

O rotulo vive numa ContextVar PROPRIA, nao dentro de `cost_context()`: o
dicionario daquele e' contrato fixado em `test_ai_cost_log.py`.
"""
import logging
from unittest.mock import AsyncMock, patch

import pytest

from tests.test_ai_cost_log import _USAGE_IMG, _registros, _resposta_openai


@pytest.fixture(autouse=True)
def _rotulo_limpo():
    from app.services.ai.cost_log import set_cost_context, set_image_edit_task
    set_cost_context(listing_id=None, sku=None)
    set_image_edit_task(None)
    yield
    set_image_edit_task(None)


class TestRotuloDoCusto:
    def test_padrao_continua_image_edit(self):
        from app.services.ai.cost_log import IMAGE_EDIT_TASK_DEFAULT, image_edit_task
        assert image_edit_task() == "image_edit" == IMAGE_EDIT_TASK_DEFAULT

    def test_rotulo_de_regeneracao(self):
        from app.services.ai.cost_log import IMAGE_EDIT_TASK_REGEN, image_edit_task, set_image_edit_task
        set_image_edit_task(IMAGE_EDIT_TASK_REGEN)
        assert image_edit_task() == "image_edit_regen"

    def test_none_volta_ao_padrao(self):
        from app.services.ai.cost_log import image_edit_task, set_image_edit_task
        set_image_edit_task("image_edit_regen")
        set_image_edit_task(None)
        assert image_edit_task() == "image_edit"

    def test_nao_mexe_no_cost_context(self):
        """`cost_context()` e' contrato (test_ai_cost_log fixa o dicionario)."""
        from app.services.ai.cost_log import cost_context, set_cost_context, set_image_edit_task
        set_cost_context(listing_id="lid", sku="T38")
        set_image_edit_task("image_edit_regen")
        assert cost_context() == {"listing_id": "lid", "sku": "T38"}


class TestMotorUsaORotulo:
    async def _editar(self):
        from app.services.image_engines.openai_edit_engine import OpenAIEditEngine
        mock_post = AsyncMock(return_value=_resposta_openai(_USAGE_IMG))
        with patch("httpx.AsyncClient") as cls:
            cls.return_value.__aenter__.return_value.post = mock_post
            engine = OpenAIEditEngine()
            with patch.object(engine.settings, "openai_image_model", "gpt-image-2"):
                await engine.edit(images=[b"a"], prompt="p", n=1, size="1200x1200")

    @pytest.mark.asyncio
    async def test_com_rotulo_de_regeneracao(self, caplog):
        from app.services.ai.cost_log import IMAGE_EDIT_TASK_REGEN, set_cost_context, set_image_edit_task
        caplog.set_level(logging.INFO, logger="ai_cost")
        set_cost_context(listing_id="lid-r", sku="T38")
        set_image_edit_task(IMAGE_EDIT_TASK_REGEN)

        await self._editar()

        (c,) = _registros(caplog)
        assert c["provider"] == "openai" and c["task"] == "image_edit_regen"
        assert c["listing_id"] == "lid-r" and c["sku"] == "T38"

    @pytest.mark.asyncio
    async def test_sem_rotulo_continua_image_edit(self, caplog):
        caplog.set_level(logging.INFO, logger="ai_cost")
        await self._editar()
        (c,) = _registros(caplog)
        assert c["task"] == "image_edit"
