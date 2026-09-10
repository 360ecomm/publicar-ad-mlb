"""Imagens geradas por IA viram ativo do seller num bucket R2 dedicado; o
banco guarda so a REFERENCIA (`ListingImage.asset_key`), nao mais o blob.

Momento do write-back: na GERACAO, nao na aprovacao — o mesmo momento em
que o candidato ja sobe ao CDN do ML (todo candidato que passa no QA sobe,
`approved=False`). Reprovado no QA tambem vai ao R2 (bytes crus do que a IA
produziu), para alguem julgar.

Consumidores internos dos bytes (variante de capa e ficha por IA) passam a
ler do R2 via `load_candidate_bytes`. Sem credencial configurada, a geracao
NAO falha: a linha nasce com `asset_key=None`, com aviso no log — perder a
posicao por causa do arquivo seria pior que perder o arquivo.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _settings(**over):
    s = MagicMock()
    s.r2_asset_bucket_name = over.get("name", "r2-mktp-img-ia")
    s.r2_asset_bucket_endpoint = over.get("endpoint", "https://acct.r2.cloudflarestorage.com")
    s.r2_asset_bucket_access_key_id = over.get("key", "AK")
    s.r2_asset_bucket_secret_access_key = over.get("secret", "SK")
    return s


class TestChaveEConfig:
    def test_chave_organiza_por_seller_sku_listing_kind(self):
        from app.services.r2_asset_service import asset_key_for

        k = asset_key_for(seller_id="sid", sku="45", listing_id="lid", kind="cover_ai", token="abc123", ext="jpg")
        assert k == "sid/45/lid/cover_ai-abc123.jpg"

    def test_configurado_so_com_as_4_variaveis(self):
        from app.services.r2_asset_service import R2AssetStore

        assert R2AssetStore(_settings()).configured is True
        assert R2AssetStore(_settings(secret="")).configured is False
        assert R2AssetStore(_settings(name="")).configured is False


class TestPutEGet:
    @pytest.mark.asyncio
    async def test_put_usa_o_endpoint_e_o_bucket_configurados(self):
        from app.services.r2_asset_service import R2AssetStore

        store = R2AssetStore(_settings())
        with patch("app.services.r2_asset_service.boto3") as boto:
            await store.put("sid/45/lid/cover_ai-x.jpg", b"bytes", "image/jpeg")

        boto.client.assert_called_once()
        kw = boto.client.call_args.kwargs
        assert kw["endpoint_url"] == "https://acct.r2.cloudflarestorage.com"
        assert kw["aws_access_key_id"] == "AK" and kw["aws_secret_access_key"] == "SK"
        boto.client.return_value.put_object.assert_called_once_with(
            Bucket="r2-mktp-img-ia", Key="sid/45/lid/cover_ai-x.jpg", Body=b"bytes", ContentType="image/jpeg"
        )

    @pytest.mark.asyncio
    async def test_get_devolve_os_bytes(self):
        from app.services.r2_asset_service import R2AssetStore

        store = R2AssetStore(_settings())
        with patch("app.services.r2_asset_service.boto3") as boto:
            boto.client.return_value.get_object.return_value = {"Body": MagicMock(read=MagicMock(return_value=b"lido"))}
            assert await store.get("k") == b"lido"
        boto.client.return_value.get_object.assert_called_once_with(Bucket="r2-mktp-img-ia", Key="k")


class TestStoreCandidateBytes:
    @pytest.mark.asyncio
    async def test_grava_e_devolve_a_chave(self):
        from app.services.r2_asset_service import store_candidate_bytes

        store = MagicMock(); store.configured = True; store.put = AsyncMock()
        with patch("app.services.r2_asset_service.get_asset_store", return_value=store):
            key = await store_candidate_bytes(b"jpeg", seller_id="sid", sku="45", listing_id="lid", kind="specs_ai")

        assert key.startswith("sid/45/lid/specs_ai-") and key.endswith(".jpg")
        store.put.assert_awaited_once_with(key, b"jpeg", "image/jpeg")

    @pytest.mark.asyncio
    async def test_sem_credencial_devolve_none_sem_levantar(self, caplog):
        from app.services.r2_asset_service import store_candidate_bytes

        store = MagicMock(); store.configured = False; store.put = AsyncMock()
        with patch("app.services.r2_asset_service.get_asset_store", return_value=store):
            key = await store_candidate_bytes(b"jpeg", seller_id="sid", sku="45", listing_id="lid", kind="cover_ai")

        assert key is None
        store.put.assert_not_awaited()
        assert "r2_asset" in caplog.text

    @pytest.mark.asyncio
    async def test_falha_do_r2_devolve_none_sem_levantar(self):
        from app.services.r2_asset_service import store_candidate_bytes

        store = MagicMock(); store.configured = True; store.put = AsyncMock(side_effect=RuntimeError("boom"))
        with patch("app.services.r2_asset_service.get_asset_store", return_value=store):
            assert await store_candidate_bytes(b"x", seller_id="s", sku="1", listing_id="l", kind="cover_ai") is None


class TestLoadCandidateBytes:
    @pytest.mark.asyncio
    async def test_le_pela_chave(self):
        from app.services.r2_asset_service import load_candidate_bytes

        img = MagicMock(); img.asset_key = "sid/45/lid/cover_deterministic-x.jpg"
        store = MagicMock(); store.configured = True; store.get = AsyncMock(return_value=b"capa")
        with patch("app.services.r2_asset_service.get_asset_store", return_value=store):
            assert await load_candidate_bytes(img) == b"capa"

    @pytest.mark.asyncio
    async def test_sem_chave_ou_sem_credencial_devolve_none(self):
        from app.services.r2_asset_service import load_candidate_bytes

        sem_chave = MagicMock(); sem_chave.asset_key = None
        store = MagicMock(); store.configured = True; store.get = AsyncMock()
        with patch("app.services.r2_asset_service.get_asset_store", return_value=store):
            assert await load_candidate_bytes(sem_chave) is None
        store.get.assert_not_awaited()

        com_chave = MagicMock(); com_chave.asset_key = "k"
        store.configured = False
        with patch("app.services.r2_asset_service.get_asset_store", return_value=store):
            assert await load_candidate_bytes(com_chave) is None


class TestModelSemBlob:
    def test_listing_image_tem_asset_key_e_nao_image_bytes(self):
        from app.models.listing_image import ListingImage

        cols = set(ListingImage.__table__.columns.keys())
        assert "asset_key" in cols
        assert "image_bytes" not in cols


class TestSalvarPosicaoEscreveNoR2:
    @pytest.mark.asyncio
    async def test_aprovada_no_qa_grava_bytes_preparados(self):
        from app.workers.tasks.image_tasks import _salvar_posicao

        listing = MagicMock(); listing.id = "lid"; listing.seller_id = "sid"
        db = AsyncMock(); db.add = MagicMock()
        with patch("app.workers.tasks.image_tasks._prepare_image_for_upload", return_value=(b"preparado", MagicMock(is_valid=True))), \
             patch("app.services.image_service.MLPictureService") as ml, \
             patch("app.services.r2_asset_service.store_candidate_bytes", new_callable=AsyncMock, return_value="sid/45/lid/cover_ai-x.jpg") as store:
            ml.return_value.upload = AsyncMock(return_value="pic-1")
            ok = await _salvar_posicao(db, listing, "45", "cover_ai", 0, b"gerado", "tok", requires_white_bg=True)

        assert ok is True
        store.assert_awaited_once_with(b"preparado", seller_id="sid", sku="45", listing_id="lid", kind="cover_ai")
        linha = db.add.call_args.args[0]
        assert linha.asset_key == "sid/45/lid/cover_ai-x.jpg"
        assert not hasattr(linha, "image_bytes") or linha.__table__.columns.get("image_bytes") is None

    @pytest.mark.asyncio
    async def test_reprovada_no_qa_grava_bytes_crus(self):
        from app.workers.tasks.image_tasks import _salvar_posicao

        listing = MagicMock(); listing.id = "lid"; listing.seller_id = "sid"
        db = AsyncMock(); db.add = MagicMock()
        with patch("app.workers.tasks.image_tasks._prepare_image_for_upload", return_value=(None, MagicMock(is_valid=False, reason="fundo"))), \
             patch("app.services.r2_asset_service.store_candidate_bytes", new_callable=AsyncMock, return_value="k-reprovada") as store:
            ok = await _salvar_posicao(db, listing, "45", "detail_ai", 3, b"cru", "tok", requires_white_bg=False)

        assert ok is False
        store.assert_awaited_once_with(b"cru", seller_id="sid", sku="45", listing_id="lid", kind="detail_ai")
        assert db.add.call_args.args[0].asset_key == "k-reprovada"
