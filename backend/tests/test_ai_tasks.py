import pytest
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch


@asynccontextmanager
async def _mock_session(mock_db):
    yield mock_db


class TestGenerateDescriptionIdempotency:
    @pytest.mark.asyncio
    async def test_skips_when_status_not_generating_description(self):
        """Guard defends against a Celery chain proceeding past a pause
        (e.g. pending_raw_photos) set by the previous link
        without raising — the chain still advances since the task returned
        normally, so this step must no-op instead of overwriting the pause."""
        from app.workers.tasks.ai_tasks import _generate_description_async

        mock_listing = MagicMock()
        mock_listing.status = "pending_image_approval"  # not generating_description

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one.return_value = mock_listing
        mock_db.execute = AsyncMock(return_value=mock_result)

        with patch("app.database.worker_session", lambda: _mock_session(mock_db)), \
             patch("app.services.ai.service.get_ai_provider") as mock_provider_fn:
            result = await _generate_description_async("listing-id")

        assert result == {"listing_id": "listing-id", "skipped": True}
        # Guard aborta antes de qualquer geração de IA; apenas 1 execute (SELECT Listing).
        assert mock_db.execute.await_count == 1
        mock_provider_fn.assert_not_called()

    @pytest.mark.asyncio
    async def test_proceeds_when_status_is_generating_description(self):
        """Verificação negativa: guard NÃO aborta quando status está correto."""
        from app.workers.tasks.ai_tasks import _generate_description_async

        mock_listing = MagicMock()
        mock_listing.id = "lid"
        mock_listing.status = "generating_description"
        mock_listing.selected_title = "Title"
        mock_listing.sku_brand = "Brand"
        mock_listing.sku_description = "Desc"
        mock_listing.condition = "new"
        mock_listing.created_via = "manual"

        mock_db = AsyncMock()
        execute_calls = [0]

        async def execute_side(stmt):
            execute_calls[0] += 1
            r = MagicMock()
            if execute_calls[0] == 1:      # SELECT Listing
                r.scalar_one = MagicMock(return_value=mock_listing)
            elif execute_calls[0] == 2:    # SELECT ListingAttribute
                r.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
            else:                          # SELECT ListingDescription
                r.scalar_one_or_none = MagicMock(return_value=None)
            return r

        mock_db.execute = execute_side
        mock_db.add = MagicMock()
        mock_db.commit = AsyncMock()

        mock_ai = AsyncMock()
        mock_ai.generate_description = AsyncMock(return_value="<p>desc</p>")

        with patch("app.database.worker_session", lambda: _mock_session(mock_db)), \
             patch("app.services.ai.service.get_ai_provider", return_value=mock_ai) as mock_provider_fn:
            result = await _generate_description_async("lid")

        assert result != {"listing_id": "lid", "skipped": True}
        mock_provider_fn.assert_called_once()
        mock_ai.generate_description.assert_called_once()
