from abc import ABC, abstractmethod


class AIProvider(ABC):
    @abstractmethod
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
        """Retorna lista de {title, score, rationale}. batch_mode retorna lista com 1 item."""

    @abstractmethod
    async def generate_description(self, listing_data: dict) -> str:
        """Retorna HTML string"""

    @abstractmethod
    async def generate_card_copy(self, source: dict) -> dict:
        """Copy dos 3 cards de imagem. Recebe o dicionario montado por
        `image_card_copy_service._build_source()` e devolve
        {"benefits": {...}, "usage": {...}, "specs": {...}}, cada valor
        {"title": str, "bullets": list[str]}."""
