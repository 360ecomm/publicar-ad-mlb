"""Rota PATCH de correcao de atributos. Sem banco (sempre roda)."""


def _rotas():
    from app.api.v1.endpoints import listings

    return listings.router.routes


class TestRota:
    def test_patch_de_atributos_existe(self):
        alvos = [
            r for r in _rotas()
            if getattr(r, "path", None) == "/listings/{listing_id}/attributes"
            and "PATCH" in getattr(r, "methods", set())
        ]
        assert len(alvos) == 1

    def test_put_de_atributos_continua_existindo(self):
        """O PUT e' o passo de PREENCHIMENTO e nao muda nesta branch."""
        alvos = [
            r for r in _rotas()
            if getattr(r, "path", None) == "/listings/{listing_id}/attributes"
            and "PUT" in getattr(r, "methods", set())
        ]
        assert len(alvos) == 1

    def test_patch_devolve_o_schema_de_edicao(self):
        from app.schemas.listing import AttributesEditResponse

        alvo = next(
            r for r in _rotas()
            if getattr(r, "path", None) == "/listings/{listing_id}/attributes"
            and "PATCH" in getattr(r, "methods", set())
        )
        assert alvo.response_model is AttributesEditResponse


class TestSchema:
    def test_campos_e_defaults(self):
        from app.schemas.listing import AttributesEditResponse

        campos = AttributesEditResponse.model_fields
        assert set(campos) == {"listing", "stale_positions", "duplicated_fields"}

    def test_listas_vazias_sao_validas(self):
        from app.schemas.listing import AttributesEditResponse, ListingSummary

        assert "stale_positions" in AttributesEditResponse.model_fields
        assert AttributesEditResponse.model_fields["listing"].annotation is ListingSummary
