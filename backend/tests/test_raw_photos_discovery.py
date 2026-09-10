"""Descoberta de fotos brutas: 2 e o minimo, nao o teto.

Antes o codigo so buscava `{sku}-1.jpg` e `{sku}-2.jpg`, entao seller com
3-10 fotos por SKU tinha as extras ignoradas. Agora a quantidade e descoberta
por sondagem, mantendo as 2 primeiras como obrigatorias.
"""
import httpx
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.seller_image_source_service import (
    RAW_PHOTOS_MAX,
    RAW_PHOTOS_MIN,
    fetch_raw_photos,
)

BASE = "https://bucket.exemplo/sku"


def _cliente(por_url):
    """Cliente httpx falso: `por_url` mapeia URL -> resposta ou excecao."""
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


def _urls(sku, quantas):
    return {f"{BASE}/{sku}-{n}.jpg": f"foto{n}".encode() for n in range(1, quantas + 1)}


class TestDescoberta:
    @pytest.mark.asyncio
    async def test_exatamente_2_fotos_comportamento_identico_ao_antigo(self):
        """Seller minimo: nada pode mudar para ele."""
        ctx, cli = _cliente(_urls("A", 2))
        with patch("httpx.AsyncClient", return_value=ctx):
            fotos = await fetch_raw_photos(BASE, "A")

        assert fotos == [b"foto1", b"foto2"]
        # sondou a 3a e parou; nao pode varrer ate o teto a toa
        assert cli.get.await_count == 3

    @pytest.mark.asyncio
    async def test_cinco_fotos_todas_descobertas(self):
        ctx, cli = _cliente(_urls("B", 5))
        with patch("httpx.AsyncClient", return_value=ctx):
            fotos = await fetch_raw_photos(BASE, "B")

        assert len(fotos) == 5
        assert fotos == [b"foto1", b"foto2", b"foto3", b"foto4", b"foto5"]

    @pytest.mark.asyncio
    async def test_falta_a_foto_1_devolve_none_mesmo_tendo_2_e_3(self):
        """As 2 primeiras sao obrigatorias — ter as outras nao compensa."""
        mapa = {
            f"{BASE}/C-2.jpg": b"foto2",
            f"{BASE}/C-3.jpg": b"foto3",
        }
        ctx, _ = _cliente(mapa)
        with patch("httpx.AsyncClient", return_value=ctx):
            assert await fetch_raw_photos(BASE, "C") is None

    @pytest.mark.asyncio
    async def test_falta_a_foto_2_devolve_none(self):
        ctx, _ = _cliente({f"{BASE}/D-1.jpg": b"foto1", f"{BASE}/D-3.jpg": b"foto3"})
        with patch("httpx.AsyncClient", return_value=ctx):
            assert await fetch_raw_photos(BASE, "D") is None

    @pytest.mark.asyncio
    async def test_para_no_teto_e_nao_sonda_indefinidamente(self):
        """Bucket com mais fotos que o teto: para em RAW_PHOTOS_MAX."""
        ctx, cli = _cliente(_urls("E", RAW_PHOTOS_MAX + 5))
        with patch("httpx.AsyncClient", return_value=ctx):
            fotos = await fetch_raw_photos(BASE, "E")

        assert len(fotos) == RAW_PHOTOS_MAX
        assert cli.get.await_count == RAW_PHOTOS_MAX

    @pytest.mark.asyncio
    async def test_erro_de_rede_numa_obrigatoria_devolve_none(self):
        ctx, _ = _cliente({f"{BASE}/F-1.jpg": httpx.ConnectError("sem rede")})
        with patch("httpx.AsyncClient", return_value=ctx):
            assert await fetch_raw_photos(BASE, "F") is None

    @pytest.mark.asyncio
    async def test_erro_de_rede_numa_extra_nao_derruba_o_sku(self):
        """Falha transitoria na 3a nao pode invalidar um SKU que tem o minimo."""
        mapa = _urls("G", 2)
        mapa[f"{BASE}/G-3.jpg"] = httpx.ConnectError("timeout")
        ctx, _ = _cliente(mapa)
        with patch("httpx.AsyncClient", return_value=ctx):
            fotos = await fetch_raw_photos(BASE, "G")

        assert fotos == [b"foto1", b"foto2"], "usa o que veio, nao devolve None"

    def test_o_minimo_continua_2(self):
        """Trava contra alguem 'melhorar' o minimo sem perceber o impacto."""
        assert RAW_PHOTOS_MIN == 2


class TestFonteDaPosicao4:
    """Posicao 4 ("Detalhes") do esquema de 5 posicoes.

    Ver docs/superpowers/specs/esquema-5-posicoes.md. A funcao so ESCOLHE a
    fonte — nao trata, nao recorta, nao chama IA.
    """

    def test_com_fotos_extras_usa_a_terceira(self):
        from app.services.seller_image_source_service import pick_detail_source

        foto, veio_de_extra = pick_detail_source([b"f1", b"f2", b"f3", b"f4"])

        assert foto == b"f3", "a regra e simples e documentada: a 3a foto"
        assert veio_de_extra is True

    def test_com_exatamente_3_fotos_ja_usa_a_terceira(self):
        from app.services.seller_image_source_service import pick_detail_source

        assert pick_detail_source([b"f1", b"f2", b"f3"]) == (b"f3", True)

    def test_so_o_minimo_cai_no_fallback_e_avisa(self):
        """Sem extra, o chamador PRECISA saber que e reaproveitamento."""
        from app.services.seller_image_source_service import pick_detail_source

        foto, veio_de_extra = pick_detail_source([b"f1", b"f2"])

        assert veio_de_extra is False, (
            "a flag e o que permite ao chamador nao forcar um 'detalhe' que a "
            "foto nao mostra"
        )
        assert foto == b"f2", "a ultima, para nao repetir a fonte da posicao 2"

    def test_nao_usa_foto_que_alimenta_a_posicao_2_quando_ha_extra(self):
        from app.services.seller_image_source_service import pick_detail_source

        foto, _ = pick_detail_source([b"f1", b"f2", b"f3"])

        assert foto not in (b"f1", b"f2"), (
            "havendo extra, a posicao 4 nao pode repetir a fonte da posicao 2"
        )

    def test_lista_vazia_e_erro_de_programacao(self):
        from app.services.seller_image_source_service import pick_detail_source

        with pytest.raises(ValueError):
            pick_detail_source([])
