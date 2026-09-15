import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.publish_service import PublishService


def _listing():
    listing = MagicMock()
    listing.selected_title = "Produto teste"
    listing.ml_category_id = "MLB1055"
    listing.price = 1299.90
    listing.stock_quantity = 1
    listing.condition = "new"
    listing.listing_type_id = "gold_special"
    return listing


def _image():
    img = MagicMock()
    img.approved = True
    img.ml_picture_id = "123456-PIC"
    img.sort_order = 0
    return img


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


def _image_at(i):
    img = MagicMock()
    img.approved = True
    img.ml_picture_id = f"pic-{i}"
    img.sort_order = i
    return img


class TestPublishPicsPayloadCap:
    """Teto defensivo do payload de fotos.

    A lista de fotos aprovadas nunca teve limite: `publish_tasks` seleciona
    tudo que esta `approved` e `publish()` monta uma entrada por linha. Uma
    aprovacao em massa mal filtrada, uma segunda passada do pipeline ou os
    candidatos das Frentes A/B fariam o total passar do limite do ML, que
    recusa o item INTEIRO por validacao — depois de todo o custo de IA.
    """

    @staticmethod
    def _create_response():
        resp = MagicMock()
        resp.status_code = 201
        resp.json.return_value = {"id": "MLB999", "status": "paused", "sub_status": []}
        return resp

    async def _publish_with(self, images, category_limit):
        mock_post = AsyncMock(return_value=self._create_response())
        with patch("httpx.AsyncClient") as mock_client_cls, \
             patch(
                 "app.services.category_service.get_category_max_pictures",
                 new_callable=AsyncMock,
                 return_value=category_limit,
             ):
            mock_client_cls.return_value.__aenter__.return_value.post = mock_post
            await PublishService(db=MagicMock()).publish(
                listing=_listing(),
                attributes=[],
                images=images,
                description_html=None,
                access_token="token",
            )
        return mock_post.await_args.kwargs["json"]

    @pytest.mark.asyncio
    async def test_falls_back_to_the_safe_constant_when_ml_does_not_answer(self):
        from app.services.publish_service import ML_MAX_PICTURES_FALLBACK

        body = await self._publish_with([_image_at(i) for i in range(20)], category_limit=None)

        assert len(body["pictures"]) == ML_MAX_PICTURES_FALLBACK == 12
        # O corte vem DEPOIS da ordenacao: a capa (sort_order=0) sobrevive e o
        # que fica de fora e sempre o material de maior sort_order (cards,
        # candidatos em 90/91).
        assert body["pictures"][0] == {"id": "pic-0"}
        assert {"id": "pic-19"} not in body["pictures"]

    @pytest.mark.asyncio
    async def test_uses_the_category_limit_when_ml_answers(self):
        body = await self._publish_with([_image_at(i) for i in range(20)], category_limit=6)

        assert len(body["pictures"]) == 6
        assert body["pictures"][-1] == {"id": "pic-5"}

    @pytest.mark.asyncio
    async def test_payload_below_the_limit_is_untouched(self):
        body = await self._publish_with([_image_at(i) for i in range(3)], category_limit=None)

        assert body["pictures"] == [{"id": "pic-0"}, {"id": "pic-1"}, {"id": "pic-2"}]
