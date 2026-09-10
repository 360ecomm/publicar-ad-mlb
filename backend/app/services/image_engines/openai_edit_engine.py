import base64
import httpx

from app.config import get_settings
from app.services.ai.cost_log import log_ai_cost
from app.services.image_engines.base import ImageEngineUnavailableError

_OPENAI_EDITS_URL = "https://api.openai.com/v1/images/edits"

# A partir do gpt-image-2 a OpenAI processa toda imagem de entrada em alta
# fidelidade automaticamente e rejeita `input_fidelity` no payload. Modelos
# anteriores (gpt-image-1, gpt-image-1.5) ainda exigem o parâmetro explícito.
_MODELS_WITHOUT_INPUT_FIDELITY = ("gpt-image-2",)

# Tamanhos aceitos pelo /v1/images/edits (referência oficial da OpenAI):
# o gpt-image-2 aceita resoluções arbitrárias WIDTHxHEIGHT desde que ambos os
# lados sejam divisíveis por 16 e a proporção fique entre 1:3 e 3:1 — 1200 é
# divisível por 16, então dá para pedir o alvo do ML (1200x1200) nativamente.
# Modelos anteriores (gpt-image-1, gpt-image-1.5) só aceitam os tamanhos
# fixos 1024x1024, 1536x1024 e 1024x1536; nesses casos pedimos o quadrado e o
# pós-processamento faz o upscale até 1200.
_MODELS_WITH_ARBITRARY_SIZE = ("gpt-image-2",)
_FALLBACK_SQUARE_SIZE = "1024x1024"


def _accepts_input_fidelity(model: str) -> bool:
    return not model.startswith(_MODELS_WITHOUT_INPUT_FIDELITY)


def _size_for_model(model: str) -> str:
    """Maior quadrado que o modelo aceita, alinhado ao alvo 1200x1200 do ML."""
    from app.services.image_postprocess_service import ML_TARGET_DIM

    if model.startswith(_MODELS_WITH_ARBITRARY_SIZE):
        return f"{ML_TARGET_DIM}x{ML_TARGET_DIM}"
    return _FALLBACK_SQUARE_SIZE


class OpenAIEditEngine:
    def __init__(self) -> None:
        self.settings = get_settings()

    async def edit(
        self, images: list[bytes], prompt: str, n: int, size: str | None = None
    ) -> list[bytes]:
        """`size` explicito vence o padrao do modelo.

        Existe para o esquema de 5 posicoes: o canvas passa a ser dado do
        PERFIL da categoria (`PositionProfile.canvas`), nao uma constante do
        motor — e o que permite um perfil futuro pedir outro formato sem
        tocar aqui. Omitido, o comportamento e o de sempre.
        """
        files = [
            ("image[]", (f"input_{i}.jpg", img, "image/jpeg"))
            for i, img in enumerate(images)
        ]
        model = self.settings.openai_image_model
        data = {
            "model": model,
            "prompt": prompt,
            "n": str(n),
            "quality": "medium",
            "output_format": "jpeg",
            "size": size or _size_for_model(model),
        }
        if _accepts_input_fidelity(model):
            data["input_fidelity"] = "high"
        try:
            async with httpx.AsyncClient(timeout=90.0) as client:
                resp = await client.post(
                    _OPENAI_EDITS_URL,
                    headers={"Authorization": f"Bearer {self.settings.openai_api_key}"},
                    data=data,
                    files=files,
                )
        except httpx.TimeoutException as exc:
            raise ImageEngineUnavailableError(f"Timeout ao chamar a OpenAI (edits): {exc}") from exc

        if not resp.is_success:
            if resp.status_code in (401, 403, 429) or resp.status_code >= 500:
                raise ImageEngineUnavailableError(
                    f"OpenAI Edits API {resp.status_code}: {resp.text[:600]}"
                )
            raise httpx.HTTPStatusError(
                f"OpenAI Edits API {resp.status_code}: {resp.text[:600]}",
                request=resp.request,
                response=resp,
            )

        body = resp.json()
        data_resp = body.get("data", [])
        usage = body.get("usage") or {}
        log_ai_cost(
            provider="openai", task="image_edit", model=model,
            images=len(data_resp), input_images=len(images),
            size=data["size"], quality=data["quality"],
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
            total_tokens=usage.get("total_tokens"),
        )
        return [base64.b64decode(item["b64_json"]) for item in data_resp]
