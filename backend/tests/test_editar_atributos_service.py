"""Correcao de atributo ja gravado — guardas e semantica do service.
Sem banco (sempre roda). O comportamento com linhas reais esta em
`test_editar_atributos_pg.py`."""
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException


def _listing(status="ready_to_publish"):
    listing = MagicMock()
    listing.id = uuid.uuid4()
    listing.seller_id = uuid.uuid4()
    listing.status = status
    listing.sku_external_id = "91"
    return listing


def _attr(attribute_id, value_name, *, tipo="string", required=False, allowed=None):
    return SimpleNamespace(
        attribute_id=attribute_id,
        attribute_name=attribute_id.title(),
        value_id=None,
        value_name=value_name,
        attribute_type=tipo,
        is_required=required,
        allowed_values=allowed,
        source="ai",
    )


def _db(atributos, *, generating=(), posicoes=(0, 1, 2, 3, 4)):
    """`db.execute` responde, em ordem: placeholders `generating`, atributos,
    posicoes existentes."""
    db = AsyncMock()
    respostas = []

    def _scalars(valores):
        r = MagicMock()
        r.scalars.return_value.all.return_value = list(valores)
        return r

    respostas.append(_scalars(generating))
    respostas.append(_scalars(atributos))
    respostas.append(_scalars(posicoes))
    db.execute = AsyncMock(side_effect=respostas)
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    return db


class TestGuardaDeStatus:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", [
        "generating_title",
        "predicting_category",
        "generating_images",
        "generating_description",
        "publishing",
        "published",
        "published_paused",
    ])
    async def test_recusa_status_perigoso_com_409_antes_de_consultar(self, status):
        from app.services.listing_service import ListingService

        db = _db([])
        with pytest.raises(HTTPException) as exc:
            await ListingService(db).edit_attributes(
                _listing(status), [{"attribute_id": "FLAVOR", "value_name": "Lichia"}],
                user_id=uuid.uuid4(),
            )
        assert exc.value.status_code == 409
        assert status in exc.value.detail
        db.execute.assert_not_awaited()
        db.add.assert_not_called()
        db.commit.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", [
        "draft", "pending_title_approval", "pending_seller_attributes",
        "pending_description", "pending_raw_photos", "pending_ai_engine",
        "pending_image_approval", "ready_to_publish", "failed",
    ])
    async def test_aceita_todo_status_editavel(self, status):
        from app.services.listing_service import ListingService

        attrs = [_attr("FLAVOR", "Chocolate")]
        db = _db(attrs)
        await ListingService(db).edit_attributes(
            _listing(status), [{"attribute_id": "FLAVOR", "value_name": "Lichia"}],
            user_id=uuid.uuid4(),
        )
        assert attrs[0].value_name == "Lichia"


class TestRegeneracaoEmAndamento:
    @pytest.mark.asyncio
    async def test_recusa_com_409_enquanto_ha_placeholder(self):
        from app.services.listing_service import ListingService

        db = _db([_attr("FLAVOR", "Chocolate")], generating=[2])
        with pytest.raises(HTTPException) as exc:
            await ListingService(db).edit_attributes(
                _listing(), [{"attribute_id": "FLAVOR", "value_name": "Lichia"}],
                user_id=uuid.uuid4(),
            )
        assert exc.value.status_code == 409
        assert "aguarde a conclusão antes de aprovar" in exc.value.detail
        db.add.assert_not_called()
        db.commit.assert_not_awaited()


class TestRecusaAntesDeEscrever:
    @pytest.mark.asyncio
    async def test_valor_fora_da_lista_em_tipo_list_recusa_com_422_sem_escrever(self):
        from app.services.listing_service import ListingService

        alvo = _attr("FLAVOR", "Chocolate", tipo="list",
                     allowed=[{"id": "1", "name": "Chocolate"}, {"id": "2", "name": "Baunilha"}])
        outro = _attr("COLOR", "Preto")
        db = _db([alvo, outro])
        with pytest.raises(HTTPException) as exc:
            await ListingService(db).edit_attributes(
                _listing(),
                [
                    {"attribute_id": "COLOR", "value_name": "Branco"},
                    {"attribute_id": "FLAVOR", "value_name": "Lichia"},
                ],
                user_id=uuid.uuid4(),
            )
        assert exc.value.status_code == 422
        # NENHUM dos dois foi escrito, nem o valido que vinha antes na lista.
        assert alvo.value_name == "Chocolate"
        assert outro.value_name == "Preto"
        db.add.assert_not_called()
        db.commit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_obrigatorio_esvaziado_recusa_com_422_sem_escrever(self):
        from app.services.listing_service import ListingService

        alvo = _attr("BRAND", "Wepink", required=True)
        db = _db([alvo])
        with pytest.raises(HTTPException) as exc:
            await ListingService(db).edit_attributes(
                _listing(), [{"attribute_id": "BRAND", "value_name": "   "}],
                user_id=uuid.uuid4(),
            )
        assert exc.value.status_code == 422
        assert "Brand" in exc.value.detail
        assert alvo.value_name == "Wepink"
        db.add.assert_not_called()
        db.commit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_opcional_pode_ser_esvaziado(self):
        from app.services.listing_service import ListingService

        alvo = _attr("FLAVOR", "Chocolate")
        db = _db([alvo])
        await ListingService(db).edit_attributes(
            _listing(), [{"attribute_id": "FLAVOR", "value_name": ""}],
            user_id=uuid.uuid4(),
        )
        assert alvo.value_name is None and alvo.value_id is None

    @pytest.mark.asyncio
    async def test_edicao_que_nao_muda_nada_recusa_com_422(self):
        """Mesma regra de `approve_images`: acao que nao faz nada nao e' acao,
        e o evento de auditoria exige `approved_count >= 1`."""
        from app.services.listing_service import ListingService

        db = _db([_attr("FLAVOR", "Chocolate")])
        with pytest.raises(HTTPException) as exc:
            await ListingService(db).edit_attributes(
                _listing(), [{"attribute_id": "FLAVOR", "value_name": "Chocolate"}],
                user_id=uuid.uuid4(),
            )
        assert exc.value.status_code == 422
        db.add.assert_not_called()
        db.commit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_atributo_de_outro_anuncio_e_ignorado(self):
        from app.services.listing_service import ListingService

        alvo = _attr("FLAVOR", "Chocolate")
        db = _db([alvo])
        await ListingService(db).edit_attributes(
            _listing(),
            [
                {"attribute_id": "NAO_EXISTE", "value_name": "x"},
                {"attribute_id": "FLAVOR", "value_name": "Lichia"},
            ],
            user_id=uuid.uuid4(),
        )
        assert alvo.value_name == "Lichia"


class TestNaoAvancaNemDispara:
    @pytest.mark.asyncio
    async def test_status_intocado_e_nenhuma_task_enfileirada(self):
        from app.services.listing_service import ListingService

        listing = _listing("ready_to_publish")
        db = _db([_attr("FLAVOR", "Chocolate")])
        with patch("app.workers.tasks.image_tasks.generate_images") as gi, \
             patch("app.workers.tasks.ai_tasks.generate_description") as gd, \
             patch("app.workers.tasks.publish_tasks.publish_listing") as pl:
            await ListingService(db).edit_attributes(
                listing, [{"attribute_id": "FLAVOR", "value_name": "Lichia"}],
                user_id=uuid.uuid4(),
            )
        assert listing.status == "ready_to_publish"
        gi.delay.assert_not_called()
        gd.delay.assert_not_called()
        pl.delay.assert_not_called()

    @pytest.mark.asyncio
    async def test_nao_apaga_imagem_nem_descricao(self):
        """Nenhum DELETE e nenhum UPDATE em massa: a unica escrita e' nos
        objetos de atributo carregados, mais o INSERT do evento."""
        from app.services.listing_service import ListingService

        db = _db([_attr("FLAVOR", "Chocolate")])
        await ListingService(db).edit_attributes(
            _listing(), [{"attribute_id": "FLAVOR", "value_name": "Lichia"}],
            user_id=uuid.uuid4(),
        )
        sql = " ".join(str(c.args[0]) for c in db.execute.await_args_list).lower()
        assert "delete" not in sql
        assert "update" not in sql


class TestEvento:
    @pytest.mark.asyncio
    async def test_grava_um_evento_attributes_edited_com_autor_e_contagem(self):
        from app.models.listing_review_event import (
            REVIEW_ACTION_ATTRIBUTES_EDITED,
            REVIEW_MODE_INDIVIDUAL,
        )
        from app.services.listing_service import ListingService

        user_id = uuid.uuid4()
        listing = _listing()
        db = _db([_attr("FLAVOR", "Chocolate"), _attr("COLOR", "Preto")])
        await ListingService(db).edit_attributes(
            listing,
            [
                {"attribute_id": "FLAVOR", "value_name": "Lichia"},
                {"attribute_id": "COLOR", "value_name": "Branco"},
            ],
            user_id=user_id,
        )
        assert db.add.call_count == 1
        evento = db.add.call_args.args[0]
        assert evento.listing_id == listing.id
        assert evento.user_id == user_id
        assert evento.action == REVIEW_ACTION_ATTRIBUTES_EDITED
        assert evento.mode == REVIEW_MODE_INDIVIDUAL
        assert evento.approved_count == 2
        assert evento.review_seconds is None

    @pytest.mark.asyncio
    async def test_contagem_ignora_valor_reenviado_igual(self):
        from app.services.listing_service import ListingService

        db = _db([_attr("FLAVOR", "Chocolate"), _attr("COLOR", "Preto")])
        await ListingService(db).edit_attributes(
            _listing(),
            [
                {"attribute_id": "FLAVOR", "value_name": "Lichia"},
                {"attribute_id": "COLOR", "value_name": "Preto"},
            ],
            user_id=uuid.uuid4(),
        )
        assert db.add.call_args.args[0].approved_count == 1


class TestRetorno:
    @pytest.mark.asyncio
    async def test_marca_a_posicao_1_quando_o_volume_muda(self):
        from app.services.listing_service import ListingService

        db = _db([_attr("UNIT_VOLUME", "200 ml"), _attr("BRAND", "Wepink"),
                  _attr("MODEL", "Fatal Black")])
        stale, _dup = await ListingService(db).edit_attributes(
            _listing(), [{"attribute_id": "UNIT_VOLUME", "value_name": "100 ml"}],
            user_id=uuid.uuid4(),
        )
        assert 1 in stale

    @pytest.mark.asyncio
    async def test_duplicated_fields_avisa_model_e_brand(self):
        """MODEL/BRAND existem em duplicata: o atributo alimenta a ficha, a
        COLUNA do listing alimenta a apresentacao. Corrigir um nao corrige o
        outro — a tela precisa dizer isso."""
        from app.services.listing_service import ListingService

        db = _db([_attr("MODEL", "Lichia"), _attr("BRAND", "Wepink"),
                  _attr("FLAVOR", "Chocolate")])
        _stale, dup = await ListingService(db).edit_attributes(
            _listing(),
            [
                {"attribute_id": "MODEL", "value_name": "Fatal Black"},
                {"attribute_id": "FLAVOR", "value_name": "Lichia"},
            ],
            user_id=uuid.uuid4(),
        )
        assert dup == ["MODEL"]
