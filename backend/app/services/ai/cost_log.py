"""Log estruturado de custo por chamada de IA.

Uma linha por chamada, no logger `ai_cost`, no formato `ai_cost k=v k=v ...`:

    ai_cost provider=gemini task=title model=gemini-3.8-flash listing_id=... sku=37
            input_tokens=120 output_tokens=19 thought_tokens=0 total_tokens=139
    ai_cost provider=openai task=image_edit model=gpt-image-2 listing_id=... sku=37
            images=1 input_images=2 size=1200x1200 quality=medium
            input_tokens=523 output_tokens=1056 total_tokens=1579

Sem dashboard nem agregacao: o objetivo e' que o dado real de custo por SKU,
dos dois provedores, comece a se acumular no log a partir de hoje, e possa
ser somado depois com `grep ai_cost | awk`.

O listing/SKU vem de um `ContextVar`, fixado pelo worker no inicio da task
(`set_cost_context`), em vez de ser passado por parametro por todas as
camadas ate o provider — o provider e o motor de imagem nao conhecem o
listing, e nao precisam conhecer. Cada `asyncio.run` de task Celery roda o
seu proprio contexto, entao uma task nao enxerga o listing da outra.
"""
import contextvars
import logging

logger = logging.getLogger("ai_cost")

_ctx: contextvars.ContextVar[dict | None] = contextvars.ContextVar("ai_cost_ctx", default=None)


def set_cost_context(*, listing_id, sku) -> None:
    """Fixa o listing/SKU que as proximas chamadas de IA desta task devem carregar."""
    _ctx.set({"listing_id": None if listing_id is None else str(listing_id), "sku": sku})


def cost_context() -> dict:
    return dict(_ctx.get() or {"listing_id": None, "sku": None})


def log_ai_cost(*, provider: str, task: str, model: str | None, **units) -> None:
    """Emite a linha. `units` sao as unidades cobradas (tokens, imagens...).

    Nunca levanta: custo e' telemetria, e uma falha aqui nao pode derrubar
    a chamada que ja foi paga.
    """
    try:
        ctx = cost_context()
        campos = {"provider": provider, "task": task, "model": model,
                  "listing_id": ctx["listing_id"], "sku": ctx["sku"], **units}
        logger.info("ai_cost " + " ".join(f"{k}={v}" for k, v in campos.items()))
    except Exception:  # pragma: no cover - defensivo
        logger.exception("ai_cost falhou ao registrar")
