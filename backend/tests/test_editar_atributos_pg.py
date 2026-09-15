"""PATCH /listings/{id}/attributes contra Postgres real.

Prova o que o teste de rota (sem banco) nao alcanca: que a correcao
PERSISTE (rele numa sessao NOVA), que grava exatamente 1 evento de
auditoria, que NAO mexe em status/imagem/descricao, que a deteccao de
posicao desatualizada (`stale_positions`) funciona ponta a ponta pelo
endpoint, e que 422 de obrigatorio vazio nao escreve nada.

Mesma infraestrutura de `test_recusa_aprovacao_vazia.py`: so roda com
`TEST_DATABASE_URL` apontando para o banco dedicado `publicar_test`, nunca
contra o banco do ambiente.
"""
import os
from types import SimpleNamespace
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from tests.test_eventos_de_revisao import _preparar_banco, _semear_user_e_seller

TEST_DB = os.environ.get("TEST_DATABASE_URL")
_precisa_db = pytest.mark.skipif(
    not TEST_DB, reason="TEST_DATABASE_URL nao definido: teste real so roda com Postgres dedicado"
)


async def _criar_listing(session_maker, seller_id, user_id, listing_status):
    """Cria 1 listing vazio (sem atributo, sem imagem, sem descricao) no
    `listing_status` dado. Devolve o id."""
    from app.models.listing import Listing

    async with session_maker() as s:
        listing = Listing(
            seller_id=seller_id,
            created_by=user_id,
            sku_description="d",
            sku_brand="b",
            price=10,
            stock_quantity=1,
            condition="new",
            listing_type_id="gold_special",
            status=listing_status,
            created_via="batch",
        )
        s.add(listing)
        await s.commit()
        return listing.id


async def _add_atributos(session_maker, listing_id):
    """BRAND (obrigatorio), MODEL e FLAVOR (livres), todos texto livre (sem
    `allowed_values`: nada a validar contra enumeracao). Devolve os ids."""
    from app.models.listing_attribute import ListingAttribute

    async with session_maker() as s:
        brand = ListingAttribute(
            listing_id=listing_id, attribute_id="BRAND", attribute_name="Marca",
            value_id=None, value_name="MarcaX", attribute_type="string",
            is_required=True, source="ai",
        )
        model = ListingAttribute(
            listing_id=listing_id, attribute_id="MODEL", attribute_name="Modelo",
            value_id=None, value_name="ModeloX", attribute_type="string",
            is_required=False, source="ai",
        )
        flavor = ListingAttribute(
            listing_id=listing_id, attribute_id="FLAVOR", attribute_name="Sabor",
            value_id=None, value_name="Chocolate", attribute_type="string",
            is_required=False, source="ai",
        )
        s.add_all([brand, model, flavor])
        await s.commit()
        return brand.id, model.id, flavor.id


async def _add_cinco_posicoes(session_maker, listing_id, approved=False):
    """As 5 posicoes oficiais do esquema (`sort_order` 0..4), no `kind`
    correto de cada uma."""
    from app.models.listing_image import POSITION_KINDS, ListingImage

    async with session_maker() as s:
        s.add_all([
            ListingImage(
                listing_id=listing_id,
                ml_picture_id=f"p{sort_order}",
                status="uploaded",
                approved=approved,
                sort_order=sort_order,
                kind=kind,
            )
            for sort_order, kind in POSITION_KINDS.items()
        ])
        await s.commit()


async def _add_descricao(session_maker, listing_id, html="<p>original</p>"):
    from app.models.listing_description import ListingDescription

    async with session_maker() as s:
        desc = ListingDescription(listing_id=listing_id, description_html=html)
        s.add(desc)
        await s.commit()
        return desc.id


async def _eventos(session_maker, listing_id):
    from app.models.listing_review_event import ListingReviewEvent

    async with session_maker() as s:
        rows = (
            await s.execute(
                select(ListingReviewEvent).where(ListingReviewEvent.listing_id == listing_id)
            )
        ).scalars().all()
    return rows


async def _atributo_por_id(session_maker, listing_id, attribute_id):
    from app.models.listing_attribute import ListingAttribute

    async with session_maker() as s:
        return (
            await s.execute(
                select(ListingAttribute).where(
                    ListingAttribute.listing_id == listing_id,
                    ListingAttribute.attribute_id == attribute_id,
                )
            )
        ).scalar_one()


async def _chamar_patch(session_maker, seller_id, user_id, listing_id, attributes):
    """Chama o endpoint de verdade via ASGI, com `get_db`/`get_active_seller`/
    `get_current_user` trocados pelos dados semeados neste teste — mesmo
    padrao de `test_atributos_tags_ml.py`."""
    from app.core.dependencies import get_active_seller, get_current_user, get_db
    from app.main import app

    async def _override_get_db():
        async with session_maker() as session:
            yield session

    async def _override_get_active_seller():
        return SimpleNamespace(id=seller_id)

    async def _override_get_current_user():
        return SimpleNamespace(id=user_id)

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_active_seller] = _override_get_active_seller
    app.dependency_overrides[get_current_user] = _override_get_current_user
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            return await client.patch(
                f"/api/v1/listings/{listing_id}/attributes",
                json={"attributes": attributes},
            )
    finally:
        app.dependency_overrides.clear()


@_precisa_db
class TestEdicaoRealNoBanco:
    @pytest.mark.asyncio
    async def test_persiste_o_valor_e_marca_source_seller(self):
        """Edita FLAVOR de Chocolate para Lichia; rele do banco numa sessao
        NOVA e confere value_name e source."""
        engine, sm = await _preparar_banco()
        try:
            user_id, seller_id = await _semear_user_e_seller(sm)
            listing_id = await _criar_listing(sm, seller_id, user_id, "ready_to_publish")
            await _add_atributos(sm, listing_id)

            resp = await _chamar_patch(
                sm, seller_id, user_id, listing_id,
                [{"attribute_id": "FLAVOR", "value_id": None, "value_name": "Lichia"}],
            )
            assert resp.status_code == 200, resp.text

            flavor = await _atributo_por_id(sm, listing_id, "FLAVOR")
            assert flavor.value_name == "Lichia"
            assert flavor.source == "seller"
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_grava_exatamente_um_evento_attributes_edited(self):
        """Conta linhas de `listing_review_events` antes e depois: +1, com
        action='attributes_edited', mode='individual', approved_count=1,
        review_seconds IS NULL e user_id do autor."""
        engine, sm = await _preparar_banco()
        try:
            from app.models.listing_review_event import (
                REVIEW_ACTION_ATTRIBUTES_EDITED,
                REVIEW_MODE_INDIVIDUAL,
            )

            user_id, seller_id = await _semear_user_e_seller(sm)
            listing_id = await _criar_listing(sm, seller_id, user_id, "ready_to_publish")
            await _add_atributos(sm, listing_id)

            antes = await _eventos(sm, listing_id)
            assert antes == []

            resp = await _chamar_patch(
                sm, seller_id, user_id, listing_id,
                [{"attribute_id": "FLAVOR", "value_id": None, "value_name": "Lichia"}],
            )
            assert resp.status_code == 200, resp.text

            depois = await _eventos(sm, listing_id)
            assert len(depois) == 1
            evento = depois[0]
            assert evento.action == REVIEW_ACTION_ATTRIBUTES_EDITED
            assert evento.mode == REVIEW_MODE_INDIVIDUAL
            assert evento.approved_count == 1
            assert evento.review_seconds is None
            assert evento.user_id == user_id
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_nao_muda_o_status_nem_apaga_imagem_aprovada_nem_descricao(self):
        """Listing em ready_to_publish com 5 imagens aprovadas e uma
        ListingDescription. Depois da edicao: status ainda ready_to_publish,
        5 imagens ainda approved=True, descricao intacta (mesmo id e mesmo
        HTML)."""
        from app.models.listing import Listing
        from app.models.listing_image import ListingImage

        engine, sm = await _preparar_banco()
        try:
            user_id, seller_id = await _semear_user_e_seller(sm)
            listing_id = await _criar_listing(sm, seller_id, user_id, "ready_to_publish")
            await _add_atributos(sm, listing_id)
            await _add_cinco_posicoes(sm, listing_id, approved=True)
            descricao_id = await _add_descricao(sm, listing_id)

            resp = await _chamar_patch(
                sm, seller_id, user_id, listing_id,
                [{"attribute_id": "FLAVOR", "value_id": None, "value_name": "Lichia"}],
            )
            assert resp.status_code == 200, resp.text

            async with sm() as s:
                listing = (
                    await s.execute(select(Listing).where(Listing.id == listing_id))
                ).scalar_one()
                assert listing.status == "ready_to_publish"

                imagens = (
                    await s.execute(
                        select(ListingImage).where(ListingImage.listing_id == listing_id)
                    )
                ).scalars().all()
                assert len(imagens) == 5
                assert all(img.approved is True for img in imagens)

                from app.models.listing_description import ListingDescription

                desc = (
                    await s.execute(
                        select(ListingDescription).where(
                            ListingDescription.listing_id == listing_id
                        )
                    )
                ).scalar_one()
                assert desc.id == descricao_id
                assert desc.description_html == "<p>original</p>"
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_ficha_alterada_marca_a_posicao_4(self):
        """Cria as 5 posicoes em listing_images; edita MODEL; a resposta traz
        4 em stale_positions."""
        engine, sm = await _preparar_banco()
        try:
            user_id, seller_id = await _semear_user_e_seller(sm)
            listing_id = await _criar_listing(sm, seller_id, user_id, "ready_to_publish")
            await _add_atributos(sm, listing_id)
            await _add_cinco_posicoes(sm, listing_id)

            resp = await _chamar_patch(
                sm, seller_id, user_id, listing_id,
                [{"attribute_id": "MODEL", "value_id": None, "value_name": "ModeloNovo"}],
            )
            assert resp.status_code == 200, resp.text
            assert 4 in resp.json()["stale_positions"]
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_anuncio_sem_imagem_nao_recebe_aviso(self):
        """Mesmo listing sem nenhuma linha em listing_images: stale_positions
        volta vazio mesmo editando MODEL."""
        engine, sm = await _preparar_banco()
        try:
            user_id, seller_id = await _semear_user_e_seller(sm)
            listing_id = await _criar_listing(sm, seller_id, user_id, "ready_to_publish")
            await _add_atributos(sm, listing_id)
            # Sem _add_cinco_posicoes: nenhuma linha em listing_images.

            resp = await _chamar_patch(
                sm, seller_id, user_id, listing_id,
                [{"attribute_id": "MODEL", "value_id": None, "value_name": "ModeloNovo"}],
            )
            assert resp.status_code == 200, resp.text
            assert resp.json()["stale_positions"] == []
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_422_de_obrigatorio_vazio_nao_escreve_nada(self):
        """Tenta esvaziar BRAND (is_required=True) junto com uma edicao valida
        de FLAVOR. Espera 422; rele do banco: BRAND e FLAVOR intactos e ZERO
        eventos gravados."""
        engine, sm = await _preparar_banco()
        try:
            user_id, seller_id = await _semear_user_e_seller(sm)
            listing_id = await _criar_listing(sm, seller_id, user_id, "ready_to_publish")
            await _add_atributos(sm, listing_id)

            resp = await _chamar_patch(
                sm, seller_id, user_id, listing_id,
                [
                    {"attribute_id": "BRAND", "value_id": None, "value_name": ""},
                    {"attribute_id": "FLAVOR", "value_id": None, "value_name": "Lichia"},
                ],
            )
            assert resp.status_code == 422, resp.text

            brand = await _atributo_por_id(sm, listing_id, "BRAND")
            flavor = await _atributo_por_id(sm, listing_id, "FLAVOR")
            assert brand.value_name == "MarcaX"
            assert flavor.value_name == "Chocolate"

            eventos = await _eventos(sm, listing_id)
            assert eventos == []
        finally:
            await engine.dispose()
