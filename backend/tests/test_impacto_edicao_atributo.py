"""Deteccao de posicao de imagem desatualizada depois de uma edicao de
atributo. Determinístico, sem banco e sem chamada paga (sempre roda)."""
from types import SimpleNamespace


def _attr(attribute_id, value_name, attribute_name=None):
    return SimpleNamespace(
        attribute_id=attribute_id,
        attribute_name=attribute_name or attribute_id.title(),
        value_name=value_name,
    )


# A ficha ordena BRAND, MODEL e depois alfabetico por `attribute_id`, e corta
# em MAX_BULLETS = 3. Com estes atributos os bullets sao BRAND, MODEL, FLAVOR;
# UNIT_VOLUME e PACKAGE_TYPE ficam FORA — e e' isso que permite testar
# "mudou a vitrine sem mexer na ficha" sem esbarrar no volume (que marcaria a
# posicao 1 junto).
def _base():
    return [
        _attr("BRAND", "Wepink", "Marca"),            # 0 — bullet 1
        _attr("MODEL", "Fatal Black", "Modelo"),      # 1 — bullet 2
        _attr("UNIT_VOLUME", "200 ml", "Volume"),     # 2 — fora dos bullets
        _attr("FLAVOR", "Chocolate", "Sabor"),        # 3 — bullet 3
        _attr("SELLER_SKU", "91", "SKU"),             # 4 — excluido da ficha
        _attr("SELLER_PACKAGE_WEIGHT", "120 g", "Peso"),  # 5 — excluido
        _attr("PACKAGE_TYPE", "Frasco", "Embalagem"), # 6 — fora dos bullets
    ]


TODAS = {0, 1, 2, 3, 4}


class TestSnapshot:
    def test_volume_sai_do_unit_volume(self):
        from app.services.attribute_impact import snapshot_attributes

        assert snapshot_attributes(_base()).volume == "200 ml"

    def test_sem_unit_volume_o_volume_e_none(self):
        from app.services.attribute_impact import snapshot_attributes

        attrs = [a for a in _base() if a.attribute_id != "UNIT_VOLUME"]
        assert snapshot_attributes(attrs).volume is None

    def test_vitrine_ignora_os_excluidos_da_ficha(self):
        """`SELLER_SKU` e `SELLER_PACKAGE_WEIGHT` estao em
        SPECS_EXCLUDED_ATTRIBUTE_IDS: nao descrevem o produto na vitrine."""
        from app.services.attribute_impact import snapshot_attributes

        ids = {aid for aid, _ in snapshot_attributes(_base()).vitrine}
        assert "SELLER_SKU" not in ids and "SELLER_PACKAGE_WEIGHT" not in ids
        assert ids == {"BRAND", "MODEL", "UNIT_VOLUME", "FLAVOR", "PACKAGE_TYPE"}

    def test_vitrine_ignora_atributo_sem_valor(self):
        from app.services.attribute_impact import snapshot_attributes

        attrs = _base() + [_attr("COLOR", None, "Cor")]
        ids = {aid for aid, _ in snapshot_attributes(attrs).vitrine}
        assert "COLOR" not in ids

    def test_snapshot_e_imune_a_mutacao_posterior(self):
        """A foto do estado ANTES nao pode mudar quando o service muta os
        objetos ORM no lugar — e' exatamente o que `edit_attributes` faz."""
        from app.services.attribute_impact import snapshot_attributes

        attrs = _base()
        antes = snapshot_attributes(attrs)
        attrs[3].value_name = "Lichia"
        assert antes.vitrine == snapshot_attributes(_base()).vitrine


class TestPosicoesDesatualizadas:
    def _stale(self, mutar, existentes=TODAS):
        from app.services.attribute_impact import snapshot_attributes, stale_positions

        antes_attrs = _base()
        antes = snapshot_attributes(antes_attrs)
        depois_attrs = _base()
        mutar(depois_attrs)
        return stale_positions(antes, snapshot_attributes(depois_attrs), existentes)

    def test_sem_mudanca_nenhuma_posicao(self):
        assert self._stale(lambda a: None) == []

    def test_ficha_muda_marca_a_posicao_4(self):
        """`MODEL` e' prioridade 2 na ficha: mudar o valor muda um bullet."""
        def mutar(attrs):
            attrs[1].value_name = "Fatal Red"

        assert 4 in self._stale(mutar)

    def test_unit_volume_muda_marca_a_posicao_1(self):
        def mutar(attrs):
            attrs[2].value_name = "100 ml"

        assert 1 in self._stale(mutar)

    def test_atributo_irrelevante_nao_marca_nada(self):
        """Peso da embalagem nao entra na ficha nem na copy. E' o teste que
        prova o filtro da posicao 2: sem ele, QUALQUER edicao marcaria a 2."""
        def mutar(attrs):
            attrs[5].value_name = "500 g"

        assert self._stale(mutar) == []

    def test_sku_interno_nao_marca_nada(self):
        def mutar(attrs):
            attrs[4].value_name = "999"

        assert self._stale(mutar) == []

    def test_vitrine_muda_sem_mexer_na_ficha_marca_so_a_2(self):
        """`PACKAGE_TYPE` fica FORA dos 3 bullets (o corte em MAX_BULLETS
        para em BRAND, MODEL, FLAVOR), mas a copy do LLM le todos os
        atributos de vitrine. Entao a posicao 2 e' marcada e as outras duas
        nao: a ficha nao mudou e o volume tambem nao."""
        def mutar(attrs):
            attrs[6].value_name = "Refil"

        assert self._stale(mutar) == [2]

    def test_so_posicoes_existentes_entram(self):
        """Anuncio sem imagem gerada nao recebe aviso de imagem."""
        def mutar(attrs):
            attrs[2].value_name = "100 ml"

        assert self._stale(mutar, existentes=set()) == []
        assert self._stale(mutar, existentes={4}) == []

    def test_resultado_e_ordenado_e_sem_repeticao(self):
        def mutar(attrs):
            attrs[1].value_name = "Fatal Red"
            attrs[2].value_name = "100 ml"

        stale = self._stale(mutar)
        assert stale == sorted(set(stale))


class TestFiltroCompartilhadoComAFicha:
    def test_build_specs_card_usa_o_mesmo_filtro(self):
        """Uma definicao so: se `specs_candidate_attributes` mudar, a ficha
        muda junto. Duas copias do filtro divergiriam em silencio."""
        from app.services.image_card_copy_service import (
            build_specs_card,
            specs_candidate_attributes,
        )

        attrs = _base()
        candidatos = specs_candidate_attributes(attrs)
        ficha = build_specs_card(attrs)
        assert ficha is not None
        nomes = {c.attribute_name for c in candidatos}
        for bullet in ficha.bullets:
            assert bullet.split(":")[0] in nomes
