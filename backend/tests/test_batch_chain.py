import pytest
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch, call


@asynccontextmanager
async def _mock_session(mock_db):
    yield mock_db


class TestCategoryTaskChainDispatch:
    @pytest.mark.asyncio
    async def test_dispatches_chain_when_update_succeeds(self):
        """Quando o UPDATE atômico altera 1 linha, a chain é despachada."""
        from app.workers.tasks.category_tasks import _predict_category_async

        mock_listing = MagicMock()
        mock_listing.created_via = "batch"
        mock_listing.status = "pending_description"
        mock_listing.ml_category_id = "MLB1055"

        # UPDATE retorna rowcount=1 (ganhou a corrida)
        mock_update_result = MagicMock()
        mock_update_result.rowcount = 1

        mock_db = AsyncMock()
        execute_calls = [0]

        async def execute_side(stmt):
            execute_calls[0] += 1
            if execute_calls[0] == 1:  # SELECT Listing
                r = MagicMock()
                r.scalar_one = MagicMock(return_value=mock_listing)
                return r
            else:  # UPDATE atômico
                return mock_update_result

        mock_db.execute = execute_side

        mock_chain_instance = MagicMock()

        with patch("app.database.worker_session", lambda: _mock_session(mock_db)), \
             patch("app.services.category_service.CategoryService") as mock_cat_cls, \
             patch("celery.chain", return_value=mock_chain_instance) as mock_chain_fn:
            mock_cat = AsyncMock()
            mock_cat.predict_and_save = AsyncMock()
            mock_cat_cls.return_value = mock_cat
            await _predict_category_async("listing-id")

        mock_chain_fn.assert_called_once()
        mock_chain_instance.delay.assert_called_once()

    @pytest.mark.asyncio
    async def test_does_not_dispatch_when_update_returns_zero(self):
        """Quando rowcount=0 (outro worker ganhou), não despacha nada."""
        from app.workers.tasks.category_tasks import _predict_category_async

        mock_listing = MagicMock()
        mock_listing.created_via = "batch"
        mock_listing.status = "pending_description"
        mock_listing.ml_category_id = "MLB1055"

        mock_update_result = MagicMock()
        mock_update_result.rowcount = 0  # outro worker ganhou

        mock_db = AsyncMock()
        execute_calls = [0]

        async def execute_side(stmt):
            execute_calls[0] += 1
            if execute_calls[0] == 1:
                r = MagicMock()
                r.scalar_one = MagicMock(return_value=mock_listing)
                return r
            else:
                return mock_update_result

        mock_db.execute = execute_side

        with patch("app.database.worker_session", lambda: _mock_session(mock_db)), \
             patch("app.services.category_service.CategoryService") as mock_cat_cls, \
             patch("celery.chain") as mock_chain_fn:
            mock_cat = AsyncMock()
            mock_cat.predict_and_save = AsyncMock()
            mock_cat_cls.return_value = mock_cat
            await _predict_category_async("listing-id")

        mock_chain_fn.assert_not_called()

    @pytest.mark.asyncio
    async def test_does_not_dispatch_when_not_batch(self):
        """Flow manual (created_via != 'batch') não despacha chain."""
        from app.workers.tasks.category_tasks import _predict_category_async

        mock_listing = MagicMock()
        mock_listing.created_via = "manual"
        mock_listing.status = "pending_description"
        mock_listing.ml_category_id = "MLB1055"

        mock_db = AsyncMock()

        async def execute_side(stmt):
            r = MagicMock()
            r.scalar_one = MagicMock(return_value=mock_listing)
            return r

        mock_db.execute = execute_side

        with patch("app.database.worker_session", lambda: _mock_session(mock_db)), \
             patch("app.services.category_service.CategoryService") as mock_cat_cls, \
             patch("celery.chain") as mock_chain_fn:
            mock_cat = AsyncMock()
            mock_cat.predict_and_save = AsyncMock()
            mock_cat_cls.return_value = mock_cat
            await _predict_category_async("listing-id")

        mock_chain_fn.assert_not_called()


class TestSubmitAttributesChainDispatch:
    @pytest.mark.asyncio
    async def test_dispatches_chain_when_batch_and_pending_description(self):
        """submit_attributes em modo batch com pending_description despacha chain."""
        from app.services.listing_service import ListingService
        from app.models.listing import Listing as ListingModel

        mock_listing = MagicMock(spec=ListingModel)
        mock_listing.id = "lid"
        mock_listing.seller_id = "sid"
        mock_listing.status = "pending_seller_attributes"
        mock_listing.created_via = "batch"

        mock_db = AsyncMock()
        execute_calls = [0]

        # Simula: sem imagem aprovada, sem descrição (new_status = pending_description)
        # depois: UPDATE atômico com rowcount=1
        # submitted=[] → nenhum select ListingAttribute ocorre; sequência real:
        #   call 1: select ListingImage (approved)
        #   call 2: select ListingDescription
        #   call 3: UPDATE atômico
        async def execute_side(stmt):
            execute_calls[0] += 1
            r = MagicMock()
            if execute_calls[0] == 1:    # select ListingImage (approved)
                r.scalars = MagicMock(return_value=MagicMock(first=MagicMock(return_value=None)))
                return r
            if execute_calls[0] == 2:    # select ListingDescription
                r.scalar_one_or_none = MagicMock(return_value=None)
                return r
            # UPDATE atômico
            r.rowcount = 1
            return r

        mock_db.execute = execute_side
        mock_db.commit = AsyncMock()

        mock_chain_instance = MagicMock()

        with patch("celery.chain", return_value=mock_chain_instance) as mock_chain_fn:
            svc = ListingService(mock_db)
            await svc.submit_attributes(mock_listing, [])

        mock_chain_fn.assert_called_once()
        mock_chain_instance.delay.assert_called_once()


class TestRemovedInternalDispatch:
    """Garante que tasks no batch path não mais chamam .delay() internamente.
    A chain (Task 3) é responsável por despachar os próximos steps.
    """

    # O teste do "reuse path" (imagens copiadas do indice SKU→imagem) saiu
    # junto com o proprio caminho de reuso, removido em 2026-09-10 — ver
    # tests/test_sem_reuso_de_imagem.py.

    @pytest.mark.asyncio
    async def test_generate_description_batch_termina_em_ready_to_publish(self):
        """generate_description em batch termina em 'ready_to_publish', igual ao fluxo manual,
        e NÃO despacha publish_listing. Publicação em lote só acontece por ação humana
        (trigger_publish / bulk_publish)."""
        from app.workers.tasks.ai_tasks import _generate_description_async

        mock_listing = MagicMock()
        mock_listing.id = "lid"
        mock_listing.status = "generating_description"
        mock_listing.created_via = "batch"
        mock_listing.selected_title = "Title"
        mock_listing.sku_brand = "Brand"
        mock_listing.sku_description = "Desc"
        mock_listing.condition = "new"

        mock_db = AsyncMock()
        execute_calls = [0]

        async def execute_side(stmt):
            execute_calls[0] += 1
            r = MagicMock()
            if execute_calls[0] == 1:   # SELECT Listing
                r.scalar_one = MagicMock(return_value=mock_listing)
            elif execute_calls[0] == 2: # SELECT ListingAttribute
                r.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
            else:                        # SELECT ListingDescription
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
            await _generate_description_async("lid")

        mock_pl.delay.assert_not_called()
        mock_pl.si.assert_not_called()
        assert mock_listing.status == "ready_to_publish"
