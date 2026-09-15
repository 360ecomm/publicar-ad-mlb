"""Titulo nunca termina com palavra ou unidade partida.

Vitima real em producao: `MLB7638983316` (SKU 31, MLB6284) foi ao ar com
"...Wepink 200m" — 60 caracteres exatos, o "l" de "200ml" comido por um
`[:60]` cego em `gemini.py`. O modelo tinha escrito 61 caracteres; o codigo
transformou um estouro de UM caractere em titulo corrompido, sem log e sem
erro.

Sem banco (sempre roda).
"""
import logging
from unittest.mock import AsyncMock, patch

import pytest

# O titulo que o modelo escreveu para o SKU 31: 61 chars, um a mais que o alvo.
SKU31_61 = "Body Splash Desodorante Colônia Liberté Exclusif Wepink 200ml"
# Um titulo valido para o mesmo produto, preservando os tres termos de tipo
# (Body Splash, Desodorante, Colônia), a marca e o volume.
SKU31_52 = "Body Splash Desodorante Colônia Liberté Wepink 200ml"


def _titulos(*textos):
    return [{"title": t, "score": None, "rationale": "batch_auto"} for t in textos]


def _sem_palavra_partida(resultado: str, origem: str) -> bool:
    """A ultima palavra do resultado tem de ser uma palavra INTEIRA da origem."""
    if not resultado:
        return False
    return resultado.split()[-1] in origem.split()


class TestCortarNaUltimaPalavra:
    def test_titulo_que_cabe_volta_igual(self):
        from app.services.ai.title_guard import cortar_na_ultima_palavra

        assert cortar_na_ultima_palavra(SKU31_52) == SKU31_52

    def test_corta_na_fronteira_de_palavra_nunca_no_meio(self):
        from app.services.ai.title_guard import cortar_na_ultima_palavra

        r = cortar_na_ultima_palavra(SKU31_61)
        assert len(r) <= 60
        assert _sem_palavra_partida(r, SKU31_61)
        # O bug original: terminava em "200m".
        assert not r.endswith("200m")

    def test_nunca_termina_com_espaco(self):
        from app.services.ai.title_guard import cortar_na_ultima_palavra

        # O corte em 60 cai exatamente sobre um espaco.
        origem = "a" * 59 + " bbbb"
        assert cortar_na_ultima_palavra(origem) == "a" * 59

    def test_nunca_termina_com_separador_solto(self):
        from app.services.ai.title_guard import cortar_na_ultima_palavra

        origem = "Body Splash Colônia Wepink 200ml - Liberté Exclusif Edicao Rara"
        r = cortar_na_ultima_palavra(origem)
        assert not r.endswith("-")
        assert r == r.rstrip()

    def test_resultado_e_sempre_prefixo_da_origem(self):
        """A garantia de que NADA e' inventado: o corte so remove."""
        from app.services.ai.title_guard import cortar_na_ultima_palavra

        for origem in (SKU31_61, SKU31_52, "x" * 80, "Perfume " * 12):
            assert origem.strip().startswith(cortar_na_ultima_palavra(origem).rstrip(" -"))

    def test_palavra_unica_gigante_avisa_em_log(self, caplog):
        """Unico caso em que sobra fragmento: nao ha fronteira onde cortar.
        Entrada patologica — falha alto em vez de derrubar o anuncio."""
        from app.services.ai.title_guard import cortar_na_ultima_palavra

        with caplog.at_level(logging.WARNING):
            r = cortar_na_ultima_palavra("A" * 80)
        assert len(r) == 60
        assert "sem_fronteira" in caplog.text


class TestAplicarLimite:
    @pytest.mark.asyncio
    async def test_titulo_dentro_do_limite_nao_faz_chamada_extra(self):
        from app.services.ai.title_guard import aplicar_limite

        retentar = AsyncMock()
        r = await aplicar_limite(_titulos(SKU31_52), retentar=retentar)
        assert r[0]["title"] == SKU31_52
        retentar.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_estouro_pede_de_novo_uma_vez_e_usa_a_resposta_boa(self):
        from app.services.ai.title_guard import aplicar_limite

        retentar = AsyncMock(return_value=_titulos(SKU31_52))
        r = await aplicar_limite(_titulos(SKU31_61), retentar=retentar)
        assert r[0]["title"] == SKU31_52       # inteiro, sem corte
        assert retentar.await_count == 1        # exatamente UMA chamada extra

    @pytest.mark.asyncio
    async def test_segunda_tentativa_tambem_estoura_entao_corta_e_loga(self, caplog):
        from app.services.ai.title_guard import aplicar_limite

        ainda_longo = "Body Splash Desodorante Colônia Liberté Exclusif Wepink 250ml"
        retentar = AsyncMock(return_value=_titulos(ainda_longo))
        with caplog.at_level(logging.WARNING):
            r = await aplicar_limite(_titulos(SKU31_61), retentar=retentar)
        assert len(r[0]["title"]) <= 60
        assert _sem_palavra_partida(r[0]["title"], ainda_longo)
        assert "titulo_cortado" in caplog.text
        assert "excedeu=1" in caplog.text

    @pytest.mark.asyncio
    async def test_sem_retentar_corta_direto(self):
        from app.services.ai.title_guard import aplicar_limite

        r = await aplicar_limite(_titulos(SKU31_61), retentar=None)
        assert len(r[0]["title"]) <= 60
        assert _sem_palavra_partida(r[0]["title"], SKU31_61)

    @pytest.mark.asyncio
    async def test_retentar_que_estoura_excecao_cai_no_corte_sem_propagar(self):
        from app.services.ai.title_guard import aplicar_limite

        retentar = AsyncMock(side_effect=RuntimeError("provedor fora"))
        r = await aplicar_limite(_titulos(SKU31_61), retentar=retentar)
        assert len(r[0]["title"]) <= 60
        assert _sem_palavra_partida(r[0]["title"], SKU31_61)

    @pytest.mark.asyncio
    async def test_modo_manual_todos_os_titulos_cabem_na_coluna(self):
        """`ListingTitle.title_text` e' String(60): passar disso estoura o
        INSERT com erro de banco e o anuncio vai para 'Com erro'."""
        from app.services.ai.title_guard import aplicar_limite

        longos = _titulos(SKU31_61, "P" * 75, SKU31_52)
        r = await aplicar_limite(longos, retentar=None)
        assert [len(t["title"]) <= 60 for t in r] == [True, True, True]

    @pytest.mark.asyncio
    async def test_nunca_acrescenta_texto_que_nao_veio_da_origem(self):
        from app.services.ai.title_guard import aplicar_limite

        r = await aplicar_limite(_titulos(SKU31_61), retentar=None)
        assert SKU31_61.startswith(r[0]["title"])


class TestGeminiBatch:
    def _resposta(self, titulo):
        import json

        return json.dumps({"title": titulo})

    @pytest.mark.asyncio
    async def test_titulo_de_61_faz_uma_segunda_chamada_e_grava_inteiro(self):
        from app.services.ai.gemini import GeminiProvider

        p = GeminiProvider()
        with patch.object(
            GeminiProvider, "_call", new=AsyncMock(
                side_effect=[self._resposta(SKU31_61), self._resposta(SKU31_52)]
            )
        ) as call:
            r = await p.generate_titles("Body Splash", "Wepink", "new", batch_mode=True)
        assert r[0]["title"] == SKU31_52
        assert call.await_count == 2

    @pytest.mark.asyncio
    async def test_titulo_dentro_do_limite_faz_uma_chamada_so(self):
        from app.services.ai.gemini import GeminiProvider

        p = GeminiProvider()
        with patch.object(
            GeminiProvider, "_call", new=AsyncMock(side_effect=[self._resposta(SKU31_52)])
        ) as call:
            r = await p.generate_titles("Body Splash", "Wepink", "new", batch_mode=True)
        assert r[0]["title"] == SKU31_52
        assert call.await_count == 1

    @pytest.mark.asyncio
    async def test_caso_real_do_sku_31_nunca_produz_fragmento(self, caplog):
        """A entrada que gerou os 61 caracteres, com o modelo teimando duas
        vezes: o que sai NAO pode terminar em fragmento."""
        from app.services.ai.gemini import GeminiProvider

        p = GeminiProvider()
        with patch.object(
            GeminiProvider, "_call", new=AsyncMock(
                side_effect=[self._resposta(SKU31_61), self._resposta(SKU31_61)]
            )
        ):
            with caplog.at_level(logging.WARNING):
                r = await p.generate_titles(
                    "Body Splash Desodorante Colônia Wepink 200 ml - Liberté Exclusif",
                    "Wepink", "new", batch_mode=True,
                )
        saida = r[0]["title"]
        assert len(saida) <= 60
        assert not saida.endswith("200m")
        assert _sem_palavra_partida(saida, SKU31_61)


class TestFatiaCegaRemovida:
    @pytest.mark.parametrize("modulo", ["gemini", "claude"])
    def test_nenhum_provedor_fatia_em_60(self, modulo):
        """`[:60]` transformava estouro de 1 caractere em titulo corrompido.
        Fora dos dois — o Claude esta sem uso, mas a armadilha nao fica.

        Le a ARVORE, nao o texto: os dois arquivos citam `[:60]` em comentario
        para explicar o que foi removido, e uma busca por string acusaria a
        propria documentacao da correcao.
        """
        import ast
        import inspect

        mod = __import__(f"app.services.ai.{modulo}", fromlist=["x"])
        arvore = ast.parse(inspect.getsource(mod))
        fatias = [
            no for no in ast.walk(arvore)
            if isinstance(no, ast.Slice)
            and isinstance(no.upper, ast.Constant)
            and no.upper.value == 60
        ]
        assert fatias == []

    def test_prompt_proibe_partir_palavra(self):
        from app.services.ai.prompts import build_title_prompt

        prompt = build_title_prompt("Body Splash", "Wepink", "new", batch_mode=True)
        texto = prompt.lower()
        assert "partida" in texto or "partir" in texto
        assert "remova" in texto or "remover" in texto
