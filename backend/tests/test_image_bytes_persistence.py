"""Testes da Task 1 (SPEC frentes A/B — capa e ficha): persistencia dos bytes
da capa deterministica em `ListingImage.image_bytes`.

Escopo deliberadamente estreito: so a capa deterministica popula a coluna.
Individuais e cards continuam com `image_bytes=None` — a variante de capa
(frente futura) so precisa da capa, e persistir bytes de toda imagem seria
custo de armazenamento sem uso conhecido.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.image_service import ImageValidationResult


def _passthrough_prepare(image_bytes, requires_white_bg):
    """Substitui o pos-processamento + QA nos testes: aprova e devolve os bytes."""
    return image_bytes, ImageValidationResult(is_valid=True, errors=[])


def _make_listing():
    listing = MagicMock()
    listing.id = "lid"
    listing.seller_id = "sid"
    listing.sku_external_id = "SKU0001"
    listing.created_via = "manual"
    return listing


def _make_i2i_db(attributes=None):
    """Mock de sessao que responde tanto a query do SellerImageConfig quanto
    a query de ListingAttribute que a copy dos cards dispara."""
    mock_config = MagicMock()
    mock_config.raw_base_url = "https://pub-xxx.r2.dev/sku"

    mock_db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_config
    mock_db.execute = AsyncMock(return_value=mock_result)
    mock_db.execute.return_value.scalars.return_value.all.return_value = (
        attributes if attributes is not None else []
    )
    added = []
    mock_db.add = MagicMock(side_effect=added.append)
    return mock_db, added


class TestImageBytesNeverLoadedOnTheUiPath:
    """Finding 6: `image_bytes` e um JPEG q92 1200x1200 (300-600 KB), nao os
    ~100 KB que a triagem inicial assumiu. `GET /listings/{id}` e polado pela
    UI a cada 8s e nao usa nenhum byte — `ImageOut` nem tem o campo. Em
    producao (VPS de 2 vCPU / 7.8 GiB sem swap, compartilhada com outros dois
    bancos, uvicorn com 2 workers sob `mem_limit`) carregar alguns MB por
    request so pra descartar e desperdicio com consequencia real.

    O teste roda `_load_detail` de verdade e inspeciona a query que ela
    montou: se alguem voltar a escrever um `select(ListingImage)` cru nesta
    rota, `image_bytes` reaparece na lista de colunas e o teste quebra.
    """

    @staticmethod
    def _listing():
        from datetime import datetime, timezone
        from decimal import Decimal
        from uuid import uuid4

        from app.models.listing import Listing

        agora = datetime.now(timezone.utc)
        return Listing(
            id=uuid4(),
            sku_external_id="SKU0001",
            sku_brand="Marca",
            sku_description="Produto teste",
            selected_title=None,
            status="draft",
            created_via="manual",
            mlb_id=None,
            created_at=agora,
            updated_at=agora,
            price=Decimal("10.00"),
            stock_quantity=1,
            condition="new",
            listing_type_id="gold_special",
            ml_category_id=None,
            error_message=None,
        )

    @pytest.mark.asyncio
    async def test_get_listing_detail_query_defers_the_blob(self):
        from app.api.v1.endpoints.listings import _load_detail

        statements = []

        async def execute_side(stmt):
            statements.append(stmt)
            r = MagicMock()
            r.scalars.return_value.all.return_value = []
            r.scalar_one_or_none.return_value = None
            return r

        db = AsyncMock()
        db.execute = execute_side

        await _load_detail(db, self._listing())

        imagens_sql = [s for s in statements if "FROM listing_images" in str(s)]
        assert len(imagens_sql) == 1, statements
        sql = str(imagens_sql[0])
        assert "listing_images.image_bytes" not in sql, sql
        # sanity: as colunas que a UI realmente usa continuam vindo.
        assert "listing_images.ml_picture_id" in sql, sql
        assert "listing_images.sort_order" in sql, sql
