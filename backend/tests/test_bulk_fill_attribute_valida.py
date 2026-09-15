"""A grade de atributos em massa passa a validar, como o caminho individual.
Sem banco (sempre roda)."""
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


def _attr(attribute_id="FLAVOR", *, tipo="list", allowed=None, value_name=None):
    return SimpleNamespace(
        attribute_id=attribute_id,
        attribute_name="Sabor",
        value_id=None,
        value_name=value_name,
        attribute_type=tipo,
        is_required=False,
        allowed_values=allowed if allowed is not None else [
            {"id": "1", "name": "Chocolate"},
            {"id": "2", "name": "Baunilha"},
        ],
        source="ai",
    )


def _listing():
    listing = MagicMock()
    listing.id = uuid.uuid4()
    listing.status = "pending_seller_attributes"
    return listing


def _db(listing, attr, *, obrigatorios_vazios=()):
    """execute responde: listing, atributo, obrigatorios sem valor."""
    db = AsyncMock()

    def _one(v):
        r = MagicMock()
        r.scalar_one_or_none.return_value = v
        return r

    def _all(v):
        r = MagicMock()
        r.scalars.return_value.all.return_value = list(v)
        return r

    db.execute = AsyncMock(side_effect=[_one(listing), _one(attr), _all(obrigatorios_vazios)])
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    return db


class TestValidacao:
    @pytest.mark.asyncio
    async def test_valor_fora_da_enumeracao_falha_o_item_sem_gravar(self):
        from app.services.listing_service import ListingService

        listing, attr = _listing(), _attr()
        db = _db(listing, attr)
        svc = ListingService(db, seller_id=uuid.uuid4())
        res = await svc.bulk_fill_attribute([listing.id], "FLAVOR", "Lichia", None)
        assert res.failed == 1 and res.processed == 0
        assert res.results[0].success is False
        assert attr.value_name is None
        assert attr.source == "ai"

    @pytest.mark.asyncio
    async def test_mensagem_de_erro_cabe_na_tela(self):
        """`sanitizeBulkError` troca por mensagem generica acima de 200
        chars — a lista inteira de aceitos nunca chegaria ao operador."""
        from app.services.listing_service import ListingService

        listing, attr = _listing(), _attr(
            allowed=[{"id": str(i), "name": f"Sabor numero {i}"} for i in range(40)]
        )
        db = _db(listing, attr)
        svc = ListingService(db, seller_id=uuid.uuid4())
        res = await svc.bulk_fill_attribute([listing.id], "FLAVOR", "Lichia", None)
        erro = res.results[0].error
        assert erro and len(erro) <= 200
        for marcador in ("sql", "traceback", "sqlalchemy"):
            assert marcador not in erro.lower()

    @pytest.mark.asyncio
    async def test_valor_valido_grava_e_marca_source_seller(self):
        from app.services.listing_service import ListingService

        listing, attr = _listing(), _attr()
        db = _db(listing, attr)
        svc = ListingService(db, seller_id=uuid.uuid4())
        res = await svc.bulk_fill_attribute([listing.id], "FLAVOR", "Chocolate", None)
        assert res.processed == 1 and res.failed == 0
        assert attr.value_name == "Chocolate"
        assert attr.value_id == "1"  # resolvido a partir do nome
        assert attr.source == "seller"

    @pytest.mark.asyncio
    async def test_texto_livre_passa_em_tipo_string(self):
        """Mesma regra do caminho individual: em `string` a lista do ML e'
        de sugestoes, nao enumeracao."""
        from app.services.listing_service import ListingService

        listing, attr = _listing(), _attr(tipo="string")
        db = _db(listing, attr)
        svc = ListingService(db, seller_id=uuid.uuid4())
        res = await svc.bulk_fill_attribute([listing.id], "FLAVOR", "Lichia", None)
        assert res.processed == 1
        assert attr.value_name == "Lichia"

    @pytest.mark.asyncio
    async def test_atributo_inexistente_continua_falhando_o_item(self):
        from app.services.listing_service import ListingService

        listing = _listing()
        db = _db(listing, None)
        svc = ListingService(db, seller_id=uuid.uuid4())
        res = await svc.bulk_fill_attribute([listing.id], "FLAVOR", "Chocolate", None)
        assert res.failed == 1
        assert "atributo não encontrado" in res.results[0].error


class TestComportamentoPreservado:
    @pytest.mark.asyncio
    async def test_avanca_para_pending_description_quando_nao_resta_obrigatorio(self):
        """`bulk_fill_attribute` e' o gemeo em LOTE de `submit_attributes`
        (preenchimento), nao da correcao: continua avancando a etapa."""
        from app.services.listing_service import ListingService

        listing, attr = _listing(), _attr()
        db = _db(listing, attr, obrigatorios_vazios=())
        svc = ListingService(db, seller_id=uuid.uuid4())
        await svc.bulk_fill_attribute([listing.id], "FLAVOR", "Chocolate", None)
        assert listing.status == "pending_description"

    @pytest.mark.asyncio
    async def test_nao_grava_evento_de_auditoria(self):
        """Preenchimento nao gera evento — `submit_attributes` tambem nao."""
        from app.services.listing_service import ListingService

        listing, attr = _listing(), _attr()
        db = _db(listing, attr)
        db.add = MagicMock()
        svc = ListingService(db, seller_id=uuid.uuid4())
        await svc.bulk_fill_attribute([listing.id], "FLAVOR", "Chocolate", None)
        db.add.assert_not_called()
