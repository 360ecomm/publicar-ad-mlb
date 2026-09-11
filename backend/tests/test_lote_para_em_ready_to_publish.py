"""Ponta a ponta: um listing de LOTE (`created_via="batch"`) que teve as
imagens aprovadas por um humano (via `approve_images` ou
`bulk_approve_images`) termina em `ready_to_publish`, exatamente como um
listing manual — nunca em `publishing`, e `publish_listing` nunca é
despachado por caminho automático. Publicar é sempre ação humana
(`trigger_publish` / `bulk_publish`).
"""
import uuid
import pytest
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch


@asynccontextmanager
async def _mock_session(mock_db):
    yield mock_db


def _make_batch_listing(status: str = "pending_image_approval"):
    listing = MagicMock()
    listing.id = uuid.uuid4()
    listing.seller_id = uuid.uuid4()
    listing.status = status
    listing.created_via = "batch"
    listing.selected_title = "Título"
    listing.sku_brand = "Marca"
    listing.sku_description = "Descrição"
    listing.condition = "new"
    return listing


async def _run_generate_description(mock_listing):
    """Executa `_generate_description_async` com `worker_session` e o provider
    de IA mockados, devolvendo os mocks de `publish_listing` (delay e si)."""
    from app.workers.tasks.ai_tasks import _generate_description_async

    mock_db = AsyncMock()
    execute_calls = [0]

    async def execute_side(stmt):
        execute_calls[0] += 1
        r = MagicMock()
        if execute_calls[0] == 1:      # SELECT Listing
            r.scalar_one = MagicMock(return_value=mock_listing)
        elif execute_calls[0] == 2:    # SELECT ListingAttribute
            r.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
        else:                           # SELECT ListingDescription
            r.scalar_one_or_none = MagicMock(return_value=None)
        return r

    mock_db.execute = execute_side
    mock_db.add = MagicMock()
    mock_db.commit = AsyncMock()

    with patch("app.database.worker_session", lambda: _mock_session(mock_db)), \
         patch("app.services.ai.service.get_ai_provider") as mock_provider_fn, \
         patch("app.workers.tasks.publish_tasks.publish_listing") as mock_pl:
        mock_ai = AsyncMock()
        mock_ai.generate_description = AsyncMock(return_value="<p>desc</p>")
        mock_provider_fn.return_value = mock_ai
        await _generate_description_async(str(mock_listing.id))

    return mock_pl


class TestApproveImagesLoteTerminaEmReadyToPublish:
    """`ListingService.approve_images` → `generate_description.delay` →
    `_generate_description_async` para um listing de LOTE."""

    @pytest.mark.asyncio
    async def test_approve_images_to_ready_to_publish(self):
        from app.services.listing_service import ListingService

        mock_listing = _make_batch_listing("pending_image_approval")

        mock_img = MagicMock()
        mock_img.id = uuid.uuid4()
        mock_img.kind = "presentation_ai"
        mock_img.approved = False
        mock_img.ml_picture_id = "ML123"

        mock_db = AsyncMock()

        async def execute_side(stmt):
            r = MagicMock()
            r.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=[mock_img])))
            return r

        mock_db.execute = execute_side
        mock_db.commit = AsyncMock()
        mock_db.add = MagicMock()

        with patch("app.workers.tasks.ai_tasks.generate_description") as mock_gen_desc:
            svc = ListingService(mock_db, mock_listing.seller_id)
            await svc.approve_images(mock_listing, [mock_img.id], user_id=uuid.uuid4())

        # Etapa 1: aprovação de imagem avança para generating_description e
        # despacha generate_description com o id do listing.
        assert mock_listing.status == "generating_description"
        mock_gen_desc.delay.assert_called_once_with(str(mock_listing.id))

        # Etapa 2: o worker roda de fato e termina em ready_to_publish, sem
        # jamais despachar publish_listing.
        mock_pl = await _run_generate_description(mock_listing)

        assert mock_listing.status == "ready_to_publish"
        mock_pl.delay.assert_not_called()
        mock_pl.si.assert_not_called()


class TestBulkApproveImagesLoteTerminaEmReadyToPublish:
    """`ListingService.bulk_approve_images` → `generate_description.delay` →
    `_generate_description_async` para um listing de LOTE."""

    @pytest.mark.asyncio
    async def test_bulk_approve_images_to_ready_to_publish(self):
        from app.services.listing_service import ListingService

        mock_listing = _make_batch_listing("pending_image_approval")

        mock_db = AsyncMock()
        execute_calls = [0]

        async def execute_side(stmt):
            execute_calls[0] += 1
            r = MagicMock()
            if execute_calls[0] == 1:   # SELECT Listing (seller_id + id)
                r.scalar_one_or_none = MagicMock(return_value=mock_listing)
            else:                        # UPDATE em massa das ListingImage
                r.rowcount = 1
            return r

        mock_db.execute = execute_side
        mock_db.commit = AsyncMock()
        mock_db.add = MagicMock()

        with patch("app.workers.tasks.ai_tasks.generate_description") as mock_gen_desc:
            svc = ListingService(mock_db, mock_listing.seller_id)
            result = await svc.bulk_approve_images([mock_listing.id], user_id=uuid.uuid4())

        assert result.processed == 1
        assert mock_listing.status == "generating_description"
        mock_gen_desc.delay.assert_called_once_with(str(mock_listing.id))

        mock_pl = await _run_generate_description(mock_listing)

        assert mock_listing.status == "ready_to_publish"
        mock_pl.delay.assert_not_called()
        mock_pl.si.assert_not_called()
