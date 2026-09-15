import json
import re
import httpx
from app.config import get_settings
from app.services.ai.base import AIProvider
from app.services.ai.cost_log import log_ai_cost
from app.services.ai.prompts import (
    build_title_prompt,
    build_title_retry_prompt,
    build_description_prompt,
    build_card_copy_prompt,
)
from app.services.ai.title_guard import TITLE_TARGET_CHARS, aplicar_limite

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
        # Batch: 1 titulo, saida curta, thinking DESLIGADO (thinkingBudget=0).
        # Com o teto antigo de 500, o modelo gastava o orcamento pensando e a
        # resposta chegava cortada (finish=MAX_TOKENS, 482 de thought, 14 de
        # texto). Budget zero resolve NA MAIORIA das vezes — mas e' melhor
        # esforco no gemini-3.8-flash: na amostragem de 2026-09-10, 1 em 4
        # chamadas veio com thoughtSignature e 366+ tokens de thought mesmo
        # assim, e uma estourou os 500. Por isso o teto e' 2000 nos dois modos:
        # custa zero thought quando o modelo obedece, e sai inteiro quando nao.
        text = await self._call(
            prompt, max_tokens=2000, temperature=0.6,
            thinking=not batch_mode, task="title",
        )
        parsed = json.loads(_extract_json(text))
        if not isinstance(parsed, dict):
            raise RuntimeError(f"Gemini não retornou um JSON de título válido: {text[:300]!r}")

        def _titulos_de(bruto: dict) -> list | None:
            if batch_mode:
                titulo = bruto.get("title", "")
                titulo = titulo.strip() if isinstance(titulo, str) else ""
                if not titulo:
                    return None
                return [{"title": titulo, "score": None, "rationale": "batch_auto"}]
            lista = bruto.get("titles")
            return lista if isinstance(lista, list) else None

        titulos = _titulos_de(parsed)
        if titulos is None:
            if batch_mode:
                # Vazio seguia ate `domain_discovery?q=` devolver 400, tres
                # tasks depois. Falha aqui, com o texto cru para diagnostico.
                raise RuntimeError(f"Gemini retornou título vazio em batch_mode: {text[:300]!r}")
            raise RuntimeError(f"Gemini não retornou a lista 'titles': {text[:300]!r}")

        async def _retentar() -> list | None:
            recusados = [
                t.get("title", "") for t in titulos
                if isinstance(t, dict) and isinstance(t.get("title"), str)
            ]
            resposta = await self._call(
                build_title_retry_prompt(prompt, recusados, TITLE_TARGET_CHARS),
                max_tokens=2000, temperature=0.6,
                thinking=not batch_mode, task="title",
            )
            novo = json.loads(_extract_json(resposta))
            return _titulos_de(novo) if isinstance(novo, dict) else None

        # O `[:60]` que vivia aqui era uma fatia cega: estouro de UM caractere
        # virava titulo com a ultima palavra mutilada, publicado sem aviso
        # (MLB7638983316, "...Wepink 200m"). Ver `title_guard`.
        return await aplicar_limite(titulos, retentar=_retentar)

    async def generate_description(self, listing_data: dict) -> str:
        prompt = build_description_prompt(listing_data)
        # Preventivo, mesma classe do bug do titulo e do card. Sonda com a
        # descricao real do T38 (budget 0, 5x): saida de 531-603 tokens,
        # thought 0. Teto 4000 e nao 2000 porque o budget 0 e' melhor esforco
        # no gemini-3.8-flash: um thought escapado (ja medido em 1125-1467
        # tokens) somado a saida de ~600 nao cabe em 2000.
        return await self._call(
            prompt, max_tokens=4000, temperature=0.6, thinking=False, task="description"
        )

    async def generate_card_copy(self, source: dict) -> dict:
        prompt = build_card_copy_prompt(source)
        # Saida curta e estruturada (2-3 cards com titulo + bullets): thinking
        # desligado, como no titulo em lote. Com thinking ligado e teto 1200, o
        # gemini-3.8-flash gastou 1125 tokens pensando e a copy chegou cortada
        # (finish=MAX_TOKENS) — o service devolveu [] e a posicao 3 (benefits_ai)
        # do T38 simplesmente nao saiu, sem erro. Teto 2000 porque o budget 0 e'
        # melhor esforco: quando o modelo pensa mesmo assim, ainda cabe.
        text = await self._call(
            prompt, max_tokens=2000, temperature=0.4, thinking=False, task="card_copy"
        )
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
