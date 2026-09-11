"""`sort_order=0` e reservado a kind de capa — invariante estrutural.

Por que existe este arquivo: `promote_cover` so rebaixa linhas de kind de
capa, de proposito, para nunca despublicar uma foto `individual` que o
operador aprovou. Enquanto `approve_images` numerava com `enumerate` puro, a
primeira aprovada (quase sempre uma `individual`) ficava em `sort_order=0` —
e promover uma capa deixava DUAS linhas em 0. `publish_service` ordena por
`sort_order` sem desempate, entao qual delas virava a capa do anuncio era
arbitrario: a promocao podia ficar inerte.

A correcao e estrutural (reservar a posicao), nao um desempate tardio na
publicacao. Os testes abaixo miram a propriedade, nao a implementacao:
executam `approve_images` e `promote_cover` DE VERDADE sobre instancias reais
de `ListingImage` e olham o estado final dos objetos. So a sessao e mockada.

O teste que carrega o peso e
`TestInvariantHoldsThroughPromotion.test_no_tie_at_cover_position_after_promotion`:
ele encadeia os dois servicos e reproduz o filtro SQL de `promote_cover` sobre
o estado real, de modo que reverter a reserva em `approve_images` recoloca uma
`individual` em 0, o filtro por `kind` a deixa de fora do rebaixamento, e o
empate aparece na assercao. Verificado por mutacao — ver o relatorio da branch.
"""
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.models.listing_image import (
    COVER_SORT_ORDER,
    PROMOTABLE_COVER_KINDS,
    ListingImage,
)
from app.services.cover_variant_service import COVER_AI_SORT_ORDER, promote_cover
from app.services.listing_service import ListingService


def _listing(status="pending_image_approval"):
    listing = MagicMock()
    listing.id = uuid4()
    listing.seller_id = uuid4()
    listing.status = status
    listing.sku_external_id = None  # evita o branch de ProductImage (2a query)
    return listing


def _img(listing_id, kind, sort_order=99, approved=False):
    return ListingImage(
        id=uuid4(),
        listing_id=listing_id,
        kind=kind,
        approved=approved,
        sort_order=sort_order,
        status="uploaded",
    )


def _approve_db(images):
    db = AsyncMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = images
    db.execute = AsyncMock(return_value=result)
    db.commit = AsyncMock()
    # `add` e' sincrono (nunca `await self.db.add(...)`); sem isto, o filho
    # generico de `AsyncMock()` tambem vira AsyncMock e a chamada sincrona do
    # servico deixa uma coroutine sem await (RuntimeWarning ruidoso, sem
    # afetar o resultado do teste).
    db.add = MagicMock()
    return db


def _at_cover(images):
    return [i for i in images if i.sort_order == COVER_SORT_ORDER]


class TestApproveImagesReservesCoverPosition:
    @pytest.mark.asyncio
    async def test_primeira_aprovada_nao_capa_comeca_em_1_e_deixa_o_0_vago(self):
        listing = _listing()
        a = _img(listing.id, "individual")
        b = _img(listing.id, "card_benefits")
        db = _approve_db([a, b])

        await ListingService(db).approve_images(listing, [a.id, b.id], user_id=uuid4())

        assert a.sort_order == 1, "nenhuma foto que nao seja capa pode cair em 0"
        assert b.sort_order == 2, "a ordem escolhida pelo operador e preservada"
        assert _at_cover([a, b]) == [], "o 0 fica vago ate existir uma capa"

    @pytest.mark.asyncio
    async def test_capa_em_primeiro_ocupa_o_0(self):
        listing = _listing()
        capa = _img(listing.id, "cover_deterministic")
        foto = _img(listing.id, "individual")
        db = _approve_db([capa, foto])

        await ListingService(db).approve_images(listing, [capa.id, foto.id], user_id=uuid4())

        assert capa.sort_order == COVER_SORT_ORDER
        assert foto.sort_order == 1

    @pytest.mark.asyncio
    async def test_capa_no_meio_da_lista_nao_e_promovida_a_0(self):
        """A reserva nao reordena a escolha do operador — so evita o 0."""
        listing = _listing()
        foto = _img(listing.id, "individual")
        capa = _img(listing.id, "cover_ai")
        db = _approve_db([foto, capa])

        await ListingService(db).approve_images(listing, [foto.id, capa.id], user_id=uuid4())

        assert (foto.sort_order, capa.sort_order) == (1, 2)
        assert _at_cover([foto, capa]) == []

    @pytest.mark.asyncio
    async def test_id_repetido_nao_consome_duas_posicoes(self):
        listing = _listing()
        capa = _img(listing.id, "cover_deterministic")
        foto = _img(listing.id, "individual")
        db = _approve_db([capa, foto])

        await ListingService(db).approve_images(listing, [capa.id, capa.id, foto.id], user_id=uuid4())

        assert foto.sort_order == 1, "o id duplicado nao pode abrir um buraco"

    @pytest.mark.asyncio
    async def test_id_de_outro_anuncio_e_ignorado_sem_estourar(self):
        listing = _listing()
        capa = _img(listing.id, "cover_deterministic")
        db = _approve_db([capa])

        await ListingService(db).approve_images(listing, [uuid4(), capa.id], user_id=uuid4())

        assert capa.sort_order == COVER_SORT_ORDER
        assert capa.approved is True


class TestInvariantHoldsThroughPromotion:
    @pytest.mark.asyncio
    async def test_no_tie_at_cover_position_after_promotion(self):
        """O cenario exato do defeito, ponta a ponta.

        Operador aprova duas fotos comuns (nenhuma capa), depois promove a
        variante `cover_ai`. Ao final tem de existir EXATAMENTE uma linha em
        `sort_order=0`, e tem de ser a variante — senao a ordenacao da
        publicacao escolhe a capa por sorteio.
        """
        listing = _listing()
        foto1 = _img(listing.id, "individual")
        foto2 = _img(listing.id, "individual")
        variante = _img(listing.id, "cover_ai", sort_order=COVER_AI_SORT_ORDER)
        galeria = [foto1, foto2, variante]

        db = _approve_db(galeria)
        await ListingService(db).approve_images(listing, [foto1.id, foto2.id], user_id=uuid4())

        # Reproduz o filtro SQL de `promote_cover` (sort_order=0 E kind de capa
        # E != alvo) sobre o estado REAL deixado por `approve_images`. E isto
        # que faz o teste morder: se a reserva sumir, `foto1` volta para 0, o
        # filtro por `kind` a deixa de fora do rebaixamento, e ela empata.
        others = [
            i for i in galeria
            if i.sort_order == COVER_SORT_ORDER
            and i.kind in PROMOTABLE_COVER_KINDS
            and i.id is not variante.id
        ]
        target_result = MagicMock()
        target_result.scalar_one_or_none.return_value = variante
        others_result = MagicMock()
        others_result.scalars.return_value.all.return_value = others
        promote_db = AsyncMock()
        promote_db.execute = AsyncMock(side_effect=[target_result, others_result])
        promote_db.commit = AsyncMock()

        await promote_cover(promote_db, listing, variante.id)

        assert _at_cover(galeria) == [variante], (
            "exatamente uma linha em sort_order=0, e e a capa promovida — "
            "empate aqui torna a capa publicada arbitraria"
        )
        assert variante.approved is True
        assert foto1.approved is True and foto2.approved is True, (
            "promover capa nunca despublica foto aprovada"
        )
