"""Fotos brutas em .jpg, .png ou .webp — por posicao, na ordem, a primeira
que existir vence. Mistura de formato no mesmo SKU e' permitida.

Contrato do motor (OpenAI /v1/images/edits, GPT image models): aceita png,
webp e jpg como entrada. O `OpenAIEditEngine` mandava TODO arquivo como
`input_i.jpg` / `image/jpeg`; agora detecta o formato pelos bytes (magic
number) e envia nome e MIME corretos. Formato fora desses tres (gif, bmp...)
e' convertido para JPEG com Pillow antes do upload — o bucket do seller
continua aceitando o que aceitava; a conversao e' so na hora da chamada.
"""
import io
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.seller_image_source_service import (
    RAW_PHOTO_EXTENSIONS,
    RAW_PHOTOS_MIN,
    fetch_raw_photos,
)

BASE = "https://bucket.exemplo/sku"


def _cliente(por_url):
    cli = MagicMock()

    async def get(url):
        item = por_url.get(url)
        if item is None:
            r = MagicMock(); r.status_code = 404; r.content = b""
            return r
        if isinstance(item, Exception):
            raise item
        r = MagicMock(); r.status_code = 200; r.content = item
        return r

    cli.get = AsyncMock(side_effect=get)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=cli)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx, cli


class TestSondagemMultiExtensao:
    def test_ordem_das_extensoes(self):
        assert RAW_PHOTO_EXTENSIONS == ("jpg", "png", "webp")

    @pytest.mark.asyncio
    async def test_mistura_de_formatos_descobre_todas_na_ordem(self):
        ctx, cli = _cliente({
            f"{BASE}/M-1.jpg": b"um-jpg",
            f"{BASE}/M-2.png": b"dois-png",
            f"{BASE}/M-3.webp": b"tres-webp",
        })
        with patch("httpx.AsyncClient", return_value=ctx):
            fotos = await fetch_raw_photos(BASE, "M")
        assert fotos == [b"um-jpg", b"dois-png", b"tres-webp"]

    @pytest.mark.asyncio
    async def test_so_jpg_continua_identico(self):
        ctx, cli = _cliente({f"{BASE}/J-1.jpg": b"1", f"{BASE}/J-2.jpg": b"2", f"{BASE}/J-3.jpg": b"3"})
        with patch("httpx.AsyncClient", return_value=ctx):
            fotos = await fetch_raw_photos(BASE, "J")
        assert fotos == [b"1", b"2", b"3"]
        urls = [c.args[0] for c in cli.get.await_args_list]
        # os 3 indices existentes custam 1 request cada (jpg vence de primeira);
        # o 4o, ausente, custa 3 (jpg, png, webp) antes de encerrar
        assert urls[:3] == [f"{BASE}/J-1.jpg", f"{BASE}/J-2.jpg", f"{BASE}/J-3.jpg"]
        assert urls[3:] == [f"{BASE}/J-4.jpg", f"{BASE}/J-4.png", f"{BASE}/J-4.webp"]

    @pytest.mark.asyncio
    async def test_jpg_vence_quando_ha_dois_formatos_no_mesmo_indice(self):
        ctx, cli = _cliente({f"{BASE}/D-1.jpg": b"jpg", f"{BASE}/D-1.png": b"png", f"{BASE}/D-2.webp": b"w"})
        with patch("httpx.AsyncClient", return_value=ctx):
            fotos = await fetch_raw_photos(BASE, "D")
        assert fotos[0] == b"jpg"
        assert f"{BASE}/D-1.png" not in [c.args[0] for c in cli.get.await_args_list], "nao sonda alem da primeira que existe"

    @pytest.mark.asyncio
    async def test_obrigatoria_ausente_em_todas_as_extensoes_devolve_none(self):
        ctx, cli = _cliente({f"{BASE}/N-1.png": b"1"})   # falta a 2 em qualquer formato
        with patch("httpx.AsyncClient", return_value=ctx):
            assert await fetch_raw_photos(BASE, "N") is None
        assert RAW_PHOTOS_MIN == 2

    @pytest.mark.asyncio
    async def test_erro_de_rede_num_formato_tenta_o_proximo(self):
        import httpx
        ctx, cli = _cliente({f"{BASE}/R-1.jpg": httpx.ConnectError("boom"), f"{BASE}/R-1.png": b"png", f"{BASE}/R-2.jpg": b"2"})
        with patch("httpx.AsyncClient", return_value=ctx):
            fotos = await fetch_raw_photos(BASE, "R")
        assert fotos == [b"png", b"2"]


def _png_bytes():
    from PIL import Image
    buf = io.BytesIO(); Image.new("RGB", (8, 8), "red").save(buf, format="PNG"); return buf.getvalue()


def _webp_bytes():
    from PIL import Image
    buf = io.BytesIO(); Image.new("RGB", (8, 8), "blue").save(buf, format="WEBP"); return buf.getvalue()


def _jpeg_bytes():
    from PIL import Image
    buf = io.BytesIO(); Image.new("RGB", (8, 8), "green").save(buf, format="JPEG"); return buf.getvalue()


def _gif_bytes():
    from PIL import Image
    buf = io.BytesIO(); Image.new("P", (8, 8)).save(buf, format="GIF"); return buf.getvalue()


class TestMotorEnviaFormatoReal:
    @pytest.mark.asyncio
    async def test_png_webp_jpeg_vao_com_nome_e_mime_certos(self):
        import base64
        from app.services.image_engines.openai_edit_engine import OpenAIEditEngine

        resp = MagicMock(); resp.is_success = True
        resp.json.return_value = {"data": [{"b64_json": base64.b64encode(b"out").decode()}]}
        mock_post = AsyncMock(return_value=resp)
        with patch("httpx.AsyncClient") as cls:
            cls.return_value.__aenter__.return_value.post = mock_post
            await OpenAIEditEngine().edit(images=[_jpeg_bytes(), _png_bytes(), _webp_bytes()], prompt="p", n=1)

        files = mock_post.await_args.kwargs["files"]
        assert [(f[1][0], f[1][2]) for f in files] == [
            ("input_0.jpg", "image/jpeg"), ("input_1.png", "image/png"), ("input_2.webp", "image/webp"),
        ]
        assert files[1][1][1] == _png_bytes(), "png vai como esta, sem conversao"

    @pytest.mark.asyncio
    async def test_formato_fora_do_contrato_e_convertido_para_jpeg(self):
        import base64
        from app.services.image_engines.openai_edit_engine import OpenAIEditEngine

        resp = MagicMock(); resp.is_success = True
        resp.json.return_value = {"data": [{"b64_json": base64.b64encode(b"out").decode()}]}
        mock_post = AsyncMock(return_value=resp)
        with patch("httpx.AsyncClient") as cls:
            cls.return_value.__aenter__.return_value.post = mock_post
            await OpenAIEditEngine().edit(images=[_gif_bytes()], prompt="p", n=1)

        nome, conteudo, mime = mock_post.await_args.kwargs["files"][0][1]
        assert (nome, mime) == ("input_0.jpg", "image/jpeg")
        assert conteudo[:3] == b"\xff\xd8\xff", "bytes JPEG de verdade, nao o gif renomeado"
