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

# Sentinela pra distinguir "mlb_id omitido" (gera um valor) de "mlb_id
# explicitamente None" (deixa NULL) — spec.get("mlb_id") sozinho nao
# consegue, porque os dois casos devolvem None.
_AUSENTE = object()


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
    - `description` -> `Listing.sku_description` (default "d"; e' o unico
      campo de texto que SEMPRE existe, mesmo antes de o titulo ser escolhido)
    - `mlb_id` -> `Listing.mlb_id` (UNIQUE na tabela: se a chave for
      OMITIDA, gera um valor distinto usando o contador de modulo, pra nao
      colidir entre listings de testes diferentes; se vier explicitamente
      `None`, o listing nasce com `mlb_id` NULL de proposito — os dois casos
      sao distintos via sentinela `_AUSENTE`, nao dariam pra diferenciar com
      `spec.get("mlb_id")` sozinho)

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
                mlb_id = spec.get("mlb_id", _AUSENTE)
                if mlb_id is _AUSENTE:
                    mlb_id = f"MLB{next(_contador_mlb):010d}"
                s.add(
                    Listing(
                        seller_id=seller.id,
                        created_by=user.id,
                        sku_external_id=spec.get("sku"),
                        sku_description=spec.get("description", "d"),
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


@_precisa_db
class TestFiltroPorVariosStatus:
    """`list_listings` ganha suporte a lista de status (Task 2): cada
    agrupamento da fila junta 3 ou 4 status numa so consulta. O caso de um
    unico status (str, como hoje) precisa continuar identico — e' o que o
    quadro atual chama."""

    async def _semear_seller_a(self, sm):
        """Seller A: 2 failed, 2 ready_to_publish, 1 draft, 1 published (6
        no total). Seller B nao entra aqui porque estes casos sao so sobre
        o filtro de status, nao sobre isolamento multi-tenant (isso e'
        coberto em TestBusca)."""
        especificacao = [
            ([{"status": "failed"}] * 2)
            + ([{"status": "ready_to_publish"}] * 2)
            + [{"status": "draft"}]
            + [{"status": "published"}]
        ]
        user_id, (seller_a,) = await _semear(sm, [especificacao[0]])
        return seller_a

    @pytest.mark.asyncio
    async def test_lista_de_dois_status_junta_os_dois_grupos(self):
        from app.services.listing_service import ListingService

        engine, sm = await _preparar_banco()
        try:
            seller_a = await self._semear_seller_a(sm)
            async with sm() as s:
                pagina = await ListingService(s).list_listings(
                    seller_a, ["failed", "ready_to_publish"], 1, 20
                )
            assert pagina.total == 4, pagina.total
            assert all(item.status in ("failed", "ready_to_publish") for item in pagina.items)
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_string_unica_continua_funcionando_como_hoje(self):
        from app.services.listing_service import ListingService

        engine, sm = await _preparar_banco()
        try:
            seller_a = await self._semear_seller_a(sm)
            async with sm() as s:
                pagina = await ListingService(s).list_listings(seller_a, "failed", 1, 20)
            assert pagina.total == 2, pagina.total
            assert all(item.status == "failed" for item in pagina.items)
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_lista_de_um_elemento_e_igual_a_string(self):
        """`["failed"]` (lista de 1) devolve os mesmos ids que `"failed"`
        (str) — o parametro da rota vira lista quando repetido, mas o
        comportamento de um so valor nao pode mudar."""
        from app.services.listing_service import ListingService

        engine, sm = await _preparar_banco()
        try:
            seller_a = await self._semear_seller_a(sm)
            async with sm() as s:
                svc = ListingService(s)
                pagina_str = await svc.list_listings(seller_a, "failed", 1, 20)
                pagina_lista = await svc.list_listings(seller_a, ["failed"], 1, 20)
            assert pagina_lista.total == pagina_str.total == 2
            assert {i.id for i in pagina_lista.items} == {i.id for i in pagina_str.items}
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_status_none_nao_filtra(self):
        from app.services.listing_service import ListingService

        engine, sm = await _preparar_banco()
        try:
            seller_a = await self._semear_seller_a(sm)
            async with sm() as s:
                pagina = await ListingService(s).list_listings(seller_a, None, 1, 20)
            assert pagina.total == 6, pagina.total
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_lista_vazia_nao_filtra(self):
        from app.services.listing_service import ListingService

        engine, sm = await _preparar_banco()
        try:
            seller_a = await self._semear_seller_a(sm)
            async with sm() as s:
                pagina = await ListingService(s).list_listings(seller_a, [], 1, 20)
            assert pagina.total == 6, pagina.total
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_status_inexistente_devolve_vazio_sem_erro(self):
        from app.services.listing_service import ListingService

        engine, sm = await _preparar_banco()
        try:
            seller_a = await self._semear_seller_a(sm)
            async with sm() as s:
                pagina = await ListingService(s).list_listings(seller_a, ["nao_existe"], 1, 20)
            assert pagina.total == 0, pagina.total
            assert pagina.items == []
        finally:
            await engine.dispose()


@_precisa_db
class TestBusca:
    """`search` casa por SKU, titulo, marca e MLB — mesmo padrao
    (`ilike` + `or_`) de `ProductService.list_products`."""

    async def _semear_ab(self, sm):
        seller_a_listings = [
            {
                "status": "draft",
                "sku": "SKU-ALFA",
                "title": "Perfume Wepink Martin 100ml",
                "brand": "Wepink",
                "mlb_id": "MLB111",
            },
            {
                "status": "failed",
                "sku": "SKU-BETA",
                "title": "Body Splash Fatal",
                "brand": "Fatal",
                "mlb_id": "MLB222",
            },
            {
                "status": "draft",
                "sku": "SKU-GAMA",
                "title": None,
                "brand": "Outra",
                "mlb_id": None,
            },
        ]
        seller_b_listings = [
            {
                "status": "draft",
                "sku": "SKU-ALFA-B",
                "title": "Perfume Wepink",
                "brand": "Wepink",
                "mlb_id": "MLB333",
            },
        ]
        user_id, (seller_a, seller_b) = await _semear(
            sm, [seller_a_listings, seller_b_listings]
        )
        return seller_a, seller_b

    @pytest.mark.asyncio
    async def test_busca_por_sku_case_insensitive_parcial_nao_vaza_outro_seller(self):
        from app.services.listing_service import ListingService

        engine, sm = await _preparar_banco()
        try:
            seller_a, seller_b = await self._semear_ab(sm)
            async with sm() as s:
                pagina = await ListingService(s).list_listings(
                    seller_a, None, 1, 20, search="alfa"
                )
            assert pagina.total == 1, pagina.total
            assert pagina.items[0].sku_external_id == "SKU-ALFA"
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_busca_por_titulo(self):
        from app.services.listing_service import ListingService

        engine, sm = await _preparar_banco()
        try:
            seller_a, seller_b = await self._semear_ab(sm)
            async with sm() as s:
                pagina = await ListingService(s).list_listings(
                    seller_a, None, 1, 20, search="martin"
                )
            assert pagina.total == 1, pagina.total
            assert pagina.items[0].sku_external_id == "SKU-ALFA"
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_busca_por_marca(self):
        """"fatal" tambem casaria pelo titulo ("Body Splash Fatal"), entao
        nao prova nada sobre a clausula de marca — o teste passaria mesmo
        com `Listing.sku_brand.ilike(...)` apagado do `or_`. "outra" so
        aparece na marca do SKU-GAMA (`title=None`), entao so a clausula de
        marca pode fazer esta busca casar."""
        from app.services.listing_service import ListingService

        engine, sm = await _preparar_banco()
        try:
            seller_a, seller_b = await self._semear_ab(sm)
            async with sm() as s:
                pagina = await ListingService(s).list_listings(
                    seller_a, None, 1, 20, search="outra"
                )
            assert pagina.total == 1, pagina.total
            assert pagina.items[0].sku_external_id == "SKU-GAMA"
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_busca_por_mlb_id(self):
        from app.services.listing_service import ListingService

        engine, sm = await _preparar_banco()
        try:
            seller_a, seller_b = await self._semear_ab(sm)
            async with sm() as s:
                pagina = await ListingService(s).list_listings(
                    seller_a, None, 1, 20, search="MLB222"
                )
            assert pagina.total == 1, pagina.total
            assert pagina.items[0].sku_external_id == "SKU-BETA"
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_busca_por_sku_description_sem_titulo(self):
        """Antes de o titulo ser escolhido (draft, generating_title,
        pending_title_approval...), `selected_title` e' NULL — e e' justamente
        nesse momento que o operador procura um anuncio parado. A descricao de
        origem (`sku_description`) e' o unico campo de texto sempre preenchido,
        entao a busca precisa casar por ela. O termo ("hidratante") nao aparece
        em SKU, marca nem mlb_id: so a clausula de `sku_description` pode fazer
        esta busca casar."""
        from app.services.listing_service import ListingService

        engine, sm = await _preparar_banco()
        try:
            user_id, (seller_a,) = await _semear(
                sm,
                [[
                    {
                        "status": "pending_title_approval",
                        "sku": "SKU-DELTA",
                        "title": None,
                        "brand": "Nivea",
                        "description": "Hidratante Corporal 200ml",
                    },
                    {
                        "status": "draft",
                        "sku": "SKU-EPSILON",
                        "title": None,
                        "brand": "Outra",
                        "description": "Sabonete Liquido 250ml",
                    },
                ]],
            )
            async with sm() as s:
                pagina = await ListingService(s).list_listings(
                    seller_a, None, 1, 20, search="hidratante"
                )
            assert pagina.total == 1, pagina.total
            assert pagina.items[0].sku_external_id == "SKU-DELTA"
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_busca_combina_com_filtro_de_status(self):
        from app.services.listing_service import ListingService

        engine, sm = await _preparar_banco()
        try:
            seller_a, seller_b = await self._semear_ab(sm)
            async with sm() as s:
                svc = ListingService(s)
                pagina_failed = await svc.list_listings(
                    seller_a, ["failed"], 1, 20, search="wepink"
                )
                pagina_draft = await svc.list_listings(
                    seller_a, ["draft"], 1, 20, search="wepink"
                )
            assert pagina_failed.total == 0, pagina_failed.total
            assert pagina_draft.total == 1, pagina_draft.total
            assert pagina_draft.items[0].sku_external_id == "SKU-ALFA"
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_busca_so_com_espacos_nao_filtra(self):
        from app.services.listing_service import ListingService

        engine, sm = await _preparar_banco()
        try:
            seller_a, seller_b = await self._semear_ab(sm)
            async with sm() as s:
                pagina = await ListingService(s).list_listings(
                    seller_a, None, 1, 20, search="   "
                )
            assert pagina.total == 3, pagina.total
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_busca_sem_resultado(self):
        from app.services.listing_service import ListingService

        engine, sm = await _preparar_banco()
        try:
            seller_a, seller_b = await self._semear_ab(sm)
            async with sm() as s:
                pagina = await ListingService(s).list_listings(
                    seller_a, None, 1, 20, search="zzz"
                )
            assert pagina.total == 0, pagina.total
            assert pagina.items == []
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_nenhum_resultado_traz_id_de_outro_seller(self):
        from app.services.listing_service import ListingService

        engine, sm = await _preparar_banco()
        try:
            seller_a, seller_b = await self._semear_ab(sm)
            async with sm() as s:
                # "wepink" casa em A (marca/titulo) e em B (marca/titulo);
                # filtrando pelo seller A, nenhum item pode ter vindo de B.
                pagina = await ListingService(s).list_listings(
                    seller_a, None, 1, 20, search="wepink"
                )
            assert pagina.total == 1, pagina.total
            assert pagina.items[0].sku_external_id == "SKU-ALFA"
        finally:
            await engine.dispose()


@_precisa_db
class TestPaginacao:
    @pytest.mark.asyncio
    async def test_paginacao_com_filtro_de_status(self):
        from app.services.listing_service import ListingService

        engine, sm = await _preparar_banco()
        try:
            especificacao = [([{"status": "failed"}] * 5) + ([{"status": "draft"}] * 3)]
            user_id, (seller_a,) = await _semear(sm, especificacao)

            async with sm() as s:
                svc = ListingService(s)
                pagina_1 = await svc.list_listings(seller_a, ["failed"], 1, 2, search=None)
                pagina_3 = await svc.list_listings(seller_a, ["failed"], 3, 2, search=None)
                pagina_50 = await svc.list_listings(seller_a, ["failed"], 50, 2, search=None)

            assert len(pagina_1.items) == 2
            assert pagina_1.total == 5
            assert pagina_1.page == 1
            assert pagina_1.page_size == 2

            assert len(pagina_3.items) == 1
            assert pagina_3.total == 5

            assert pagina_50.items == []
            assert pagina_50.total == 5
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_paginacao_sem_duplicata_nem_lacuna_com_created_at_empatado(self):
        """Os 5 listings nascem na MESMA chamada de `_semear` (1 unico
        `await s.commit()`), entao compartilham o `created_at` da transacao
        — exatamente o cenario de um batch import. Sem desempate por `id`
        em `order_by`, a ordem entre eles fica a criterio do plano do
        Postgres, e paginando com OFFSET/LIMIT um listing pode aparecer em
        duas paginas (duplicata) ou em nenhuma (lacuna). Percorre TODAS as
        paginas ate uma vazia e confere que a uniao dos ids bate exatamente
        com o conjunto semeado, sem repeticao."""
        from app.services.listing_service import ListingService

        engine, sm = await _preparar_banco()
        try:
            especificacao = [[{"status": "failed"}] * 5]
            user_id, (seller_a,) = await _semear(sm, especificacao)

            ids_coletados = []
            pagina_num = 1
            async with sm() as s:
                svc = ListingService(s)
                while True:
                    pagina = await svc.list_listings(seller_a, ["failed"], pagina_num, 2)
                    if not pagina.items:
                        break
                    ids_coletados.extend(item.id for item in pagina.items)
                    pagina_num += 1
                    assert pagina_num < 20, "paginacao nao terminou — possivel loop infinito"

            assert len(ids_coletados) == len(set(ids_coletados)) == 5, ids_coletados

        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_paginacao_com_busca(self):
        from app.services.listing_service import ListingService

        engine, sm = await _preparar_banco()
        try:
            especificacao = [
                [{"status": "failed", "title": "Perfume Wepink"}]
                + [{"status": "failed", "title": "Perfume Outra Marca"}]
                + [{"status": "failed", "title": t} for t in ("A", "B", "C")]
            ]
            user_id, (seller_a,) = await _semear(sm, especificacao)

            async with sm() as s:
                pagina = await ListingService(s).list_listings(
                    seller_a, None, 1, 20, search="perfume"
                )
            assert pagina.total == 2, pagina.total
        finally:
            await engine.dispose()


@_precisa_db
class TestParametroStatusNaRota:
    """HTTP de verdade via ASGITransport: prova que o parametro `status`
    repetido chega como lista no endpoint e que a rota `/status-counts`
    continua casando antes de `/{listing_id}`."""

    async def _preparar_app(self, sm, seller_id):
        from types import SimpleNamespace

        from app.core.dependencies import get_active_seller, get_db
        from app.main import app

        async def _override_get_db():
            async with sm() as session:
                yield session

        async def _override_get_active_seller():
            return SimpleNamespace(id=seller_id)

        app.dependency_overrides[get_db] = _override_get_db
        app.dependency_overrides[get_active_seller] = _override_get_active_seller

    @pytest.mark.asyncio
    async def test_get_com_dois_status_repetidos(self):
        from httpx import ASGITransport, AsyncClient

        from app.main import app

        engine, sm = await _preparar_banco()
        try:
            especificacao = [
                ([{"status": "failed"}] * 2) + ([{"status": "ready_to_publish"}] * 1)
            ]
            user_id, (seller_a,) = await _semear(sm, especificacao)
            await self._preparar_app(sm, seller_a)
            try:
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    resp = await client.get(
                        "/api/v1/listings",
                        params=[("status", "failed"), ("status", "ready_to_publish")],
                    )
                assert resp.status_code == 200, resp.text
                assert resp.json()["total"] == 3, resp.json()
            finally:
                app.dependency_overrides.clear()
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_get_com_um_status_igual_a_hoje(self):
        from httpx import ASGITransport, AsyncClient

        from app.main import app

        engine, sm = await _preparar_banco()
        try:
            especificacao = [
                ([{"status": "failed"}] * 2) + ([{"status": "ready_to_publish"}] * 1)
            ]
            user_id, (seller_a,) = await _semear(sm, especificacao)
            await self._preparar_app(sm, seller_a)
            try:
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    resp = await client.get("/api/v1/listings", params={"status": "failed"})
                assert resp.status_code == 200, resp.text
                assert resp.json()["total"] == 2, resp.json()
            finally:
                app.dependency_overrides.clear()
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_get_com_search(self):
        from httpx import ASGITransport, AsyncClient

        from app.main import app

        engine, sm = await _preparar_banco()
        try:
            especificacao = [[{"status": "draft", "sku": "SKU-ALFA"}]]
            user_id, (seller_a,) = await _semear(sm, especificacao)
            await self._preparar_app(sm, seller_a)
            try:
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    resp = await client.get("/api/v1/listings", params={"search": "alfa"})
                assert resp.status_code == 200, resp.text
                assert resp.json()["total"] == 1, resp.json()
            finally:
                app.dependency_overrides.clear()
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_status_counts_nao_cai_no_path_param(self):
        """Prova que a ordem de declaracao das rotas continua correta: sem
        isso, "status-counts" seria interpretado como {listing_id} e
        devolveria 422 em vez do resumo."""
        from httpx import ASGITransport, AsyncClient

        from app.main import app

        engine, sm = await _preparar_banco()
        try:
            especificacao = [[{"status": "draft"}]]
            user_id, (seller_a,) = await _semear(sm, especificacao)
            await self._preparar_app(sm, seller_a)
            try:
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    resp = await client.get("/api/v1/listings/status-counts")
                assert resp.status_code == 200, resp.text
                data = resp.json()
                assert "by_status" in data
                assert "total" in data
            finally:
                app.dependency_overrides.clear()
        finally:
            await engine.dispose()
