from datetime import datetime
from typing import Optional
from uuid import uuid4
from sqlalchemy import Boolean, DateTime, ForeignKey, Index, LargeBinary, SmallInteger, String, Text, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base

# --------------------------------------------------------------------------
# Vocabulario da coluna `kind`. Mora aqui, no model, porque mais de um modulo
# (services de variante, worker de imagens, listing_service) precisa decidir
# comportamento a partir do MESMO conjunto de valores — duplicar as strings em
# cada call site foi exatamente o que permitiu que os dois pontos de aprovacao
# em massa esquecessem os candidatos.
# --------------------------------------------------------------------------
COVER_DETERMINISTIC_KIND = "cover_deterministic"
COVER_AI_KIND = "cover_ai"
SPECS_AI_KIND = "specs_ai"

# A posicao de capa na galeria. Mora aqui junto do vocabulario de `kind`
# porque a regra "quem pode ocupar esta posicao" e do dominio, nao de um
# servico: `listing_service.approve_images` (que numera) e
# `cover_variant_service.promote_cover` (que troca) precisam concordar.
COVER_SORT_ORDER = 0

# Unicos kinds que podem ocupar `COVER_SORT_ORDER`. O conjunto e usado em dois
# lugares que TEM de concordar:
#
#   - `promote_cover` valida o alvo contra ele E restringe a ele o
#     rebaixamento, para nunca despublicar uma foto que nao seja capa;
#   - `approve_images` reserva a posicao 0 a estes kinds ao renumerar.
#
# A segunda parte e o que torna a primeira suficiente. Enquanto `approve_images`
# podia por uma `individual` em 0, o rebaixamento restrito deixava DUAS linhas
# empatadas em 0 depois de uma promocao, e `publish_service` ordena por
# `sort_order` sem desempate — a capa publicada virava sorteio. Afrouxar
# qualquer um dos dois lados reabre isso.
PROMOTABLE_COVER_KINDS = frozenset({COVER_DETERMINISTIC_KIND, COVER_AI_KIND})

CARD_SPECS_KIND = "card_specs"

# Kinds que disputam o slot de ficha tecnica na galeria — o Pillow do pipeline
# e o candidato de IA sob demanda. Usado por `promote_specs` para validar o
# alvo E para restringir o rebaixamento, exatamente como
# `PROMOTABLE_COVER_KINDS` faz na capa.
#
# DIFERENCA ESTRUTURAL EM RELACAO A CAPA, e o motivo de nao existir um
# `SPECS_SORT_ORDER` aqui ao lado de `COVER_SORT_ORDER`: a capa tem posicao
# fixa (0) porque "primeira imagem" e um conceito do dominio. A ficha tecnica
# nao tem. Os cards recebem `start_sort_order + saved` em `image_tasks.py`, ou
# seja, a posicao depende de quantas fotos individuais aquele anuncio gerou —
# 7 no SKU 37, 5 num anuncio com 2 individuais. Cravar um numero aqui mudaria
# a numeracao da galeria de todo anuncio, inclusive os ja publicados, entao
# `promote_specs` LE o slot da ficha que ja esta na galeria em vez de impor um.
PROMOTABLE_SPECS_KINDS = frozenset({CARD_SPECS_KIND, SPECS_AI_KIND})

# Fronteira entre a galeria publicavel e a area de candidatos (90/91). Serve
# para responder "qual e a ultima posicao da galeria" sem confundir candidato
# com imagem publicada.
#
# ESTA E' A DEFINICAO DE CANDIDATA: candidata e' quem tem
# `sort_order >= CANDIDATE_SORT_ORDER_FLOOR`. O `kind` NAO distingue —
# `cover_ai` e `specs_ai` sao gerados por IA SOB DEMANDA (Frentes A e B) em
# sort_order 90/91, mas sao TAMBEM os kinds das posicoes oficiais 0 (capa) e 4
# (ficha) do esquema de 5 posicoes. Um filtro por kind (`kind NOT IN
# (cover_ai, specs_ai)`) deixaria a capa e a ficha oficiais de fora de
# qualquer aprovacao em massa — foi exatamente o bug que este comentario
# substitui.
#
# Candidatas nascem `approved=False` e so viram capa/ficha por acao humana
# explicita (`promote_cover`/`promote_specs`). Toda aprovacao em massa (worker
# batch, `bulk_approve_images`) TEM de exclui-las pela POSICAO: aprovado +
# `ml_picture_id` e o filtro que monta o payload de fotos da publicacao,
# entao uma candidata aprovada por engano vai ao ar no anuncio real.
#
# Em Python, consultar por `ListingImage.is_candidate` (propriedade abaixo),
# nunca reescrever a comparacao. Em SQL (UPDATE em massa, promocoes) a
# constante continua sendo a forma de expressar o mesmo predicado.
#
# `bulk_approve_images` usa o MESMO criterio `aprovado + ml_picture_id` na
# outra ponta: o UPDATE so aprova quem, alem de estar na galeria
# (`sort_order < CANDIDATE_SORT_ORDER_FLOOR`), tem `ml_picture_id` preenchido.
# Uma posicao que reprovou no QA (worker grava `ml_picture_id=None`) fica de
# fora e continua `approved=False` — e' o que evita aprovar, no mesmo slot,
# tanto a linha reprovada quanto o fallback que a substituiu.
CANDIDATE_SORT_ORDER_FLOOR = 90


class ListingImage(Base):
    __tablename__ = "listing_images"
    # Indices unicos PARCIAIS que fecham a corrida de promote_cover /
    # promote_specs (migration 3d8f1b2c9e47). `with_for_update()` so
    # serializa promocoes que disputem as MESMAS linhas; duas promocoes de
    # alvos diferentes travavam linhas disjuntas e terminavam ambas aprovadas
    # no mesmo slot. Com o indice, a segunda falha no commit e o service
    # devolve 409.
    #
    # `approved` esta no predicado de proposito: o pipeline grava `cover_ai`
    # em sort_order 0 com approved=False, e uma regeracao pode deixar duas
    # linhas assim — o que nao pode existir e' mais de UMA APROVADA no slot.
    # As listas de kinds sao literais aqui (nao f-string sobre os frozensets)
    # para o DDL ser identico ao da migration, byte a byte.
    __table_args__ = (
        Index(
            "uq_listing_images_cover_slot",
            "listing_id",
            unique=True,
            postgresql_where=text(
                "approved AND sort_order = 0 AND kind IN ('cover_deterministic', 'cover_ai')"
            ),
        ),
        Index(
            "uq_listing_images_specs_slot",
            "listing_id",
            unique=True,
            postgresql_where=text(
                "approved AND sort_order < 90 AND kind IN ('card_specs', 'specs_ai')"
            ),
        ),
    )

    id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    listing_id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("listings.id"), nullable=False)
    freepik_job_id: Mapped[Optional[str]] = mapped_column(String(200))
    ml_picture_id: Mapped[Optional[str]] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="generating")
    approved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    sort_order: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    kind: Mapped[str] = mapped_column(String(20), nullable=False, default="individual")
    source_sku: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    validation_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Referencia (chave) dos bytes exatos desta imagem no bucket R2 de ativos
    # (`r2_asset_service`), gravada na GERACAO — aprovada ou reprovada no QA.
    # Substitui o blob `image_bytes` que vivia aqui ate 2026-09-10 (migration
    # 7b2d9f4e1c58): variantes por IA partem do MESMO arquivo publicado, nao
    # de uma re-derivacao — o seller PODE trocar a foto bruta depois
    # (aconteceu com 37-2.jpg). NULL quando o R2 nao estava configurado ou
    # falhou na hora, ou em linhas antigas nao migradas.
    asset_key: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    @property
    def is_candidate(self) -> bool:
        """Candidata das Frentes A/B: `sort_order >= CANDIDATE_SORT_ORDER_FLOOR`.
        Unica definicao em Python — o `kind` nao distingue (ver a constante)."""
        return self.sort_order >= CANDIDATE_SORT_ORDER_FLOOR

    listing: Mapped["Listing"] = relationship("Listing", back_populates="images")
