import pytest
import httpx
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.seller_image_source_service import (
    resolve_listing_skus,
    fetch_raw_photos,
    fetch_all_raw_photos,
)


class TestResolveListingSkus:
    @pytest.mark.asyncio
    async def test_returns_single_sku_list(self):
        listing = MagicMock()
        listing.sku_external_id = "SKU0001"
        assert await resolve_listing_skus(listing) == ["SKU0001"]

    @pytest.mark.asyncio
    async def test_returns_empty_list_when_no_sku(self):
        listing = MagicMock()
        listing.sku_external_id = None
        assert await resolve_listing_skus(listing) == []


def _mock_response(status_code: int, content: bytes = b"") -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.content = content
    return resp


class TestFetchRawPhotos:
    @pytest.mark.asyncio
    async def test_returns_both_photos_when_both_exist(self):
        # A 3a resposta 404 nao e enfeite: a descoberta sonda `-3.jpg` para
        # saber se o seller tem fotos alem do minimo. Seller com exatamente 2
        # devolve 404 ali, e e isso que encerra a busca.
        mock_get = AsyncMock(side_effect=[
            _mock_response(200, b"photo1-bytes"),
            _mock_response(200, b"photo2-bytes"),
            _mock_response(404),  # -3.jpg
            _mock_response(404),  # -3.png
            _mock_response(404),  # -3.webp — encerra a descoberta
        ])
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client_cls.return_value.__aenter__.return_value.get = mock_get
            result = await fetch_raw_photos("https://pub-xxx.r2.dev/sku", "SKU0001")

        assert result == [b"photo1-bytes", b"photo2-bytes"]
        assert mock_get.await_args_list[0].args[0] == "https://pub-xxx.r2.dev/sku/SKU0001-1.jpg"
        assert mock_get.await_args_list[1].args[0] == "https://pub-xxx.r2.dev/sku/SKU0001-2.jpg"

    @pytest.mark.asyncio
    async def test_returns_none_when_second_photo_missing(self):
        mock_get = AsyncMock(side_effect=[
            _mock_response(200, b"photo1-bytes"),
            _mock_response(404),  # -2.jpg
            _mock_response(404),  # -2.png
            _mock_response(404),  # -2.webp: obrigatoria ausente em todo formato
        ])
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client_cls.return_value.__aenter__.return_value.get = mock_get
            result = await fetch_raw_photos("https://pub-xxx.r2.dev/sku", "SKU0001")

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_timeout(self):
        mock_get = AsyncMock(side_effect=httpx.TimeoutException("timed out"))
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client_cls.return_value.__aenter__.return_value.get = mock_get
            result = await fetch_raw_photos("https://pub-xxx.r2.dev/sku", "SKU0001")

        assert result is None


class TestFetchAllRawPhotos:
    @pytest.mark.asyncio
    async def test_returns_dict_when_all_skus_have_photos(self):
        with patch(
            "app.services.seller_image_source_service.fetch_raw_photos",
            new_callable=AsyncMock,
            side_effect=[[b"s1-1", b"s1-2"], [b"s2-1", b"s2-2"]],
        ):
            result = await fetch_all_raw_photos("https://pub-xxx.r2.dev/sku", ["SKU0001", "SKU0002"])

        assert result == {"SKU0001": [b"s1-1", b"s1-2"], "SKU0002": [b"s2-1", b"s2-2"]}

    @pytest.mark.asyncio
    async def test_returns_none_all_or_nothing_when_any_sku_missing_photos(self):
        with patch(
            "app.services.seller_image_source_service.fetch_raw_photos",
            new_callable=AsyncMock,
            side_effect=[[b"s1-1", b"s1-2"], None],
        ):
            result = await fetch_all_raw_photos("https://pub-xxx.r2.dev/sku", ["SKU0001", "SKU0002"])

        assert result is None
