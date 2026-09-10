import pytest
from unittest.mock import AsyncMock, MagicMock

from app.schemas.seller_image_config import SellerImageConfigUpsert
from app.services.seller_image_config_service import SellerImageConfigService


class TestSellerImageConfigService:
    @pytest.mark.asyncio
    async def test_get_returns_none_when_not_configured(self):
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=mock_result)

        svc = SellerImageConfigService(mock_db, "seller-1")
        result = await svc.get()

        assert result is None

    @pytest.mark.asyncio
    async def test_upsert_creates_new_config(self):
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()
        mock_db.add = MagicMock()

        payload = SellerImageConfigUpsert(raw_base_url="https://pub-xxx.r2.dev/sku")

        svc = SellerImageConfigService(mock_db, "seller-1")
        cfg = await svc.upsert(payload)

        assert cfg.seller_id == "seller-1"
        assert cfg.raw_base_url == "https://pub-xxx.r2.dev/sku"
        mock_db.add.assert_called_once()
        mock_db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_upsert_updates_existing_config_raw_base_url(self):
        existing = MagicMock()
        existing.raw_base_url = "https://old-url/sku"

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = existing
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()
        mock_db.add = MagicMock()

        payload = SellerImageConfigUpsert(raw_base_url="https://new-url/sku")

        svc = SellerImageConfigService(mock_db, "seller-1")
        cfg = await svc.upsert(payload)

        assert cfg is existing
        assert cfg.raw_base_url == "https://new-url/sku"
        mock_db.add.assert_not_called()  # atualização, não criação
