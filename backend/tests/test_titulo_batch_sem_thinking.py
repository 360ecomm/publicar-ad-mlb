"""Titulo em lote: thinking desligado, e resposta cortada nunca vira titulo.

O caso real (2026-09-09, lote T37/T38 em producao): `generate_titles` em
`batch_mode` usava `maxOutputTokens=500` sem `thinkingConfig`, e o modelo por
tras do alias `gemini-flash-latest` gasta esse orcamento PENSANDO antes de
escrever. Sonda com o prompt real do T37:

    maxOutputTokens=500                    -> finish=MAX_TOKENS thoughts=482 out=14  (cortado)
    maxOutputTokens=500 + thinkingBudget=0 -> finish=STOP       thoughts=0   out=19  (inteiro)
    maxOutputTokens=2000                   -> finish=STOP       thoughts=1467 out=19 (inteiro, caro)

O JSON cortado passava pelo `json_repair` e virava titulo pela metade (T38:
"...Desodorante Col") ou vazio (T37), e o vazio seguiu adiante ate o
`domain_discovery?q=` devolver 400. Duas travas, independentes:

1. Titulo e saida curta: em batch, thinking DESLIGADO (`thinkingBudget: 0`).
   Mais barato e mais confiavel que "thinking ligado com orcamento maior".
2. `finishReason == MAX_TOKENS` e erro, nunca texto. Se alguem baixar o
   orcamento de novo, ou o alias apontar para um modelo que ignore o
   `thinkingBudget`, a falha e ruidosa em vez de virar titulo truncado.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.ai.gemini import GeminiProvider


def _resposta(text: str, finish_reason: str | None = None) -> MagicMock:
    response = MagicMock()
    response.raise_for_status = MagicMock()
    candidate = {"content": {"parts": [{"text": text}]}}
    if finish_reason:
        candidate["finishReason"] = finish_reason
    response.json.return_value = {"candidates": [candidate]}
    return response


async def _gerar(mock_post, **kwargs):
    with patch("httpx.AsyncClient") as cls:
        cls.return_value.__aenter__.return_value.post = mock_post
        return await GeminiProvider().generate_titles(
            sku_description="Martin Desodorante Colônia 100ml - Wepink",
            sku_brand="Wepink", condition="new", **kwargs,
        )


def _generation_config(mock_post) -> dict:
    return mock_post.call_args.kwargs["json"]["generationConfig"]


class TestTituloBatchSemThinking:
    @pytest.mark.asyncio
    async def test_batch_desliga_thinking_na_requisicao(self):
        mock_post = AsyncMock(return_value=_resposta('{"title": "Martin Desodorante Colônia 100ml Wepink"}', "STOP"))

        titles = await _gerar(mock_post, batch_mode=True)

        assert titles[0]["title"] == "Martin Desodorante Colônia 100ml Wepink"
        assert _generation_config(mock_post)["thinkingConfig"] == {"thinkingBudget": 0}

    @pytest.mark.asyncio
    async def test_manual_continua_com_thinking(self):
        """Escopo contido: o modo manual (3 titulos com score, 2000 tokens)
        funciona hoje e nao muda."""
        text = '{"titles": [{"title": "Perfume Martin Wepink", "score": 9.0, "rationale": "ok"}]}'
        mock_post = AsyncMock(return_value=_resposta(text, "STOP"))

        await _gerar(mock_post, batch_mode=False)

        assert "thinkingConfig" not in _generation_config(mock_post)

    @pytest.mark.asyncio
    async def test_resposta_cortada_por_max_tokens_e_erro(self):
        """O JSON cortado do caso real. Sem a trava, o json_repair devolvia
        um titulo pela metade e ninguem via."""
        mock_post = AsyncMock(return_value=_resposta('{"title": "Martin Desodorante Colônia 100', "MAX_TOKENS"))

        with pytest.raises(RuntimeError, match="MAX_TOKENS"):
            await _gerar(mock_post, batch_mode=True)

    @pytest.mark.asyncio
    async def test_titulo_vazio_em_batch_e_erro(self):
        """T37: titulo '' seguiu ate o domain_discovery com q= vazio. Vazio
        tem que falhar aqui, no provider, nao tres tasks depois."""
        mock_post = AsyncMock(return_value=_resposta('{"title": ""}', "STOP"))

        with pytest.raises(RuntimeError, match="vazio"):
            await _gerar(mock_post, batch_mode=True)
