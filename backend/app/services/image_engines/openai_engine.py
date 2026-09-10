import base64
import httpx

from app.config import get_settings
from app.services.ai.cost_log import log_ai_cost
from app.services.image_engines.base import (
    ImageEngineProvider,
    ImageEngineUnavailableError,
    PROMPT_SUFFIX,
)

_OPENAI_IMAGES_URL = "https://api.openai.com/v1/images/generations"
_OPENAI_MODELS_URL = "https://api.openai.com/v1/models"


class OpenAIImageEngine(ImageEngineProvider):
    def __init__(self) -> None:
        self.settings = get_settings()

    async def generate(self, prompt: str) -> list[bytes]:
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(
                    _OPENAI_IMAGES_URL,
                    headers={"Authorization": f"Bearer {self.settings.openai_api_key}"},
                    json={
                        "model": self.settings.openai_image_model,
                        "prompt": prompt + PROMPT_SUFFIX,
                        "n": 4,
                        "size": "1024x1024",
                        "quality": "medium",
                        "background": "opaque",
                    },
                )
        except httpx.TimeoutException as exc:
            raise ImageEngineUnavailableError(f"Timeout ao chamar a OpenAI: {exc}") from exc

        if not resp.is_success:
            if resp.status_code in (401, 403, 429) or resp.status_code >= 500:
                raise ImageEngineUnavailableError(
                    f"OpenAI API {resp.status_code}: {resp.text[:600]}"
                )
            raise httpx.HTTPStatusError(
                f"OpenAI API {resp.status_code}: {resp.text[:600]}",
                request=resp.request,
                response=resp,
            )
        body = resp.json()
        data = body.get("data", [])
        usage = body.get("usage") or {}
        log_ai_cost(
            provider="openai", task="image_generate", model=self.settings.openai_image_model,
            images=len(data), size="1024x1024", quality="medium",
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
            total_tokens=usage.get("total_tokens"),
        )
        return [base64.b64decode(item["b64_json"]) for item in data]


async def check_openai_health() -> bool:
    """Checagem leve de conectividade — usada quando o motor atual é Gemini,
    para decidir se já pode voltar a usar a OpenAI automaticamente (RF4)."""
    settings = get_settings()
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                _OPENAI_MODELS_URL,
                headers={"Authorization": f"Bearer {settings.openai_api_key}"},
            )
        return resp.status_code == 200
    except httpx.HTTPError:
        return False
