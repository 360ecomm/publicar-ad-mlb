"""`approved_image_count` depois do commit: nunca estoura e sai com o valor certo.

O bug (reproduzido em 2026-09-12 na prova da tela de revisao): `Listing.approved_image_count`
e' `column_property` com subconsulta, e o SQLAlchemy expira column_property de
expressao em TODO flush em que o objeto esta sujo (`expire_on_flush=True` e' o
padrao). Depois do commit, `ListingSummary.model_validate(listing)` tentava
recarregar o atributo dentro do contexto async e estourava `MissingGreenlet`
— 500 SEM cabecalho CORS, com a transacao ja commitada (o anuncio andava e o
operador via falha). Em producao desde `c2bc490`.

Por que nenhum teste pegou: os testes de endpoint usam sessao SIMULADA
(`AsyncMock`), sem o ciclo real carregar → alterar → flush → serializar; e'
o flush real que expira o atributo. Os testes de Postgres deste arquivo
fazem exatamente esse ciclo, com a sessao de verdade e o `commit()` de
verdade — e' o unico jeito de este bug aparecer num teste.

Correcao (combinacao):
- (a) `expire_on_flush=False` na `column_property`: serializar depois do
  commit nunca mais estoura, em nenhum caminho, mesmo onde alguem esquecer a
  releitura. Sozinha, porem, devolveria o valor VELHO quando a propria
  requisicao mudou a contagem (aprova 5 e a resposta diz 0).
- (b) `ListingService.summary_after_commit(listing)`: UM ponto que faz
  `refresh` (uma consulta) e serializa; todos os endpoints que commitam e
  devolvem `ListingSummary` passam por ele. O teste sem banco abaixo trava
  isso por AST, para o 12o endpoint nao nascer com o `model_validate` solto.

Postgres real so com `TEST_DATABASE_URL` apontando para `publicar_test`
(`_pg_dedicado`); os de AST rodam sempre.
"""
import ast
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tests.test_bulk_approve_por_posicao import _linhas_padrao, _preparar_banco, _semear

TEST_DB = os.environ.get("TEST_DATABASE_URL")
_precisa_db = pytest.mark.skipif(
    not TEST_DB, reason="TEST_DATABASE_URL nao definido: teste real so roda com Postgres dedicado"
)

ENDPOINTS = Path(__file__).resolve().parents[1] / "app" / "api" / "v1" / "endpoints" / "listings.py"

# Os 11 endpoints de acao que commitam e devolvem ListingSummary (passo 0 da
# tarefa de 2026-09-12). `create_listing` fica de fora da lista porque o
# service faz `refresh` e devolve o ORM — a resposta ja sai recarregada.
ENDPOINTS_DE_ACAO = {
    "start_pipeline",
    "retry_pipeline",
    "select_title",
    "submit_attributes",
    "generate_images",
    "resume_raw_photos",
    "resume_ai_engine",
    "approve_images",
    "publish_listing",
    "promote_cover",
    "promote_specs",
}


def _endpoints_que_devolvem_listing_summary() -> dict[str, ast.AsyncFunctionDef]:
    tree = ast.parse(ENDPOINTS.read_text(encoding="utf-8"))
    achados = {}
    for node in tree.body:
        if not isinstance(node, ast.AsyncFunctionDef):
            continue
        for deco in node.decorator_list:
            if not isinstance(deco, ast.Call) or not isinstance(deco.func, ast.Attribute):
                continue
            if deco.func.attr not in ("post", "put"):
                continue
            for kw in deco.keywords:
                if kw.arg == "response_model" and isinstance(kw.value, ast.Name) and kw.value.id == "ListingSummary":
                    achados[node.name] = node
    return achados


def _chama(node: ast.AST, nome_do_metodo: str) -> bool:
    return any(
        isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == nome_do_metodo
        for n in ast.walk(node)
    )


def _serializa_direto(node: ast.AST) -> bool:
    """`ListingSummary.model_validate(...)` solto no corpo do endpoint."""
    for n in ast.walk(node):
        if (
            isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "model_validate"
            and isinstance(n.func.value, ast.Name)
            and n.func.value.id == "ListingSummary"
        ):
            return True
    return False


class TestTodosOsEndpointsDeAcaoPassamPeloPontoUnico:
    def test_a_lista_do_passo_0_bate_com_o_arquivo(self):
        achados = _endpoints_que_devolvem_listing_summary()
        assert set(achados) - {"create_listing"} == ENDPOINTS_DE_ACAO, sorted(achados)

    def test_nenhum_endpoint_de_acao_serializa_solto(self):
        achados = _endpoints_que_devolvem_listing_summary()
        soltos = [nome for nome in ENDPOINTS_DE_ACAO if _serializa_direto(achados[nome])]
        assert soltos == [], f"serializam sem reler depois do commit: {soltos}"

    def test_todos_os_endpoints_de_acao_chamam_summary_after_commit(self):
        achados = _endpoints_que_devolvem_listing_summary()
        faltando = [nome for nome in ENDPOINTS_DE_ACAO if not _chama(achados[nome], "summary_after_commit")]
        assert faltando == [], faltando


def _ids_em_ordem(rows):
    return [r.id for r in sorted(rows, key=lambda r: r.sort_order) if r.sort_order < 90]


@_precisa_db
class TestSerializarDepoisDoCommit:
    @pytest.mark.asyncio
    async def test_reproducao_carregar_alterar_commit_serializar_nao_estoura(self):
        """O ciclo minimo do bug. No codigo antigo: `ValidationError` com
        `MissingGreenlet` em `approved_image_count`."""
        from sqlalchemy import select

        from app.models.listing import Listing
        from app.schemas.listing import ListingSummary

        engine, sm = await _preparar_banco()
        try:
            listing_id, _, _ = await _semear(sm, _linhas_padrao("cover_ai"))
            async with sm() as s:
                listing = (await s.execute(select(Listing).where(Listing.id == listing_id))).scalar_one()
                assert listing.approved_image_count == 0
                listing.error_message = "alterado nesta requisicao"
                await s.commit()
                resumo = ListingSummary.model_validate(listing)  # nao pode estourar
            assert resumo.approved_image_count == 0
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_approve_images_resposta_traz_a_contagem_nova(self):
        """A familia em que o numero muda DENTRO da requisicao: aprova 5 e a
        resposta tem de dizer 5 (com (a) sozinha diria 0)."""
        from sqlalchemy import select

        from app.models.listing_image import ListingImage
        from app.services.listing_service import ListingService

        engine, sm = await _preparar_banco()
        try:
            listing_id, seller_id, user_id = await _semear(sm, _linhas_padrao("cover_ai"))
            async with sm() as s:
                svc = ListingService(s, seller_id)
                listing = await svc.get_or_404(listing_id, seller_id)
                assert listing.approved_image_count == 0
                rows = (await s.execute(select(ListingImage).where(ListingImage.listing_id == listing_id))).scalars().all()
                with patch("app.workers.tasks.ai_tasks.generate_description") as task:
                    task.delay = MagicMock()
                    await svc.approve_images(listing, _ids_em_ordem(rows), 42, user_id=user_id)
                resumo = await svc.summary_after_commit(listing)
            assert resumo.status == "generating_description"
            assert resumo.approved_image_count == 5
            assert str(resumo.id) == str(listing_id)
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_familia_transicao_de_status_sai_com_status_novo_e_contagem_certa(self):
        """start/retry/select/attributes/generate_images/resume_*/publish so
        mudam o status; a contagem nao muda e tem de continuar certa."""
        from sqlalchemy import update

        from app.models.listing import Listing
        from app.services.listing_service import ListingService

        engine, sm = await _preparar_banco()
        try:
            listing_id, seller_id, _ = await _semear(sm, _linhas_padrao("cover_ai"))
            async with sm() as s:
                await s.execute(update(Listing).where(Listing.id == listing_id).values(status="draft"))
                await s.commit()
            async with sm() as s:
                svc = ListingService(s, seller_id)
                listing = await svc.get_or_404(listing_id, seller_id)
                with patch("app.workers.tasks.ai_tasks.generate_title") as task:
                    task.delay = MagicMock()
                    await svc.start_pipeline(listing)
                resumo = await svc.summary_after_commit(listing)
            assert resumo.status == "generating_title"
            assert resumo.approved_image_count == 0
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_promote_cover_resposta_traz_a_contagem_nova(self):
        """Familia das promocoes: promover a candidata aprova uma linha a mais
        (4 → 5) e a resposta tem de refletir."""
        from sqlalchemy import select, update

        from app.models.listing_image import ListingImage
        from app.services.cover_variant_service import promote_cover
        from app.services.listing_service import ListingService

        engine, sm = await _preparar_banco()
        try:
            listing_id, seller_id, _ = await _semear(sm, [
                ("presentation_ai", 1, "p1", "uploaded"),
                ("benefits_ai", 2, "p2", "uploaded"),
                ("detail_ai", 3, "p3", "uploaded"),
                ("specs_ai", 4, "p4", "uploaded"),
                ("cover_ai", 90, "c90", "uploaded"),
            ])
            async with sm() as s:
                await s.execute(update(ListingImage).where(
                    ListingImage.listing_id == listing_id, ListingImage.sort_order < 90
                ).values(approved=True))
                await s.commit()
            async with sm() as s:
                svc = ListingService(s, seller_id)
                listing = await svc.get_or_404(listing_id, seller_id)
                assert listing.approved_image_count == 4
                candidata = (await s.execute(select(ListingImage).where(
                    ListingImage.listing_id == listing_id, ListingImage.sort_order == 90
                ))).scalar_one()
                await promote_cover(s, listing, candidata.id)
                resumo = await svc.summary_after_commit(listing)
            assert resumo.approved_image_count == 5
        finally:
            await engine.dispose()
