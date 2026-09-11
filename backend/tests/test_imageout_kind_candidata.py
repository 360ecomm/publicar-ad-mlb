"""`ImageOut` expoe `kind`, `is_candidate` e `validation_error`.

A tela de revisao precisa saber qual imagem e' qual posicao, o que e'
candidata e por que uma posicao reprovou. A regra de candidata continua num
lugar so: `ListingImage.is_candidate` (sort_order >= CANDIDATE_SORT_ORDER_FLOOR).
"""
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest


def _img(sort_order: int, kind: str = "cover_ai", **kw):
    from app.models.listing_image import ListingImage

    base = dict(id=uuid4(), listing_id=uuid4(), ml_picture_id="p", status="uploaded",
                approved=False, sort_order=sort_order, kind=kind, validation_error=None)
    base.update(kw)
    return ListingImage(**base)


class TestIsCandidate:
    @pytest.mark.parametrize("sort_order", [0, 1, 2, 3, 4, 89])
    def test_galeria_nao_e_candidata(self, sort_order):
        assert _img(sort_order).is_candidate is False

    @pytest.mark.parametrize("sort_order", [90, 91])
    def test_area_de_candidatas(self, sort_order):
        assert _img(sort_order).is_candidate is True

    def test_regra_vem_da_constante(self):
        from app.models.listing_image import CANDIDATE_SORT_ORDER_FLOOR

        assert _img(CANDIDATE_SORT_ORDER_FLOOR - 1).is_candidate is False
        assert _img(CANDIDATE_SORT_ORDER_FLOOR).is_candidate is True


class TestImageOutCampos:
    def test_campos_novos_vem_da_linha(self):
        from app.schemas.listing import ImageOut

        out = ImageOut.model_validate(_img(90, kind="cover_ai"))
        assert out.kind == "cover_ai"
        assert out.is_candidate is True
        assert out.validation_error is None

    def test_linha_reprovada_traz_o_motivo(self):
        from app.schemas.listing import ImageOut

        linha = _img(0, kind="cover_ai", status="validation_failed", ml_picture_id=None,
                     validation_error="fundo nao branco (ratio 0.71)")
        out = ImageOut.model_validate(linha)
        assert out.kind == "cover_ai"
        assert out.is_candidate is False
        assert out.validation_error == "fundo nao branco (ratio 0.71)"
        assert out.status == "validation_failed" and out.ml_picture_id is None

    def test_campos_existentes_nao_mudam(self):
        from app.schemas.listing import ImageOut

        assert {"id", "ml_picture_id", "status", "approved", "sort_order"} <= set(ImageOut.model_fields)


class TestEndpointDetalhe:
    @staticmethod
    def _listing(seller_id):
        from app.models.listing import Listing

        agora = datetime.now(timezone.utc)
        return Listing(
            id=uuid4(), seller_id=seller_id, created_by=uuid4(), sku_external_id="45",
            sku_brand="Marca", sku_description="Produto", selected_title=None,
            status="pending_image_approval", created_via="batch", mlb_id=None,
            created_at=agora, updated_at=agora, price=Decimal("10.00"), stock_quantity=1,
            condition="new", listing_type_id="gold_special", ml_category_id=None, error_message=None,
        )

    @pytest.mark.asyncio
    async def test_get_listing_traz_kind_is_candidate_e_validation_error(self):
        from httpx import ASGITransport, AsyncClient

        from app.core.dependencies import get_active_seller, get_db
        from app.main import app

        seller_id = uuid4()
        listing = self._listing(seller_id)
        imagens = [
            _img(0, kind="cover_deterministic", listing_id=listing.id),
            _img(1, kind="presentation_ai", listing_id=listing.id),
            _img(0, kind="cover_ai", listing_id=listing.id, status="validation_failed",
                 ml_picture_id=None, validation_error="fundo nao branco"),
            _img(90, kind="cover_ai", listing_id=listing.id),
        ]

        async def execute_side(stmt):
            r = MagicMock()
            r.scalars.return_value.all.return_value = imagens if "FROM listing_images" in str(stmt) else []
            r.scalar_one_or_none.return_value = None
            return r

        db = AsyncMock(); db.execute = execute_side

        async def _db():
            yield db

        app.dependency_overrides[get_db] = _db
        app.dependency_overrides[get_active_seller] = lambda: MagicMock(id=seller_id)
        try:
            with patch("app.api.v1.endpoints.listings.ListingService") as svc_cls:
                svc_cls.return_value.get_or_404 = AsyncMock(return_value=listing)
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                    resp = await client.get(f"/api/v1/listings/{listing.id}")
        finally:
            app.dependency_overrides.clear()

        assert resp.status_code == 200, resp.text
        images = resp.json()["images"]
        assert len(images) == 4
        for img in images:
            assert {"kind", "is_candidate", "validation_error"} <= set(img), img
        por_kind_e_pos = {(i["kind"], i["sort_order"]): i for i in images}
        assert por_kind_e_pos[("presentation_ai", 1)]["is_candidate"] is False
        assert por_kind_e_pos[("cover_ai", 90)]["is_candidate"] is True
        reprovada = por_kind_e_pos[("cover_ai", 0)]
        assert reprovada["validation_error"] == "fundo nao branco" and reprovada["status"] == "validation_failed"
        assert por_kind_e_pos[("cover_deterministic", 0)]["validation_error"] is None
