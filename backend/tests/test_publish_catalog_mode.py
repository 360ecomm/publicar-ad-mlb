"""Modo catalogo: `family_name` no lugar de `title`, decidido pela resposta do ML.

Algumas categorias do Mercado Livre recusam `title` e exigem `family_name`.
Os dois campos sao mutuamente exclusivos: mandar ambos devolve
"The fields [title] are invalid for requested call".

A deteccao NAO usa campo da categoria. Foi verificado contra a API real que
`settings.catalog_domain` existe em todas as categorias testadas (MLB6284
perfumes, MLB44379 desodorantes, MLB1055 celulares, MLB178938 perfume pet),
entao gatear nele mandaria todo anuncio para o modo catalogo. A decisao vem da
resposta do proprio ML.
"""
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

ERRO_FAMILY_NAME = json.dumps({
    "cause": [],
    "message": "body.required_fields",
    "error": "The body does not contains some or none of the following properties [family_name]",
    "status": 400,
})

ERRO_OUTRO = json.dumps({
    "cause": [{"code": "item.attributes.invalid", "message": "Attribute [BRAND] is required"}],
    "message": "Validation error",
    "status": 400,
})


def _resp(status_code, texto, json_data=None):
    r = MagicMock()
    r.status_code = status_code
    r.text = texto
    r.json = MagicMock(return_value=json_data if json_data is not None else json.loads(texto))
    return r


def _posts_de_item(mock_client):
    """So os POSTs de CRIACAO de item.

    O `publish` tambem posta a descricao em /items/{id}/description; contar
    tudo faria o teste acusar 2 chamadas onde houve 1 criacao.
    """
    from app.services.publish_service import ML_ITEMS_URL
    return [
        c.kwargs["json"]
        for c in mock_client.post.await_args_list
        if c.args and c.args[0] == ML_ITEMS_URL
    ]


class TestDeteccaoDeModoCatalogo:
    def test_helper_reconhece_o_erro_de_family_name(self):
        from app.services.publish_service import _exige_family_name

        assert _exige_family_name(ERRO_FAMILY_NAME) is True
        assert _exige_family_name(ERRO_OUTRO) is False
        assert _exige_family_name("") is False
        assert _exige_family_name(None) is False


def _sem_teto_de_categoria():
    """Neutraliza a consulta do teto de fotos da categoria.

    `publish()` pergunta ao ML o `max_pictures_per_item` da categoria antes de
    montar o payload. Os testes desta classe mockam `httpx.AsyncClient`
    inteiro, entao esse GET cai no MESMO mock: `resp.raise_for_status()` sobre
    um AsyncMock devolve uma corrotina que ninguem aguarda (RuntimeWarning) e
    a chamada ainda polui a contagem de requests do cliente. Devolver None faz
    o `publish()` cair no teto fixo — exatamente o que acontece em producao
    quando o ML nao responde. O teto tem cobertura propria em
    `TestPublishPicsPayloadCap` (test_publish_service.py); aqui ele nao e o
    assunto.
    """
    return patch(
        "app.services.category_service.get_category_max_pictures",
        new_callable=AsyncMock,
        return_value=None,
    )


class TestPublicacaoUsaFamilyNameQuandoExigido:
    @pytest.mark.asyncio
    async def test_publica_com_family_name_quando_o_ml_exige(self):
        from app.services.publish_service import PublishService

        listing = _listing()
        svc = PublishService(AsyncMock())

        respostas = [
            _resp(400, ERRO_FAMILY_NAME),
            _resp(201, "{}", {"id": "MLB999", "status": "paused"}),
        ]

        with patch("httpx.AsyncClient") as cli, \
             _sem_teto_de_categoria(), \
             patch.object(PublishService, "_aguardar_validacao", new_callable=AsyncMock, return_value="active"), \
             patch.object(PublishService, "_post_description", new_callable=AsyncMock, create=True):
            client = cli.return_value.__aenter__.return_value
            client.post = AsyncMock(side_effect=respostas)
            try:
                await svc.publish(listing, _attrs(), _imgs(), "<p>desc</p>", "token")
            except Exception:
                pass

            corpos = _posts_de_item(client)

        assert len(corpos) >= 2, "deveria repetir a chamada em modo catalogo"
        primeiro, segundo = corpos[0], corpos[1]

        assert "title" in primeiro, "a 1a tentativa usa title (comportamento normal)"
        assert "family_name" not in primeiro

        assert "title" not in segundo, (
            "title e family_name sao mutuamente exclusivos — mandar os dois faz "
            "o ML recusar com 'The fields [title] are invalid'"
        )
        assert segundo.get("family_name") == listing.selected_title, (
            "o family_name sai do titulo aprovado pelo seller, nao de valor inventado"
        )
        # O resto do payload nao pode mudar entre as tentativas.
        for chave in ("category_id", "price", "pictures", "attributes", "status"):
            assert segundo.get(chave) == primeiro.get(chave)

    @pytest.mark.asyncio
    async def test_categoria_normal_usa_title_e_faz_uma_chamada_so(self):
        """Comportamento atual preservado: sucesso de primeira, com title."""
        from app.services.publish_service import PublishService

        svc = PublishService(AsyncMock())
        ok = _resp(201, "{}", {"id": "MLB123", "status": "paused"})

        with patch("httpx.AsyncClient") as cli, \
             _sem_teto_de_categoria(), \
             patch.object(PublishService, "_aguardar_validacao", new_callable=AsyncMock, return_value="active"):
            client = cli.return_value.__aenter__.return_value
            client.post = AsyncMock(return_value=ok)
            await svc.publish(_listing(), _attrs(), _imgs(), "<p>desc</p>", "token")
            corpos = _posts_de_item(client)

        assert len(corpos) == 1, "sem erro, nao pode haver 2a tentativa de criar o item"
        assert "title" in corpos[0]
        assert "family_name" not in corpos[0]

    @pytest.mark.asyncio
    async def test_outro_erro_400_nao_dispara_o_fallback(self):
        """Erro que nao e de family_name continua virando MLValidationError direto."""
        from app.services.publish_service import MLValidationError, PublishService

        svc = PublishService(AsyncMock())
        with patch("httpx.AsyncClient") as cli, _sem_teto_de_categoria():
            client = cli.return_value.__aenter__.return_value
            client.post = AsyncMock(return_value=_resp(400, ERRO_OUTRO))
            with pytest.raises(MLValidationError):
                await svc.publish(_listing(), _attrs(), _imgs(), "<p>desc</p>", "token")
            assert len(_posts_de_item(client)) == 1, "nao pode repetir chamada paga a toa"


def _listing():
    l = MagicMock()
    l.id = "lid"
    l.selected_title = "Perfume Martin Desodorante Colônia 100ml Wepink"
    l.ml_category_id = "MLB6284"
    l.price = 276
    l.stock_quantity = 1
    l.condition = "new"
    l.listing_type_id = "gold_special"
    return l


def _attrs():
    a = MagicMock()
    a.attribute_id = "BRAND"
    a.value_id = None
    a.value_name = "Wepink"
    return [a]


def _imgs():
    i = MagicMock()
    i.ml_picture_id = "pic1"
    i.approved = True
    i.sort_order = 0
    return [i]
