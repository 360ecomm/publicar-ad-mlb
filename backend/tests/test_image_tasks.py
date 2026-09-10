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


def _make_i2i_mocks():
    """Mocks compartilhados do caminho image-to-image."""
    mock_config = MagicMock()
    mock_config.raw_base_url = "https://pub-xxx.r2.dev/sku"

    mock_db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_config
    mock_db.execute = AsyncMock(return_value=mock_result)
    added = []
    mock_db.add = MagicMock(side_effect=added.append)

    listing = MagicMock()
    listing.id = "lid"
    listing.seller_id = "sid"
    listing.sku_external_id = "SKU0001"
    listing.created_via = "manual"

    return mock_db, added, listing


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


class TestUploadSkipsInvalidImages:
    """Uma imagem ruim vira linha validation_failed; as boas seguem para o ML."""

    @pytest.mark.asyncio
    async def test_bad_image_recorded_and_good_images_still_uploaded(self):
        from app.workers.tasks.image_tasks import _try_i2i_generation

        mock_db, added, listing = _make_i2i_mocks()

        with patch(
            "app.workers.tasks.image_tasks._append_benefit_cards",
            new_callable=AsyncMock,
            return_value=0,
        ), patch(
            "app.services.seller_image_source_service.fetch_all_raw_photos",
            new_callable=AsyncMock,
            return_value={"SKU0001": [b"raw1"]},
        ), patch(
            "app.services.image_engines.openai_edit_engine.OpenAIEditEngine"
        ) as mock_engine_cls, patch(
            "app.workers.tasks.image_tasks._resolve_requires_white_bg",
            new_callable=AsyncMock,
            return_value=False,
        ), patch(
            "app.services.image_service.MLPictureService"
        ) as mock_ml_cls:
            mock_engine_cls.return_value.edit = AsyncMock(
                return_value=[_png(1200, 1200), b"corrupted"]
            )
            mock_ml_cls.return_value.upload = AsyncMock(return_value="pic1")

            saved = await _try_i2i_generation(mock_db, listing, MagicMock(), "token")

        assert saved == 1, "so a imagem valida conta para o total"
        assert mock_ml_cls.return_value.upload.await_count == 1, "a ruim nao foi enviada ao ML"

        failed = [o for o in added if getattr(o, "status", None) == "validation_failed"]
        assert len(failed) == 1
        assert failed[0].ml_picture_id is None
        assert failed[0].validation_error

    @pytest.mark.asyncio
    async def test_all_images_invalid_yields_zero_saved(self):
        """Zero imagens validas -> chamador bloqueia a publicacao (comportamento existente)."""
        from app.workers.tasks.image_tasks import _try_i2i_generation

        mock_db, added, listing = _make_i2i_mocks()

        with patch(
            "app.workers.tasks.image_tasks._append_benefit_cards",
            new_callable=AsyncMock,
            return_value=0,
        ), patch(
            "app.services.seller_image_source_service.fetch_all_raw_photos",
            new_callable=AsyncMock,
            return_value={"SKU0001": [b"raw1"]},
        ), patch(
            "app.services.image_engines.openai_edit_engine.OpenAIEditEngine"
        ) as mock_engine_cls, patch(
            "app.workers.tasks.image_tasks._resolve_requires_white_bg",
            new_callable=AsyncMock,
            return_value=False,
        ), patch(
            "app.services.image_service.MLPictureService"
        ) as mock_ml_cls:
            mock_engine_cls.return_value.edit = AsyncMock(return_value=[b"bad", b"worse"])
            mock_ml_cls.return_value.upload = AsyncMock(return_value="pic1")

            saved = await _try_i2i_generation(mock_db, listing, MagicMock(), "token")

        assert saved == 0
        assert mock_ml_cls.return_value.upload.await_count == 0
        assert len([o for o in added if getattr(o, "status", None) == "validation_failed"]) == 2

    @pytest.mark.asyncio
    async def test_white_bg_checked_while_cover_slot_is_unfilled(self):
        """Fundo colorido numa categoria que exige branco: reprova a capa."""
        from app.workers.tasks.image_tasks import _try_i2i_generation

        mock_db, added, listing = _make_i2i_mocks()
        colored = _png(1200, 1200, color=(200, 90, 40))

        with patch(
            "app.workers.tasks.image_tasks._append_benefit_cards",
            new_callable=AsyncMock,
            return_value=0,
        ), patch(
            "app.services.seller_image_source_service.fetch_all_raw_photos",
            new_callable=AsyncMock,
            return_value={"SKU0001": [b"raw1"]},
        ), patch(
            "app.services.image_engines.openai_edit_engine.OpenAIEditEngine"
        ) as mock_engine_cls, patch(
            "app.workers.tasks.image_tasks._resolve_requires_white_bg",
            new_callable=AsyncMock,
            return_value=True,
        ), patch(
            "app.services.image_service.MLPictureService"
        ) as mock_ml_cls:
            mock_engine_cls.return_value.edit = AsyncMock(return_value=[colored, colored])
            mock_ml_cls.return_value.upload = AsyncMock(return_value="pic1")

            saved = await _try_i2i_generation(mock_db, listing, MagicMock(), "token")

        failed = [o for o in added if getattr(o, "status", None) == "validation_failed"]
        assert saved == 0
        assert len(failed) == 2, "enquanto a capa nao for preenchida, a checagem continua"
        assert all("fundo" in o.validation_error for o in failed)

    @pytest.mark.asyncio
    async def test_white_bg_not_enforced_on_images_after_the_cover(self):
        """Capa branca ocupa sort_order 0; a 2a imagem colorida e aceita."""
        from app.workers.tasks.image_tasks import _try_i2i_generation

        mock_db, added, listing = _make_i2i_mocks()

        with patch(
            "app.workers.tasks.image_tasks._append_benefit_cards",
            new_callable=AsyncMock,
            return_value=0,
        ), patch(
            "app.services.seller_image_source_service.fetch_all_raw_photos",
            new_callable=AsyncMock,
            return_value={"SKU0001": [b"raw1"]},
        ), patch(
            "app.services.image_engines.openai_edit_engine.OpenAIEditEngine"
        ) as mock_engine_cls, patch(
            "app.workers.tasks.image_tasks._resolve_requires_white_bg",
            new_callable=AsyncMock,
            return_value=True,
        ), patch(
            "app.services.image_service.MLPictureService"
        ) as mock_ml_cls:
            mock_engine_cls.return_value.edit = AsyncMock(
                return_value=[_png(1200, 1200), _png(1200, 1200, color=(200, 90, 40))]
            )
            mock_ml_cls.return_value.upload = AsyncMock(side_effect=["pic1", "pic2"])

            saved = await _try_i2i_generation(mock_db, listing, MagicMock(), "token")

        assert saved == 2, "a 2a imagem nao e capa, entao o fundo colorido passa"
        assert [o for o in added if getattr(o, "status", None) == "validation_failed"] == []


class TestBatchApprovalNeverApprovesCandidates:
    """Frentes A/B: `cover_ai` e `specs_ai` nascem `approved=False` e so viram
    capa por acao humana (`promote_cover`). A aprovacao em massa do ramo batch
    seleciona por `status == "uploaded"` — que e exatamente o status de um
    candidato que subiu com sucesso — entao sem filtro de `kind` uma SEGUNDA
    passada do pipeline (retry, retomada de pending_raw_photos) aprovaria o candidato, e
    imagem aprovada com `ml_picture_id` vai direto para o payload de
    publicacao no ML real.
    """

    @staticmethod
    def _image(kind, status="uploaded"):
        from app.models.listing_image import ListingImage

        img = ListingImage(kind=kind, status=status, approved=False, sort_order=0)
        img.ml_picture_id = f"pic-{kind}"
        return img

    @pytest.mark.asyncio
    async def test_candidate_handed_over_by_the_session_is_not_approved(self):
        from app.workers.tasks.image_tasks import _generate_images_async

        listing = MagicMock()
        listing.status = "generating_images"
        listing.sku_external_id = None
        listing.seller_id = "sid"
        listing.created_via = "batch"

        individual = self._image("individual")
        cover_ai = self._image("cover_ai")
        specs_ai = self._image("specs_ai")

        statements = []

        async def execute_side(stmt):
            statements.append(stmt)
            r = MagicMock()
            if len(statements) == 1:      # SELECT Listing
                r.scalar_one = MagicMock(return_value=listing)
            elif len(statements) == 2:    # SELECT Seller
                r.scalar_one = MagicMock(return_value=MagicMock())
            else:                         # SELECT ListingImage (aprovacao em massa)
                r.scalars.return_value.all.return_value = [individual, cover_ai, specs_ai]
            return r

        mock_db = AsyncMock()
        mock_db.execute = execute_side
        mock_db.commit = AsyncMock()

        with patch("app.database.worker_session", lambda: _mock_session(mock_db)), \
             patch(
                 "app.workers.tasks.image_tasks._fetch_upload_token",
                 new_callable=AsyncMock,
                 return_value="tok",
             ), \
             patch(
                 "app.workers.tasks.image_tasks._try_i2i_generation",
                 new_callable=AsyncMock,
                 return_value=4,
             ):
            result = await _generate_images_async("listing-id")

        assert result["source"] == "i2i"
        assert individual.approved is True, "imagem normal do pipeline deve ser aprovada"
        assert cover_ai.approved is False, "candidato cover_ai nunca pode ser auto-aprovado"
        assert specs_ai.approved is False, "candidato specs_ai nunca pode ser auto-aprovado"

    @pytest.mark.asyncio
    async def test_batch_approval_query_excludes_candidate_kinds_in_sql(self):
        """Complemento do teste acima: o banco nem devolve os candidatos."""
        from app.workers.tasks.image_tasks import _generate_images_async

        listing = MagicMock()
        listing.status = "generating_images"
        listing.sku_external_id = None
        listing.seller_id = "sid"
        listing.created_via = "batch"

        statements = []

        async def execute_side(stmt):
            statements.append(stmt)
            r = MagicMock()
            if len(statements) == 1:
                r.scalar_one = MagicMock(return_value=listing)
            elif len(statements) == 2:
                r.scalar_one = MagicMock(return_value=MagicMock())
            else:
                r.scalars.return_value.all.return_value = []
            return r

        mock_db = AsyncMock()
        mock_db.execute = execute_side
        mock_db.commit = AsyncMock()

        with patch("app.database.worker_session", lambda: _mock_session(mock_db)), \
             patch(
                 "app.workers.tasks.image_tasks._fetch_upload_token",
                 new_callable=AsyncMock,
                 return_value="tok",
             ), \
             patch(
                 "app.workers.tasks.image_tasks._try_i2i_generation",
                 new_callable=AsyncMock,
                 return_value=4,
             ):
            await _generate_images_async("listing-id")

        sql = str(statements[2])
        assert "listing_images.kind NOT IN" in sql, sql
        # `defer(image_bytes)` (Finding 6): o blob nao entra nesta query.
        assert "listing_images.image_bytes" not in sql, sql
