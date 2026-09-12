"""Testes com Postgres real (Task 1 do plano
`2026-09-11-listagem-em-escala`): `ListingService.count_by_status` agrega em
UMA consulta (GROUP BY status) e sempre devolve as 16 chaves de
`LISTING_STATUSES`, com zero quando nao ha anuncio naquele status.

So roda com `TEST_DATABASE_URL` apontando pro banco dedicado `publicar_test`
(nunca contra o banco de desenvolvimento) — mesma infraestrutura de
`test_bulk_approve_por_posicao.py`. Nunca rodar esta suite e outra que use
`publicar_test` ao mesmo tempo: ambas fazem `drop_all`/`create_all`.
"""
import os
from datetime import datetime, timedelta, timezone
from itertools import count
from uuid import uuid4

import pytest

TEST_DB = os.environ.get("TEST_DATABASE_URL")
_precisa_db = pytest.mark.skipif(
    not TEST_DB, reason="TEST_DATABASE_URL nao definido: teste real so roda com Postgres dedicado"
)

# Contador de processo pra gerar mlb_id distintos entre chamadas de _semear
# dentro do mesmo teste (a coluna e' UNIQUE) sem depender de uuid4 inteiro
# (mais dificil de ler numa falha).
_contador_mlb = count(1)


async def _preparar_banco():
    """Cria o engine do banco dedicado (`_engine_dedicado`, que valida o nome
    do banco ANTES de conectar e le `TEST_DATABASE_URL` sozinho), zera o
    schema e devolve `(engine, session_maker)`. Quem chama e' responsavel por
    `await engine.dispose()` num `finally`."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from tests._pg_dedicado import _engine_dedicado

    import app.models  # noqa: F401 — registra todas as tabelas
    from app.models.base import Base

    engine = _engine_dedicado()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    return engine, sm


async def _semear(session_maker, especificacao):
    """Cria 1 user e N sellers (um por item de `especificacao`); pra cada
    seller, cria os listings descritos na lista de dicts correspondente.

    `especificacao` e' uma lista de listas de dicts: `especificacao[i]` sao
    os listings do i-esimo seller criado. Cada dict aceita:
    - `status` (obrigatorio)
    - `sku` -> `Listing.sku_external_id`
    - `title` -> `Listing.selected_title`
    - `brand` -> `Listing.sku_brand` (default "b")
    - `mlb_id` -> `Listing.mlb_id` (UNIQUE na tabela: se omitido, gera um
      valor distinto usando o contador de modulo, pra nao colidir entre
      listings de testes diferentes)

    Devolve `(user_id, [seller_ids])`, na mesma ordem dos sellers recebidos
    em `especificacao` — quem chama identifica "o seller A" e "o seller B"
    pela posicao na lista devolvida.

    Reusado pela Task 2 (filtro multi-status e busca), por isso fica no nivel
    do modulo em vez de dentro de uma classe de teste.
    """
    from app.models.listing import Listing
    from app.models.seller import Seller
    from app.models.user import User

    async with session_maker() as s:
        user = User(email=f"{uuid4()}@t.local", password_hash="x", full_name="t")
        s.add(user)
        await s.flush()

        seller_ids = []
        for listings_do_seller in especificacao:
            seller = Seller(
                ml_user_id=int(str(uuid4().int)[:9]),
                ml_nickname="t",
                access_token_enc="x",
                refresh_token_enc="x",
                token_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            )
            s.add(seller)
            await s.flush()
            seller_ids.append(seller.id)

            for spec in listings_do_seller:
                mlb_id = spec.get("mlb_id")
                if mlb_id is None:
                    mlb_id = f"MLB{next(_contador_mlb):010d}"
                s.add(
                    Listing(
                        seller_id=seller.id,
                        created_by=user.id,
                        sku_external_id=spec.get("sku"),
                        sku_description="d",
                        sku_brand=spec.get("brand", "b"),
                        price=10,
                        stock_quantity=1,
                        condition="new",
                        listing_type_id="gold_special",
                        status=spec["status"],
                        created_via="batch",
                        mlb_id=mlb_id,
                        selected_title=spec.get("title"),
                    )
                )
        await s.commit()
        return user.id, seller_ids


@_precisa_db
class TestContagemPorStatus:
    @pytest.mark.asyncio
    async def test_conta_por_status_do_seller_ativo_com_todas_as_16_chaves(self):
        """Seller A tem 3 failed, 2 ready_to_publish, 1 draft; seller B tem 4
        failed (isolamento multi-tenant: a contagem do A nao pode contar os
        do B). `count_by_status(A)` devolve as 16 chaves de
        `LISTING_STATUSES`, com os 3 status semeados corretos e os 13
        restantes em zero (`pending_raw_photos` conferido explicitamente como
        exemplo de zero)."""
        from app.models.listing import LISTING_STATUSES
        from app.services.listing_service import ListingService

        engine, sm = await _preparar_banco()
        try:
            especificacao = [
                (["failed"] * 3) + (["ready_to_publish"] * 2) + ["draft"],
                ["failed"] * 4,
            ]
            especificacao = [[{"status": st} for st in lista] for lista in especificacao]
            user_id, (seller_a, seller_b) = await _semear(sm, especificacao)

            async with sm() as s:
                svc = ListingService(s)
                resultado = await svc.count_by_status(seller_a)

            assert set(resultado.by_status.keys()) == set(LISTING_STATUSES)
            assert resultado.by_status["failed"] == 3, resultado.by_status
            assert resultado.by_status["ready_to_publish"] == 2, resultado.by_status
            assert resultado.by_status["draft"] == 1, resultado.by_status
            assert resultado.by_status["pending_raw_photos"] == 0, resultado.by_status
            assert all(v >= 0 for v in resultado.by_status.values()), resultado.by_status
            assert resultado.total == 6, resultado.total
            assert sum(resultado.by_status.values()) == resultado.total
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_seller_sem_nenhum_anuncio_devolve_16_chaves_zeradas(self):
        """Barra de resumo nao pode quebrar (nem sumir chave) pro seller que
        acabou de conectar a conta ML e ainda nao tem nenhum anuncio."""
        from app.models.listing import LISTING_STATUSES
        from app.services.listing_service import ListingService

        engine, sm = await _preparar_banco()
        try:
            user_id, (seller_id,) = await _semear(sm, [[]])

            async with sm() as s:
                svc = ListingService(s)
                resultado = await svc.count_by_status(seller_id)

            assert set(resultado.by_status.keys()) == set(LISTING_STATUSES)
            assert all(v == 0 for v in resultado.by_status.values()), resultado.by_status
            assert resultado.total == 0
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_status_legado_fora_da_lista_aparece_e_entra_no_total(self):
        """Um status que nao esta mais em `LISTING_STATUSES` (dado legado,
        por exemplo de uma fase anterior do pipeline) nao pode sumir da
        contagem: aparece com a propria chave, alem das 16 canonicas, e conta
        no total. Perder um anuncio da conta e' pior do que mostrar uma
        chave extra."""
        from app.models.listing import LISTING_STATUSES
        from app.services.listing_service import ListingService

        engine, sm = await _preparar_banco()
        try:
            user_id, (seller_id,) = await _semear(
                sm, [[{"status": "draft"}, {"status": "legado_x"}]]
            )

            async with sm() as s:
                svc = ListingService(s)
                resultado = await svc.count_by_status(seller_id)

            assert set(LISTING_STATUSES).issubset(resultado.by_status.keys())
            assert resultado.by_status["legado_x"] == 1, resultado.by_status
            assert resultado.by_status["draft"] == 1, resultado.by_status
            assert resultado.total == 2, resultado.total
            assert sum(resultado.by_status.values()) == resultado.total
        finally:
            await engine.dispose()
