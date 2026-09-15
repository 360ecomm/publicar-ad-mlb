"""Vocabulario da edicao de atributos: quais status aceitam correcao e qual
a acao de auditoria. Sem banco (sempre roda)."""


class TestStatusEditaveis:
    def test_todos_existem_em_listing_statuses(self):
        from app.models.listing import EDITABLE_ATTRIBUTE_STATUSES, LISTING_STATUSES

        assert EDITABLE_ATTRIBUTE_STATUSES <= set(LISTING_STATUSES)

    def test_os_oito_perigosos_ficam_de_fora(self):
        """Nenhum destes pode entrar sem uma decisao nova: em cinco deles um
        worker esta lendo os atributos neste exato momento, e em
        `predicting_category` a edicao seria APAGADA em silencio pelo
        `_save_attributes`."""
        from app.models.listing import EDITABLE_ATTRIBUTE_STATUSES

        proibidos = {
            "generating_title",
            "predicting_category",
            "generating_images",
            "generating_description",
            "publishing",
            "published",
            "published_under_review",
            "published_paused",
        }
        assert EDITABLE_ATTRIBUTE_STATUSES & proibidos == set()

    def test_cobre_os_nove_status_de_espera(self):
        from app.models.listing import EDITABLE_ATTRIBUTE_STATUSES

        assert EDITABLE_ATTRIBUTE_STATUSES == {
            "draft",
            "pending_title_approval",
            "pending_seller_attributes",
            "pending_description",
            "pending_raw_photos",
            "pending_ai_engine",
            "pending_image_approval",
            "ready_to_publish",
            "failed",
        }

    def test_particao_exata_de_listing_statuses(self):
        """Editaveis + proibidos = TODOS os status. Status novo obriga uma
        decisao explicita: o teste quebra ate alguem classifica-lo."""
        from app.models.listing import EDITABLE_ATTRIBUTE_STATUSES, LISTING_STATUSES

        proibidos = {
            "generating_title", "predicting_category", "generating_images",
            "generating_description", "publishing", "published",
            "published_under_review", "published_paused",
        }
        assert EDITABLE_ATTRIBUTE_STATUSES | proibidos == set(LISTING_STATUSES)


class TestStatusDeRegeneracao:
    def test_regeneracao_aceita_os_dois_status(self):
        from app.models.listing import REGENERABLE_POSITION_STATUSES

        assert REGENERABLE_POSITION_STATUSES == {
            "pending_image_approval",
            "ready_to_publish",
        }

    def test_ambos_sao_editaveis(self):
        """Quem pode regenerar imagem tem de poder corrigir o atributo que
        gerou o texto dela — o contrario e' um beco sem saida."""
        from app.models.listing import (
            EDITABLE_ATTRIBUTE_STATUSES,
            REGENERABLE_POSITION_STATUSES,
        )

        assert REGENERABLE_POSITION_STATUSES <= EDITABLE_ATTRIBUTE_STATUSES


class TestAcaoDeAuditoria:
    def test_valor_da_acao(self):
        from app.models.listing_review_event import REVIEW_ACTION_ATTRIBUTES_EDITED

        assert REVIEW_ACTION_ATTRIBUTES_EDITED == "attributes_edited"

    def test_nao_colide_com_a_aprovacao_de_imagens(self):
        from app.models.listing_review_event import (
            REVIEW_ACTION_ATTRIBUTES_EDITED,
            REVIEW_ACTION_IMAGES_APPROVED,
        )

        assert REVIEW_ACTION_ATTRIBUTES_EDITED != REVIEW_ACTION_IMAGES_APPROVED

    def test_cabe_na_coluna(self):
        """`action` e' String(40)."""
        from app.models.listing_review_event import REVIEW_ACTION_ATTRIBUTES_EDITED

        assert len(REVIEW_ACTION_ATTRIBUTES_EDITED) <= 40
