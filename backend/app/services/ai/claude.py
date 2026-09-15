import json
import httpx
from app.config import get_settings
from app.services.ai.base import AIProvider
from app.services.ai.prompts import (
    build_title_prompt,
    build_title_retry_prompt,
    build_description_prompt,
    build_card_copy_prompt,
)
from app.services.ai.title_guard import TITLE_TARGET_CHARS, aplicar_limite

_BASE = "https://api.anthropic.com/v1/messages"


class ClaudeProvider(AIProvider):
    def __init__(self) -> None:
        self.settings = get_settings()

    async def generate_titles(
        self,
        sku_description: str,
        sku_brand: str,
        condition: str,
        ean: str | None = None,
        seo_context: str | None = None,
        batch_mode: bool = False,
        title_config: dict | None = None,
        sku_model: str | None = None,
        technical_reference: str | None = None,
        vehicle_application: str | None = None,
        color: str | None = None,
        size: str | None = None,
        capacity: str | None = None,
        material: str | None = None,
        gender: str | None = None,
    ) -> list[dict]:
        prompt = build_title_prompt(
            sku_description, sku_brand, condition, ean, seo_context, batch_mode,
            title_config=title_config, sku_model=sku_model,
            technical_reference=technical_reference, vehicle_application=vehicle_application,
            color=color, size=size, capacity=capacity, material=material, gender=gender,
        )
        text = await self._call(prompt, max_tokens=200 if batch_mode else 500, temperature=0.6)
        if batch_mode:
            from app.services.ai.gemini import _extract_json
            parsed = json.loads(_extract_json(text))
            titulo = parsed.get("title", "")
            titulos = [{
                "title": titulo.strip() if isinstance(titulo, str) else "",
                "score": None,
                "rationale": "batch_auto",
            }]
        else:
            limpo = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            titulos = json.loads(limpo)["titles"]

        async def _retentar() -> list | None:
            from app.services.ai.gemini import _extract_json

            recusados = [
                t.get("title", "") for t in titulos
                if isinstance(t, dict) and isinstance(t.get("title"), str)
            ]
            resposta = await self._call(
                build_title_retry_prompt(prompt, recusados, TITLE_TARGET_CHARS),
                max_tokens=200 if batch_mode else 500, temperature=0.6,
            )
            if batch_mode:
                novo = json.loads(_extract_json(resposta))
                titulo_novo = novo.get("title", "")
                titulo_novo = titulo_novo.strip() if isinstance(titulo_novo, str) else ""
                if not titulo_novo:
                    return None
                return [{"title": titulo_novo, "score": None, "rationale": "batch_auto"}]
            limpo_novo = (
                resposta.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            )
            lista = json.loads(limpo_novo).get("titles")
            return lista if isinstance(lista, list) else None

        # Este provedor esta fora de uso, mas o `[:60]` cego que vivia aqui era
        # a MESMA armadilha do Gemini (ver `title_guard`): deixar so num dos
        # dois seria plantar o bug para quando alguem trocar o provider.
        return await aplicar_limite(titulos, retentar=_retentar)

    async def generate_description(self, listing_data: dict) -> str:
        prompt = build_description_prompt(listing_data)
        return await self._call(prompt, max_tokens=2000, temperature=0.6)

    async def generate_card_copy(self, source: dict) -> dict:
        from app.services.ai.gemini import _extract_json

        prompt = build_card_copy_prompt(source)
        text = await self._call(prompt, max_tokens=1200, temperature=0.4)
        parsed = json.loads(_extract_json(text))
        if not isinstance(parsed, dict):
            raise RuntimeError(f"Claude não retornou um JSON de card válido: {text[:300]!r}")
        return parsed

    async def _call(self, prompt: str, max_tokens: int, temperature: float) -> str:
        async with httpx.AsyncClient(timeout=45.0) as client:
            response = await client.post(
                _BASE,
                headers={
                    "x-api-key": self.settings.anthropic_api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": self.settings.claude_model,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                    "messages": [{"role": "user", "content": prompt}],
                },
            )
        response.raise_for_status()
        return response.json()["content"][0]["text"]
