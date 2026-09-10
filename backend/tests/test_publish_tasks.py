import pytest
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch


@asynccontextmanager
async def _mock_session(mock_db):
    yield mock_db


class TestPublishListingIdempotency:
    @pytest.mark.asyncio
    async def test_skips_when_status_not_publishing(self):
        """Guard defends against a Celery chain proceeding past a pause set
        by an earlier link (e.g. pending_raw_photos) without
        raising — this step must no-op instead of publishing an incomplete
        listing (e.g. zero images)."""
        from app.workers.tasks.publish_tasks import _publish_listing_async

        mock_listing = MagicMock()
        mock_listing.status = "ready_to_publish"  # not publishing

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one.return_value = mock_listing
        mock_db.execute = AsyncMock(return_value=mock_result)

        with patch("app.database.worker_session", lambda: _mock_session(mock_db)), \
             patch("app.services.publish_service.PublishService") as mock_publish_cls, \
             patch("app.services.publish_service.get_valid_access_token", new_callable=AsyncMock) as mock_token_fn:
            result = await _publish_listing_async("listing-id")

        assert result == {"listing_id": "listing-id", "skipped": True}
        # Guard aborta antes de buscar seller/atributos/imagens; apenas 1 execute (SELECT Listing).
        assert mock_db.execute.await_count == 1
        mock_token_fn.assert_not_called()
        mock_publish_cls.assert_not_called()

    @pytest.mark.asyncio
    async def test_proceeds_when_status_is_publishing(self):
        """Verificação negativa: guard NÃO aborta quando status está correto."""
        from app.workers.tasks.publish_tasks import _publish_listing_async

        mock_listing = MagicMock()
        mock_listing.id = "lid"
        mock_listing.status = "publishing"
        mock_listing.seller_id = "sid"

        mock_seller = MagicMock()

        mock_db = AsyncMock()
        execute_calls = [0]

        async def execute_side(stmt):
            execute_calls[0] += 1
            r = MagicMock()
            if execute_calls[0] == 1:      # SELECT Listing
                r.scalar_one = MagicMock(return_value=mock_listing)
            elif execute_calls[0] == 2:    # SELECT Seller
                r.scalar_one = MagicMock(return_value=mock_seller)
            elif execute_calls[0] == 3:    # SELECT ListingAttribute
                r.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
            elif execute_calls[0] == 4:    # SELECT ListingImage
                r.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
            else:                          # SELECT ListingDescription
                r.scalar_one_or_none = MagicMock(return_value=None)
            return r

        mock_db.execute = execute_side
        mock_db.commit = AsyncMock()

        with patch("app.database.worker_session", lambda: _mock_session(mock_db)), \
             patch(
                 "app.services.publish_service.get_valid_access_token",
                 new_callable=AsyncMock,
                 return_value="token",
             ) as mock_token_fn, \
             patch("app.services.publish_service.PublishService") as mock_publish_cls:
            mock_publish_cls.return_value.publish = AsyncMock(return_value="MLB123")
            result = await _publish_listing_async("lid")

        assert result != {"listing_id": "lid", "skipped": True}
        mock_token_fn.assert_called_once()
        mock_publish_cls.return_value.publish.assert_called_once()
        assert mock_listing.mlb_id == "MLB123"
