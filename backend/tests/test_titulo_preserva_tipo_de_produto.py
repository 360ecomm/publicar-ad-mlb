"""O prompt de titulo em lote manda preservar a palavra de TIPO de produto.

Caso real (T38, lote de 2026-09-10): a descricao do ERP e' "Body Splash Fatal
Black For Her Desodorante Colônia 200ml - Wepink" e o titulo gerado saiu
"Body Splash Fatal Black For Her Desodorante 200ml Wepink" — sem "Colônia".
So com isso o `domain_discovery` trocou MLB6284 (Perfumes) por MLB44379
(Desodorantes), e o lote parou por um atributo de desodorante. Com "Colônia"
no titulo, a mesma consulta devolve MLB6284 em 1o lugar.

LIMITACAO, a mesma dos prompts de imagem: nao da' para testar unitariamente
se o modelo OBEDECE. O que se testa aqui e' que a instrucao esta' no prompt
e diz o que tem que dizer — preservar o que ESTA' na origem e nunca inventar
tipo que nao esteja (o T37, cuja origem nao tem "Perfume", continua sem
"Perfume": isso e' dado de catalogo, nao prompt). A obediencia real e'
aferida por amostragem repetida contra o `domain_discovery`, fora da suite.
"""
from app.services.ai.prompts import build_title_prompt


def _prompt_lote(descricao="Body Splash Fatal Black For Her Desodorante Colônia 200ml - Wepink"):
    return build_title_prompt(descricao, "Wepink", "new", ean="7908647604106", batch_mode=True)


class TestPromptPreservaTipoDeProduto:
    def test_instrucao_de_preservar_esta_no_prompt_de_lote(self):
        p = _prompt_lote()
        regras = p.split("PRODUTO:")[0]
        assert "tipo de produto" in regras.lower()
        assert "preserv" in regras.lower()
        # Nunca omitir por economia de caracteres: e' a causa concreta do T38.
        assert "caracter" in regras.lower() and "omit" in regras.lower()

    def test_instrucao_cita_os_tipos_da_vertical(self):
        regras = _prompt_lote().split("PRODUTO:")[0]
        for termo in ("Perfume", "Colônia", "Body Splash", "Desodorante"):
            assert termo in regras, termo

    def test_instrucao_proibe_inventar_tipo(self):
        regras = _prompt_lote().split("PRODUTO:")[0].lower()
        assert "invent" in regras
        assert "origem" in regras or "descrição do erp" in regras

    def test_modo_manual_nao_muda(self):
        """Escopo: so o lote. O manual tem selecao humana entre 3 opcoes."""
        manual = build_title_prompt("x", "y", "new", batch_mode=False).split("PRODUTO:")[0]
        assert "tipo de produto" not in manual.lower()
