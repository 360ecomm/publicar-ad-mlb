import asyncio
import logging
import pytest
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch



@asynccontextmanager
async def _mock_session(mock_db):
    yield mock_db


def _make_mock_listing():
    listing = MagicMock()
    listing.status = "generating_images"
    listing.error_message = None
    return listing


class TestMarkFailed:
    @pytest.mark.asyncio
    async def test_sets_status_and_error_message(self):
        from app.workers.tasks.image_tasks import _mark_failed

        listing = _make_mock_listing()
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = listing

        async def async_execute(*args, **kwargs):
            return mock_result

        mock_db.execute = async_execute

        with patch("app.database.worker_session", lambda: _mock_session(mock_db)):
            await _mark_failed("abc-123", "something went wrong")

        assert listing.status == "failed"
        assert listing.error_message == "something went wrong"
        mock_db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_truncates_long_error_to_500_chars(self):
        from app.workers.tasks.image_tasks import _mark_failed

        listing = _make_mock_listing()
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = listing

        async def async_execute(*args, **kwargs):
            return mock_result

        mock_db.execute = async_execute

        long_error = "x" * 1000
        with patch("app.database.worker_session", lambda: _mock_session(mock_db)):
            await _mark_failed("abc-123", long_error)

        assert len(listing.error_message) == 500

    @pytest.mark.asyncio
    async def test_db_failure_logs_and_does_not_propagate(self, caplog):
        from app.workers.tasks.image_tasks import _mark_failed

        @asynccontextmanager
        async def _exploding_session():
            raise RuntimeError("DB connection lost")
            yield  # noqa: unreachable — satisfies contextmanager protocol

        with patch("app.database.worker_session", _exploding_session):
            with caplog.at_level(logging.ERROR):
                await _mark_failed("abc-123", "original error")  # must not raise

        assert "abc-123" in caplog.text
        assert "original error" in caplog.text
        assert "DB connection lost" in caplog.text

    @pytest.mark.asyncio
    async def test_listing_not_found_does_not_raise(self):
        from app.workers.tasks.image_tasks import _mark_failed

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None

        async def async_execute(*args, **kwargs):
            return mock_result

        mock_db.execute = async_execute

        with patch("app.database.worker_session", lambda: _mock_session(mock_db)):
            await _mark_failed("abc-123", "error")  # must not raise


class TestFetchUploadToken:
    @pytest.mark.asyncio
    async def test_calls_get_valid_access_token(self):
        from app.workers.tasks.image_tasks import _fetch_upload_token

        mock_seller = MagicMock()
        mock_db = AsyncMock()

        with patch(
            "app.services.publish_service.get_valid_access_token",
            new_callable=AsyncMock,
            return_value="refreshed-token",
        ) as mock_fn:
            result = await _fetch_upload_token(mock_seller, mock_db)

        assert result == "refreshed-token"
        mock_fn.assert_called_once_with(mock_seller, mock_db)

    @pytest.mark.asyncio
    async def test_does_not_call_decrypt_value_directly(self):
        from app.workers.tasks.image_tasks import _fetch_upload_token

        with patch("app.core.security.decrypt_value") as mock_decrypt, \
             patch(
                 "app.services.publish_service.get_valid_access_token",
                 new_callable=AsyncMock,
                 return_value="tok",
             ):
            await _fetch_upload_token(MagicMock(), AsyncMock())

        mock_decrypt.assert_not_called()


class TestGenerateImagesIdempotency:
    @pytest.mark.asyncio
    async def test_skips_when_status_not_generating_images(self):
        from app.workers.tasks.image_tasks import _generate_images_async

        mock_listing = MagicMock()
        mock_listing.status = "pending_image_approval"  # já avançou

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one.return_value = mock_listing
        mock_db.execute = AsyncMock(return_value=mock_result)

        with patch("app.database.worker_session", lambda: _mock_session(mock_db)):
            result = await _generate_images_async("listing-id")

        assert result == {"listing_id": "listing-id", "skipped": True}
        # Guard aborta antes de qualquer engine ser resolvido; apenas 1 execute (SELECT Listing).
        assert mock_db.execute.await_count == 1

    @pytest.mark.asyncio
    async def test_proceeds_when_status_is_generating_images(self):
        """Verificação negativa: guard NÃO aborta quando status está correto —
        chega até a tentativa de geração (`_try_i2i_generation`)."""
        from app.workers.tasks.image_tasks import _generate_images_async

        mock_listing = MagicMock()
        mock_listing.status = "generating_images"
        mock_listing.sku_external_id = "SKU"
        mock_listing.seller_id = "sid"
        mock_listing.created_via = "manual"

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=MagicMock(scalar_one=MagicMock(return_value=mock_listing)))
        mock_db.commit = AsyncMock()

        with patch("app.database.worker_session", lambda: _mock_session(mock_db)),              patch("app.workers.tasks.image_tasks._fetch_upload_token", new_callable=AsyncMock, return_value="tok"),              patch("app.workers.tasks.image_tasks._try_i2i_generation", new_callable=AsyncMock, return_value=None) as i2i:
            await _generate_images_async("listing-id")

        i2i.assert_awaited_once()


def _png(width: int, height: int, color=(255, 255, 255)) -> bytes:
    import io as _io

    from PIL import Image

    buf = _io.BytesIO()
    Image.new("RGB", (width, height), color=color).save(buf, format="PNG")
    return buf.getvalue()


def _size_of(data: bytes):
    import io as _io

    from PIL import Image

    return Image.open(_io.BytesIO(data)).size


class TestPrepareImageForUpload:
    def test_valid_image_is_normalized_to_1200_and_approved(self):
        from app.workers.tasks.image_tasks import _prepare_image_for_upload

        prepared, verdict = _prepare_image_for_upload(_png(1536, 1024), requires_white_bg=False)

        assert verdict.is_valid
        assert prepared is not None
        assert _size_of(prepared) == (1200, 1200)

    def test_corrupted_bytes_are_rejected_with_reason(self):
        from app.workers.tasks.image_tasks import _prepare_image_for_upload

        prepared, verdict = _prepare_image_for_upload(b"garbage", requires_white_bg=False)

        assert prepared is None
        assert not verdict.is_valid
        assert verdict.reason

    def test_non_white_cover_rejected_only_when_category_requires_it(self):
        from app.workers.tasks.image_tasks import _prepare_image_for_upload

        colored = _png(1200, 1200, color=(200, 90, 40))

        prepared_ok, verdict_ok = _prepare_image_for_upload(colored, requires_white_bg=False)
        assert prepared_ok is not None
        assert verdict_ok.is_valid

        prepared_bad, verdict_bad = _prepare_image_for_upload(colored, requires_white_bg=True)
        assert prepared_bad is None
        assert "fundo" in verdict_bad.reason

    def test_upscales_small_image_above_ml_minimum(self):
        """520x520 passa no minimo de 500 e sai padronizada em 1200."""
        from app.workers.tasks.image_tasks import _prepare_image_for_upload

        prepared, verdict = _prepare_image_for_upload(_png(520, 520), requires_white_bg=False)

        assert verdict.is_valid
        assert _size_of(prepared) == (1200, 1200)
