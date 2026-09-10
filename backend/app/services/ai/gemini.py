import json
import re
import httpx
from app.config import get_settings
from app.services.ai.base import AIProvider
from app.services.ai.cost_log import log_ai_cost
from app.services.ai.prompts import (
    build_title_prompt,
    build_description_prompt,
    build_image_prompt_request,
    build_card_copy_prompt,
)

_BASE = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


def _extract_json(text: str) -> str:
    text = text.strip()
    text = re.sub(r'^```(?:json)?\s*', '', text)
    text = re.sub(r'\s*```$', '', text).strip()
    start = text.find('{')
    end = text.rfind('}') + 1
    if start >= 0 and end > start:
        text = text[start:end]
    # Substitui caracteres de controle por espaço
    text = re.sub(r'[\x00-\x1f\x7f]', ' ', text)
    # Tenta parsear diretamente; se falhar, usa json-repair
    try:
        import json as _json
        _json.loads(text)
        return text
    except Exception:
        try:
            from json_repair import repair_json
            return repair_json(text)
        except Exception:
            return text


class GeminiProvider(AIProvider):
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
        # Batch: 1 titulo, saida curta, thinking DESLIGADO. O modelo por tras
        # de `gemini-flash-latest` gastava os 500 tokens pensando e a resposta
        # chegava cortada (finish=MAX_TOKENS, 482 tokens de thought, 14 de
        # texto); com thinkingBudget=0 o mesmo prompt sai inteiro em 19 tokens.
        # Mais barato e mais confiavel que subir o orcamento com thinking ligado.
        text = await self._call(
            prompt, max_tokens=500 if batch_mode else 2000, temperature=0.6,
            thinking=not batch_mode, task="title",
        )
        parsed = json.loads(_extract_json(text))
        if not isinstance(parsed, dict):
            raise RuntimeError(f"Gemini não retornou um JSON de título válido: {text[:300]!r}")
        if batch_mode:
            title = parsed.get("title", "").strip()[:60]
            if not title:
                # Vazio seguia ate `domain_discovery?q=` devolver 400, tres
                # tasks depois. Falha aqui, com o texto cru para diagnostico.
                raise RuntimeError(f"Gemini retornou título vazio em batch_mode: {text[:300]!r}")
            return [{"title": title, "score": None, "rationale": "batch_auto"}]
        titles = parsed.get("titles")
        if not isinstance(titles, list):
            raise RuntimeError(f"Gemini não retornou a lista 'titles': {text[:300]!r}")
        return titles

    async def generate_description(self, listing_data: dict) -> str:
        prompt = build_description_prompt(listing_data)
        return await self._call(prompt, max_tokens=2000, temperature=0.6, task="description")

    async def generate_image_prompt(self, brand: str, title: str, description: str) -> str:
        prompt = build_image_prompt_request(brand, title, description)
        return (await self._call(prompt, max_tokens=200, temperature=0.3, task="image_prompt")).strip()

    async def generate_card_copy(self, source: dict) -> dict:
        prompt = build_card_copy_prompt(source)
        text = await self._call(prompt, max_tokens=1200, temperature=0.4, task="card_copy")
        parsed = json.loads(_extract_json(text))
        if not isinstance(parsed, dict):
            raise RuntimeError(f"Gemini não retornou um JSON de card válido: {text[:300]!r}")
        return parsed

    async def _call(
        self, prompt: str, max_tokens: int, temperature: float, thinking: bool = True,
        task: str = "unknown",
    ) -> str:
        url = _BASE.format(model=self.settings.gemini_model)
        generation_config: dict = {"temperature": temperature, "maxOutputTokens": max_tokens}
        if not thinking:
            # `thinkingBudget: 0` desliga o raciocinio no gemini-3.8-flash:
            # sonda com o prompt real deu finish=STOP e ZERO tokens de thought.
            # A doc da familia 3.x diz que `thinkingBudget` "nao e' mais
            # recomendado" em favor de `thinkingLevel`, mas o 3.8 Flash NAO
            # aceita o nivel `minimal` (400 "not supported for this model") e
            # `low` ainda gasta thought. O budget 0 segue sendo a unica forma
            # de desligar de fato; se um modelo futuro o ignorar, a trava de
            # MAX_TOKENS abaixo denuncia.
            generation_config["thinkingConfig"] = {"thinkingBudget": 0}
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                url,
                headers={"X-goog-api-key": self.settings.gemini_api_key},
                json={
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": generation_config,
                },
            )
        response.raise_for_status()
        body = response.json()
        candidate = body["candidates"][0]
        # Custo ANTES da validacao: o dinheiro foi gasto mesmo quando a
        # resposta e' inutil. `modelVersion` e' o modelo que respondeu de fato,
        # nao o nome pedido — e' como se descobre que um alias trocou por baixo.
        usage = body.get("usageMetadata", {})
        log_ai_cost(
            provider="gemini", task=task,
            model=body.get("modelVersion") or self.settings.gemini_model,
            input_tokens=usage.get("promptTokenCount"),
            output_tokens=usage.get("candidatesTokenCount"),
            thought_tokens=usage.get("thoughtsTokenCount", 0),
            total_tokens=usage.get("totalTokenCount"),
        )
        # Resposta cortada NUNCA vira texto: o json_repair "consertava" o JSON
        # pela metade e o titulo truncado seguia adiante sem ninguem ver. Se o
        # orcamento voltar a ficar baixo demais (ou o alias trocar de modelo e
        # ignorar o thinkingBudget), a falha e' ruidosa e diz o que aconteceu.
        if candidate.get("finishReason") == "MAX_TOKENS":
            raise RuntimeError(
                f"Gemini cortou a resposta (finishReason=MAX_TOKENS, "
                f"maxOutputTokens={max_tokens}, thinking={thinking}): "
                f"{str(candidate.get('content', {}))[:300]!r}"
            )
        parts = candidate["content"]["parts"]
        # Filtra parts de "thinking" (gemini-2.5-flash/-pro emite pensamentos separados)
        return "".join(p.get("text", "") for p in parts if not p.get("thought", False))
