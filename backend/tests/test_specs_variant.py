"""Frente B: ficha tecnica renderizada por IA sob demanda + review_seconds.

Testes de `generate_specs_variant` no mesmo estilo de `test_cover_variant.py`
— db mockado com AsyncMock, engine patchado na origem (imports locais dentro
da funcao, entao o patch precisa mirar o modulo que define o nome, nao
`specs_variant_service`). Nao ha mais copy a patchar: os bullets vem de
`build_specs_card`, que roda de verdade sobre os atributos do mock.

`review_seconds` nao e' mais coluna de `ListingImage` (migration
c8d2f6a4e1b7) — o tempo de revisao vive so no `ListingReviewEvent` gravado
por `approve_images`. Os testes de `TestApproveImagesReviewSeconds` chamam
`ListingService.approve_images` de verdade (nao um mock do metodo) e
verificam o `ListingReviewEvent` passado a `db.add` — nao basta provar que um
mock generico foi chamado, o objeto tem que ser do tipo certo com os campos
certos.
"""
from unittest.mock import AsyncMock, MagicMock, patch
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.models.listing_image import ListingImage
from app.services.image_service import ImageValidationResult


def _make_listing():
    listing = MagicMock()
    listing.id = uuid4()
    return listing


def _make_cover(listing_id, image_bytes, source_sku="SKU0001"):
    cover = ListingImage(
        id=uuid4(),
        listing_id=listing_id,
        kind="cover_deterministic",
        approved=True,
        sort_order=0,
        status="uploaded",
        source_sku=source_sku,
        asset_key="asset-key-capa" if image_bytes is not None else None,
    )
    cover._bytes = image_bytes
    return cover

# R2 em memoria: o service le/grava bytes no bucket de ativos; aqui, `_bytes`
# no proprio mock da capa faz o papel do objeto no R2.
_R2 = {}


@pytest.fixture(autouse=True)
def _r2_em_memoria():
    async def _load(img):
        return getattr(img, "_bytes", None)

    store = AsyncMock(return_value="asset-key-teste")
    with patch("app.services.r2_asset_service.load_candidate_bytes", side_effect=_load), \
         patch("app.services.r2_asset_service.store_candidate_bytes", store):
        _R2["store"] = store
        yield



def _specs_attributes():
    """Atributos que rendem uma ficha tecnica.

    A Frente B deixou de pedir a copy ao LLM: os bullets saem de
    `build_specs_card`, direto do `value_name` dos atributos. Sem atributos
    nao ha ficha a compor, entao os testes que exercitam o caminho feliz
    precisam fornece-los. Ver `test_specs_card_deterministic.py`.
    """
    return [
        SimpleNamespace(attribute_id="BRAND", attribute_name="Marca",
                        value_name="Wepink", value_id=None),
        SimpleNamespace(attribute_id="MODEL", attribute_name="Modelo",
                        value_name="Martin", value_id=None),
        SimpleNamespace(attribute_id="PERFUME_TYPE", attribute_name="Tipo de perfume",
                        value_name="Água de colônia", value_id="111075"),
    ]


def _make_db(cover_image, attributes=None):
    """AsyncMock de sessao cujas duas queries (capa determinística, depois
    atributos) devolvem `cover_image` e `attributes`, nessa ordem — mesma
    ordem em que `generate_specs_variant` as executa."""
    mock_db = AsyncMock()
    cover_result = MagicMock()
    cover_result.scalars.return_value.first.return_value = cover_image
    attrs_result = MagicMock()
    attrs_result.scalars.return_value.all.return_value = list(
        _specs_attributes() if attributes is None else attributes
    )
    mock_db.execute = AsyncMock(side_effect=[cover_result, attrs_result])
    mock_db.add = MagicMock()
    mock_db.commit = AsyncMock()
    return mock_db


class TestGenerateSpecsVariantSuccess:
    @pytest.mark.asyncio
    async def test_candidate_is_born_unapproved_with_specs_ai_kind_and_sort_order(self):
        from app.services.specs_variant_service import (
            SPECS_AI_KIND,
            SPECS_AI_SORT_ORDER,
            generate_specs_variant,
        )

        listing = _make_listing()
        cover = _make_cover(listing.id, image_bytes=b"exact-saved-cover-bytes")
        mock_db = _make_db(cover)

        prepared = b"prepared-1200x1200"
        verdict = ImageValidationResult(is_valid=True)

        with patch(
            "app.services.image_engines.openai_edit_engine.OpenAIEditEngine"
        ) as mock_engine_cls, patch(
            "app.workers.tasks.image_tasks._prepare_image_for_upload",
            return_value=(prepared, verdict),
        ), patch(
            "app.services.image_service.MLPictureService"
        ) as mock_ml_cls:
            mock_engine_cls.return_value.edit = AsyncMock(return_value=[b"variant-bytes"])
            mock_ml_cls.return_value.upload = AsyncMock(return_value="pic-specs-1")

            candidate = await generate_specs_variant(mock_db, listing, "token-xyz")

        assert candidate.kind == SPECS_AI_KIND == "specs_ai"
        assert candidate.approved is False
        assert candidate.sort_order == SPECS_AI_SORT_ORDER == 91
        assert candidate.status == "uploaded"
        assert candidate.ml_picture_id == "pic-specs-1"
        mock_db.commit.assert_awaited()

        engine_kwargs = mock_engine_cls.return_value.edit.await_args.kwargs
        assert engine_kwargs["images"] == [b"exact-saved-cover-bytes"]
        assert engine_kwargs["n"] == 1
        prompt = engine_kwargs["prompt"]
        # O texto que chega ao motor e o deterministico, montado dos atributos
        # — nao mais o que um LLM redigiu para este pedido. E SEM titulo: a
        # ficha sai so com os bullets, por decisao de produto.
        from app.services.image_card_copy_service import SPECS_CARD_TITLE

        assert "Marca: Wepink" in prompt
        assert "Tipo de perfume: Água de colônia" in prompt
        assert SPECS_CARD_TITLE not in prompt


class TestGenerateSpecsVariantMissingCoverBytes:
    @pytest.mark.asyncio
    async def test_cover_without_saved_bytes_raises_and_never_calls_engine(self):
        """Mesma propriedade cobrada em `test_cover_variant.py` para o
        servico irmao: uma chamada que nao pode ter sucesso (capa sem bytes
        salvos) nao pode chegar a instanciar um motor pago. O guard fica
        ANTES de `OpenAIEditEngine`, que nao deve ser tocado."""
        from app.services.specs_variant_service import (
            SpecsVariantError,
            generate_specs_variant,
        )

        listing = _make_listing()
        cover = _make_cover(listing.id, image_bytes=None)
        mock_db = _make_db(cover)

        with patch(
            "app.services.image_engines.openai_edit_engine.OpenAIEditEngine"
        ) as mock_engine_cls:
            with pytest.raises(SpecsVariantError):
                await generate_specs_variant(mock_db, listing, "token-xyz")

        mock_engine_cls.assert_not_called()
        mock_db.add.assert_not_called()
        mock_db.commit.assert_not_awaited()


class TestGenerateSpecsVariantPreservesPillowCard:
    @pytest.mark.asyncio
    async def test_existing_card_specs_row_stays_untouched(self):
        """O `card_specs` (Pillow) ja existente no anuncio nunca aparece em
        nenhum resultado configurado para `mock_db.execute` — a unica forma
        dele ser alterado seria o servico fazer uma consulta extra (nao
        configurada aqui) que o devolvesse, o que estouraria o `side_effect`
        de 2 resultados. A asserção confere o ESTADO real do objeto depois da
        chamada, não so que ele "ainda existe"."""
        from app.services.specs_variant_service import generate_specs_variant

        listing = _make_listing()
        cover = _make_cover(listing.id, image_bytes=b"cover-bytes")
        mock_db = _make_db(cover)

        pillow_card = ListingImage(
            id=uuid4(),
            listing_id=listing.id,
            kind="card_specs",
            approved=True,
            sort_order=52,
            status="approved",
        )

        prepared = b"prepared-bytes"
        verdict = ImageValidationResult(is_valid=True)

        with patch(
            "app.services.image_engines.openai_edit_engine.OpenAIEditEngine"
        ) as mock_engine_cls, patch(
            "app.workers.tasks.image_tasks._prepare_image_for_upload",
            return_value=(prepared, verdict),
        ), patch(
            "app.services.image_service.MLPictureService"
        ) as mock_ml_cls:
            mock_engine_cls.return_value.edit = AsyncMock(return_value=[b"variant-bytes"])
            mock_ml_cls.return_value.upload = AsyncMock(return_value="pic-specs-1")

            await generate_specs_variant(mock_db, listing, "token-xyz")

        assert pillow_card.approved is True
        assert pillow_card.sort_order == 52
        assert pillow_card.kind == "card_specs"
        assert mock_db.execute.await_count == 2


class TestGenerateSpecsVariantWithoutUsableAttributes:
    """O modo de falha mudou de lugar, nao desapareceu.

    Antes a ficha podia faltar porque o LLM nao devolveu o angulo `card_specs`
    — falha estocastica, que se "resolvia" tentando de novo. Agora ela so
    falta quando os atributos do anuncio nao rendem `MIN_BULLETS` linhas, o
    que e deterministico: tentar de novo daria exatamente o mesmo resultado.
    A propriedade cobrada continua a mesma: falhar ANTES do motor pago, sem
    escrever nada.
    """

    @pytest.mark.asyncio
    async def test_insufficient_attributes_raise_and_write_nothing(self):
        from app.services.specs_variant_service import (
            SpecsVariantError,
            generate_specs_variant,
        )

        listing = _make_listing()
        cover = _make_cover(listing.id, image_bytes=b"cover-bytes")
        mock_db = _make_db(cover, attributes=[])

        with patch(
            "app.services.image_engines.openai_edit_engine.OpenAIEditEngine"
        ) as mock_engine_cls:
            with pytest.raises(SpecsVariantError):
                await generate_specs_variant(mock_db, listing, "token-xyz")

        mock_engine_cls.assert_not_called()
        mock_db.add.assert_not_called()
        mock_db.commit.assert_not_awaited()


def _make_approval_listing(status="pending_image_approval"):
    listing = MagicMock()
    listing.id = uuid4()
    listing.seller_id = uuid4()
    listing.status = status
    listing.sku_external_id = None  # evita a atualizacao de ProductImage
    return listing


def _make_approve_db(images):
    """AsyncMock de sessao cuja unica query devolve a lista `images` — sem
    `ml_picture_id`/`sku_external_id` casados, o branch de ProductImage no
    fim de `approve_images` nao dispara uma 2a query."""
    db = AsyncMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = images
    db.execute = AsyncMock(return_value=result)
    db.commit = AsyncMock()
    # `add` e' sincrono; sem isto o filho generico de `AsyncMock()` tambem
    # vira AsyncMock e a chamada sincrona do servico (evento de revisao)
    # deixa uma coroutine sem await.
    db.add = MagicMock()
    return db


class TestApproveImagesReviewSeconds:
    @pytest.mark.asyncio
    async def test_review_seconds_recorded_on_approved_images_when_provided(self):
        """Chama `ListingService.approve_images` de verdade. O tempo de
        revisao nao mora mais na imagem — verifica o `ListingReviewEvent`
        passado a `db.add`, nao um atributo do objeto ORM."""
        from app.models.listing_review_event import ListingReviewEvent
        from app.services.listing_service import ListingService

        listing = _make_approval_listing()
        approved_img = ListingImage(
            id=uuid4(), listing_id=listing.id, kind="specs_ai",
            approved=False, sort_order=91, status="uploaded",
        )
        rejected_img = ListingImage(
            id=uuid4(), listing_id=listing.id, kind="card_specs",
            approved=True, sort_order=3, status="approved",
        )
        db = _make_approve_db([approved_img, rejected_img])
        svc = ListingService(db)

        await svc.approve_images(listing, [approved_img.id], review_seconds=42, user_id=uuid4())

        assert approved_img.approved is True
        assert rejected_img.approved is False
        assert listing.status == "generating_description"
        db.commit.assert_awaited()

        db.add.assert_called_once()
        evento = db.add.call_args.args[0]
        assert isinstance(evento, ListingReviewEvent)
        assert evento.mode == "individual"
        assert evento.review_seconds == 42
        assert evento.approved_count == 1

    @pytest.mark.asyncio
    async def test_review_seconds_absent_stays_null_and_approval_still_works(self):
        from app.models.listing_review_event import ListingReviewEvent
        from app.services.listing_service import ListingService

        listing = _make_approval_listing()
        approved_img = ListingImage(
            id=uuid4(), listing_id=listing.id, kind="specs_ai",
            approved=False, sort_order=91, status="uploaded",
        )
        db = _make_approve_db([approved_img])
        svc = ListingService(db)

        await svc.approve_images(listing, [approved_img.id], user_id=uuid4())

        assert approved_img.approved is True
        assert approved_img.status == "approved"
        assert listing.status == "generating_description"
        db.commit.assert_awaited()

        db.add.assert_called_once()
        evento = db.add.call_args.args[0]
        assert isinstance(evento, ListingReviewEvent)
        assert evento.review_seconds is None

    def test_listing_image_nao_tem_mais_o_atributo(self):
        """A coluna saiu do model (migration c8d2f6a4e1b7) — o tempo de
        revisao vive so no evento acima."""
        assert not hasattr(ListingImage, "review_seconds")


class _DuplicateCoversResult:
    """Igual ao homonimo de test_cover_variant.py: emula o `Result` REAL do
    SQLAlchemy quando a query casa com mais de uma linha —
    `scalar_one_or_none()` estoura `MultipleResultsFound`."""

    def __init__(self, rows):
        self._rows = list(rows)

    def scalar_one_or_none(self):
        from sqlalchemy.exc import MultipleResultsFound

        if len(self._rows) > 1:
            raise MultipleResultsFound(
                "Multiple rows were found when one or none was required"
            )
        return self._rows[0] if self._rows else None

    def scalars(self):
        holder = MagicMock()
        holder.first.return_value = self._rows[0] if self._rows else None
        holder.all.return_value = list(self._rows)
        return holder


class TestSpecsCoverLookupWithDuplicateCovers:
    @pytest.mark.asyncio
    async def test_two_deterministic_covers_do_not_raise_multiple_results_found(self):
        """Mesma falha da Frente A e pelo mesmo caminho: a 2a passada de
        `_try_i2i_generation` no mesmo anuncio deixa duas linhas
        `cover_deterministic`, e `scalar_one_or_none` viraria 500 opaco."""
        from app.services.specs_variant_service import generate_specs_variant

        listing = _make_listing()
        newest = _make_cover(listing.id, image_bytes=b"capa-nova")
        oldest = _make_cover(listing.id, image_bytes=b"capa-antiga")

        attrs_result = MagicMock()
        attrs_result.scalars.return_value.all.return_value = _specs_attributes()
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(
            side_effect=[_DuplicateCoversResult([newest, oldest]), attrs_result]
        )
        mock_db.add = MagicMock()
        mock_db.commit = AsyncMock()

        verdict = ImageValidationResult(is_valid=True)

        with patch(
            "app.services.image_engines.openai_edit_engine.OpenAIEditEngine"
        ) as mock_engine_cls, patch(
            "app.workers.tasks.image_tasks._prepare_image_for_upload",
            return_value=(b"prepared", verdict),
        ), patch(
            "app.services.image_service.MLPictureService"
        ) as mock_ml_cls:
            mock_engine_cls.return_value.edit = AsyncMock(return_value=[b"variant-bytes"])
            mock_ml_cls.return_value.upload = AsyncMock(return_value="pic-specs-1")

            candidate = await generate_specs_variant(mock_db, listing, "token-xyz")

        assert candidate.status == "uploaded"
        assert mock_engine_cls.return_value.edit.await_args.kwargs["images"] == [b"capa-nova"]

    @pytest.mark.asyncio
    async def test_cover_query_skips_byteless_rows_and_takes_the_newest(self):
        """A escolha da capa acontece no SQL (o mock nao filtra nada), entao e
        o SQL que se inspeciona — mesma checagem da Frente A."""
        from app.services.specs_variant_service import generate_specs_variant

        listing = _make_listing()
        cover = _make_cover(listing.id, image_bytes=b"cover-bytes")
        mock_db = _make_db(cover)

        verdict = ImageValidationResult(is_valid=True)

        with patch(
            "app.services.image_engines.openai_edit_engine.OpenAIEditEngine"
        ) as mock_engine_cls, patch(
            "app.workers.tasks.image_tasks._prepare_image_for_upload",
            return_value=(b"prepared", verdict),
        ), patch(
            "app.services.image_service.MLPictureService"
        ) as mock_ml_cls:
            mock_engine_cls.return_value.edit = AsyncMock(return_value=[b"variant-bytes"])
            mock_ml_cls.return_value.upload = AsyncMock(return_value="pic-specs-1")

            await generate_specs_variant(mock_db, listing, "token-xyz")

        sql = str(mock_db.execute.await_args_list[0].args[0])
        assert "listing_images.asset_key IS NOT NULL" in sql, sql
        assert "ORDER BY listing_images.created_at DESC" in sql, sql
        assert "LIMIT" in sql, sql
