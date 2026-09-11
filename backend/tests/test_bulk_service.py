# backend/tests/test_bulk_service.py
import uuid
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call
from app.services.listing_service import ListingService
from app.schemas.bulk import BulkListingRequest, BulkAttributeRequest


def make_listing(status: str, seller_id=None):
    listing = MagicMock()
    listing.id = uuid.uuid4()
    listing.seller_id = seller_id or uuid.uuid4()
    listing.status = status
    listing.failed_step = None
    listing.selected_title = None
    return listing


def make_title(score: float | None, created_at=None):
    from datetime import datetime, timezone
    t = MagicMock()
    t.title_text = f"Título score={score}"
    t.ai_score = score
    t.selected = False
    t.created_at = created_at or datetime.now(timezone.utc)
    return t


def mock_execute_single(obj):
    result = MagicMock()
    result.scalar_one_or_none.return_value = obj
    result.scalars.return_value.all.return_value = []
    return result


@pytest.mark.asyncio
async def test_bulk_approve_titles_selects_highest_score():
    db = AsyncMock()
    seller_id = uuid.uuid4()
    listing = make_listing("pending_title_approval", seller_id)
    titles = [make_title(8.5), make_title(9.2), make_title(7.1)]

    execute_calls = [
        MagicMock(scalar_one_or_none=MagicMock(return_value=listing)),
        MagicMock(scalar_one_or_none=MagicMock(return_value=titles[1])),  # highest score
    ]
    db.execute = AsyncMock(side_effect=execute_calls)

    svc = ListingService(db, seller_id)
    with patch("app.workers.tasks.category_tasks.predict_category") as mock_task:
        mock_task.delay = MagicMock()
        result = await svc.bulk_approve_titles([listing.id])

    assert result.processed == 1
    assert result.failed == 0
    assert listing.selected_title == titles[1].title_text
    assert listing.status == "predicting_category"
    mock_task.delay.assert_called_once_with(str(listing.id))


@pytest.mark.asyncio
async def test_bulk_approve_titles_skips_wrong_status():
    db = AsyncMock()
    seller_id = uuid.uuid4()
    listing = make_listing("generating_title", seller_id)
    db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=listing)))

    svc = ListingService(db, seller_id)
    result = await svc.bulk_approve_titles([listing.id])

    assert result.processed == 0
    assert result.failed == 1
    assert result.results[0].error == "estado inválido"


@pytest.mark.asyncio
async def test_bulk_reject_titles_returns_to_draft():
    db = AsyncMock()
    seller_id = uuid.uuid4()
    listing = make_listing("pending_title_approval", seller_id)
    db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=listing)))

    svc = ListingService(db, seller_id)
    result = await svc.bulk_reject_titles([listing.id])

    assert result.processed == 1
    assert listing.status == "draft"
    assert listing.selected_title is None


@pytest.mark.asyncio
async def test_bulk_fill_attribute_advances_when_all_required_filled():
    db = AsyncMock()
    seller_id = uuid.uuid4()
    listing = make_listing("pending_seller_attributes", seller_id)

    # execute calls: 1 = get listing, 2 = update attribute, 3 = check unfilled
    execute_calls = [
        MagicMock(scalar_one_or_none=MagicMock(return_value=listing)),  # listing fetch
        MagicMock(rowcount=1),                                           # attr update
        MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))),  # no unfilled
    ]
    db.execute = AsyncMock(side_effect=execute_calls)

    svc = ListingService(db, seller_id)
    result = await svc.bulk_fill_attribute(
        listing_ids=[listing.id],
        attribute_id="BRAND",
        value_name="NSK",
        value_id=None,
    )

    assert result.processed == 1
    assert listing.status == "pending_description"


@pytest.mark.asyncio
async def test_bulk_fill_attribute_does_not_advance_when_unfilled_remain():
    db = AsyncMock()
    seller_id = uuid.uuid4()
    listing = make_listing("pending_seller_attributes", seller_id)
    unfilled_attr = MagicMock()

    execute_calls = [
        MagicMock(scalar_one_or_none=MagicMock(return_value=listing)),
        MagicMock(rowcount=1),
        MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[unfilled_attr])))),
    ]
    db.execute = AsyncMock(side_effect=execute_calls)

    svc = ListingService(db, seller_id)
    result = await svc.bulk_fill_attribute(
        listing_ids=[listing.id],
        attribute_id="BRAND",
        value_name="NSK",
        value_id=None,
    )

    assert result.processed == 1
    assert listing.status == "pending_seller_attributes"  # unchanged


@pytest.mark.asyncio
async def test_bulk_approve_images_transitions_and_dispatches():
    db = AsyncMock()
    seller_id = uuid.uuid4()
    listing = make_listing("pending_image_approval", seller_id)
    db.execute = AsyncMock(return_value=MagicMock(
        scalar_one_or_none=MagicMock(return_value=listing),
        rowcount=1,
    ))

    svc = ListingService(db, seller_id)
    with patch("app.workers.tasks.ai_tasks.generate_description") as mock_task:
        mock_task.delay = MagicMock()
        result = await svc.bulk_approve_images([listing.id])

    assert result.processed == 1
    assert listing.status == "generating_description"
    mock_task.delay.assert_called_once_with(str(listing.id))


@pytest.mark.asyncio
async def test_bulk_generate_images_dispatches_generate_images_only():
    """bulk_generate_images despacha só generate_images.delay (chain de 1
    elemento é ruído); generate_description/publish_listing nunca são
    chamados por este caminho — publicar é sempre ação humana."""
    db = AsyncMock()
    seller_id = uuid.uuid4()
    execute_result = MagicMock()
    execute_result.rowcount = 1
    db.execute = AsyncMock(return_value=execute_result)

    svc = ListingService(db, seller_id)
    listing_id = uuid.uuid4()
    with patch("app.workers.tasks.image_tasks.generate_images") as mock_task, \
         patch("app.workers.tasks.ai_tasks.generate_description") as mock_gd, \
         patch("app.workers.tasks.publish_tasks.publish_listing") as mock_pl, \
         patch("celery.chain") as mock_chain_fn:
        mock_task.delay = MagicMock()
        result = await svc.bulk_generate_images([listing_id])

    assert result.processed == 1
    mock_task.delay.assert_called_once_with(str(listing_id))
    mock_gd.si.assert_not_called()
    mock_gd.delay.assert_not_called()
    mock_pl.si.assert_not_called()
    mock_pl.delay.assert_not_called()
    mock_chain_fn.assert_not_called()


@pytest.mark.asyncio
async def test_bulk_publish_transitions_and_dispatches():
    db = AsyncMock()
    seller_id = uuid.uuid4()
    execute_result = MagicMock()
    execute_result.rowcount = 1
    db.execute = AsyncMock(return_value=execute_result)

    svc = ListingService(db, seller_id)
    listing_id = uuid.uuid4()
    with patch("app.workers.tasks.publish_tasks.publish_listing") as mock_task:
        mock_task.delay = MagicMock()
        result = await svc.bulk_publish([listing_id])

    assert result.processed == 1
    mock_task.delay.assert_called_once_with(str(listing_id))


@pytest.mark.asyncio
async def test_bulk_approve_images_never_approves_ai_candidates():
    """`bulk_approve_images` faz um UPDATE em massa por `listing_id`. Sem
    filtro de `kind` ele aprovaria tambem os candidatos `cover_ai`/`specs_ai`
    (que nascem `approved=False` e so viram capa por `promote_cover`), e
    imagem aprovada com `ml_picture_id` entra no payload de publicacao no ML.

    O UPDATE e emitido direto no banco (nao ha objeto ORM em memoria pra
    inspecionar), entao a asserção é sobre o SQL que a sessão recebeu."""
    db = AsyncMock()
    seller_id = uuid.uuid4()
    listing = make_listing("pending_image_approval", seller_id)
    statements = []

    async def execute_side(stmt):
        statements.append(stmt)
        return MagicMock(scalar_one_or_none=MagicMock(return_value=listing), rowcount=1)

    db.execute = execute_side

    svc = ListingService(db, seller_id)
    with patch("app.workers.tasks.ai_tasks.generate_description") as mock_task:
        mock_task.delay = MagicMock()
        await svc.bulk_approve_images([listing.id])

    update_sql = str(statements[1])
    assert "UPDATE listing_images" in update_sql, update_sql
    assert "listing_images.kind NOT IN" in update_sql, update_sql
