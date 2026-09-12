"""`GET /listings/{id}/raw-photos`: URLs das fotos brutas do anuncio, para o
botao "ver original" da tela de revisao.

O bucket e' publico e sem listagem: a descoberta continua sendo a sondagem
de `{sku}-1`, `-2`... em cada extensao de `RAW_PHOTO_EXTENSIONS`, herdada de
`seller_image_source_service` (mesmo teto `RAW_PHOTOS_MAX`, mesma ordem de
extensoes). Aqui so muda o que se devolve: URL, nunca bytes. A sondagem de
URL nao le o corpo (GET em streaming, fechado logo apos o status).

Testes sem banco: `httpx.AsyncClient` e' substituido por um cliente falso
que registra cada sondagem, e o endpoint roda no app real (ASGI) com
`get_or_404` e `SellerImageConfigService.get` simulados.
"""
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException

from app.services.seller_image_source_service import (
    RAW_PHOTO_EXTENSIONS,
    RAW_PHOTOS_MAX,
    discover_raw_photo_urls,
)

BASE = "https://bucket.exemplo/fotos"


class _Bucket:
    """Bucket falso: `existentes` e' o conjunto de URLs que respondem 200.
    Registra cada URL sondada (por `stream` ou `get`) em `sondadas`."""

    def __init__(self, existentes):
        self.existentes = set(existentes)
        self.sondadas: list[str] = []

    def _resposta(self, url):
        r = MagicMock()
        r.status_code = 200 if url in self.existentes else 404
        r.content = b"bytes" if url in self.existentes else b""
        return r

    def cliente(self):
        cli = MagicMock()

        async def get(url):
            self.sondadas.append(url)
            return self._resposta(url)

        def stream(method, url):
            self.sondadas.append(url)
            ctx = MagicMock()
            ctx.__aenter__ = AsyncMock(return_value=self._resposta(url))
            ctx.__aexit__ = AsyncMock(return_value=False)
            return ctx

        cli.get = AsyncMock(side_effect=get)
        cli.stream = MagicMock(side_effect=stream)
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=cli)
        ctx.__aexit__ = AsyncMock(return_value=False)
        return ctx


def _url(sku, n, ext="jpg"):
    return f"{BASE}/{sku}-{n}.{ext}"


class TestDiscoverRawPhotoUrls:
    @pytest.mark.asyncio
    async def test_duas_fotos_devolve_duas_urls_na_ordem_com_a_extensao_certa(self):
        bucket = _Bucket({_url("37", 1), _url("37", 2)})
        with patch("httpx.AsyncClient", return_value=bucket.cliente()):
            urls = await discover_raw_photo_urls(BASE, "37")
        assert urls == [_url("37", 1), _url("37", 2)]

    @pytest.mark.asyncio
    async def test_extensoes_diferentes_no_mesmo_sku_resolvem_as_duas(self):
        bucket = _Bucket({_url("38", 1, "jpg"), _url("38", 2, "png")})
        with patch("httpx.AsyncClient", return_value=bucket.cliente()):
            urls = await discover_raw_photo_urls(BASE, "38")
        assert urls == [_url("38", 1, "jpg"), _url("38", 2, "png")]

    @pytest.mark.asyncio
    async def test_sku_sem_foto_devolve_lista_vazia(self):
        bucket = _Bucket(set())
        with patch("httpx.AsyncClient", return_value=bucket.cliente()):
            urls = await discover_raw_photo_urls(BASE, "NADA")
        assert urls == []
        # indice 1 ausente: uma sondagem por extensao e para
        assert len(bucket.sondadas) == len(RAW_PHOTO_EXTENSIONS)

    @pytest.mark.asyncio
    async def test_para_no_teto_e_nao_sonda_alem_dele(self):
        """Bucket com RAW_PHOTOS_MAX + 3 fotos: devolve exatamente RAW_PHOTOS_MAX
        URLs e nenhuma sondagem chega ao indice RAW_PHOTOS_MAX + 1."""
        bucket = _Bucket({_url("K", n) for n in range(1, RAW_PHOTOS_MAX + 4)})
        with patch("httpx.AsyncClient", return_value=bucket.cliente()):
            urls = await discover_raw_photo_urls(BASE, "K")
        assert len(urls) == RAW_PHOTOS_MAX
        assert urls[-1] == _url("K", RAW_PHOTOS_MAX)
        # cada indice existente custa 1 sondagem (jpg vence na primeira)
        assert len(bucket.sondadas) == RAW_PHOTOS_MAX
        assert not any(f"-{RAW_PHOTOS_MAX + 1}." in u for u in bucket.sondadas)

    @pytest.mark.asyncio
    async def test_nao_le_o_corpo_da_foto(self):
        """URL, nunca bytes: a sondagem usa GET em streaming e nao `get()`."""
        bucket = _Bucket({_url("37", 1), _url("37", 2)})
        ctx = bucket.cliente()
        with patch("httpx.AsyncClient", return_value=ctx):
            await discover_raw_photo_urls(BASE, "37")
        cli = await ctx.__aenter__()
        assert cli.get.await_count == 0


def _listing(seller_id, sku="37"):
    from app.models.listing import Listing

    agora = datetime.now(timezone.utc)
    return Listing(
        id=uuid4(), seller_id=seller_id, created_by=uuid4(), sku_external_id=sku,
        sku_brand="Marca", sku_description="Produto", selected_title=None,
        status="pending_image_approval", created_via="batch", mlb_id=None,
        created_at=agora, updated_at=agora, price=Decimal("10.00"), stock_quantity=1,
        condition="new", listing_type_id="gold_special", ml_category_id=None, error_message=None,
        approved_image_count=0,
    )


async def _chamar(listing_or_404, config, bucket):
    """Roda o endpoint no app real com get_or_404, a config do seller e o
    bucket simulados. `listing_or_404` e' um Listing ou uma HTTPException."""
    from httpx import ASGITransport, AsyncClient

    from app.core.dependencies import get_active_seller, get_db
    from app.main import app

    seller_id = uuid4()
    db = AsyncMock()

    async def _db():
        yield db

    if isinstance(listing_or_404, Exception):
        get_or_404 = AsyncMock(side_effect=listing_or_404)
        listing_id = uuid4()
    else:
        get_or_404 = AsyncMock(return_value=listing_or_404)
        listing_id = listing_or_404.id

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_active_seller] = lambda: SimpleNamespace(id=seller_id)
    try:
        with patch("app.api.v1.endpoints.listings.ListingService") as svc_cls, patch(
            "app.api.v1.endpoints.listings.SellerImageConfigService"
        ) as cfg_cls, patch("httpx.AsyncClient", return_value=bucket.cliente()):
            svc_cls.return_value.get_or_404 = get_or_404
            cfg_cls.return_value.get = AsyncMock(return_value=config)
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                return await client.get(f"/api/v1/listings/{listing_id}/raw-photos")
    finally:
        app.dependency_overrides.clear()


class TestEndpointRawPhotos:
    @pytest.mark.asyncio
    async def test_devolve_urls_agrupadas_por_sku(self):
        bucket = _Bucket({_url("37", 1), _url("37", 2, "png")})
        resp = await _chamar(_listing(uuid4(), "37"), SimpleNamespace(raw_base_url=BASE), bucket)
        assert resp.status_code == 200, resp.text
        assert resp.json() == {
            "configured": True,
            "groups": [{"sku": "37", "urls": [_url("37", 1), _url("37", 2, "png")]}],
        }

    @pytest.mark.asyncio
    async def test_sku_sem_foto_devolve_grupo_vazio_com_200(self):
        resp = await _chamar(_listing(uuid4(), "SEM"), SimpleNamespace(raw_base_url=BASE), _Bucket(set()))
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"configured": True, "groups": [{"sku": "SEM", "urls": []}]}

    @pytest.mark.asyncio
    async def test_seller_sem_raw_base_url_devolve_vazio_com_200_sem_sondar(self):
        bucket = _Bucket({_url("37", 1)})
        resp = await _chamar(_listing(uuid4(), "37"), None, bucket)
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"configured": False, "groups": []}
        assert bucket.sondadas == []

    @pytest.mark.asyncio
    async def test_anuncio_de_outro_seller_devolve_404(self):
        bucket = _Bucket({_url("37", 1)})
        resp = await _chamar(
            HTTPException(status_code=404, detail="Anúncio não encontrado"),
            SimpleNamespace(raw_base_url=BASE), bucket,
        )
        assert resp.status_code == 404
        assert bucket.sondadas == []


class TestCaminhoAntigoIntacto:
    @pytest.mark.asyncio
    async def test_fetch_raw_photos_continua_devolvendo_bytes_e_exigindo_o_minimo(self):
        """A extracao da sondagem nao pode mudar o caminho de geracao."""
        from app.services.seller_image_source_service import fetch_raw_photos

        bucket = _Bucket({_url("A", 1), _url("A", 2), _url("A", 3)})
        with patch("httpx.AsyncClient", return_value=bucket.cliente()):
            fotos = await fetch_raw_photos(BASE, "A")
        assert fotos == [b"bytes", b"bytes", b"bytes"]

        so_uma = _Bucket({_url("B", 1)})
        with patch("httpx.AsyncClient", return_value=so_uma.cliente()):
            assert await fetch_raw_photos(BASE, "B") is None
