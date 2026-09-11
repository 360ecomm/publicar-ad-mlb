from datetime import datetime
from typing import Optional
from uuid import uuid4
from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base

# --------------------------------------------------------------------------
# Vocabulario de `action`/`mode`. Mora aqui, no model, pelo mesmo motivo do
# vocabulario de `kind` em `listing_image.py`: mais de um call site
# (`approve_images`, `bulk_approve_images`) precisa concordar com o MESMO
# conjunto de valores.
# --------------------------------------------------------------------------
REVIEW_ACTION_IMAGES_APPROVED = "images_approved"
REVIEW_MODE_INDIVIDUAL = "individual"
REVIEW_MODE_BULK = "bulk"


class ListingReviewEvent(Base):
    """Um evento por aprovacao humana de imagens de um listing — quem, modo,
    quantas imagens, quando. Gravado na MESMA transacao da aprovacao
    (`approve_images`/`bulk_approve_images`), nunca depois: aprovacao sem
    evento ou evento sem aprovacao tem que ser impossivel.

    Sem `updated_at` e sem `TimestampMixin` de proposito: um evento e'
    imutavel, nao ha "atualizar" um registro de auditoria.

    `approved_count` conta coisas diferentes conforme o `mode`, e os dois
    numeros nao sao comparaveis entre si. Em `individual`, e' a contagem de
    todo id que o operador mandou e que pertence ao listing (o loop dentro de
    `approve_images`) — pode incluir uma linha reprovada em QA ou uma
    candidata que o operador escolheu de proposito. Em `bulk`, e' o
    `rowcount` do UPDATE em massa, restrito a
    `sort_order < CANDIDATE_SORT_ORDER_FLOOR AND ml_picture_id IS NOT NULL`.
    Por isso `approved_count == 0` e' legitimo em `bulk`: acontece quando
    toda posicao falhou QA e nenhuma linha bateu o filtro do UPDATE.
    """

    __tablename__ = "listing_review_events"

    id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    # `ondelete="CASCADE"` no banco + `cascade="all, delete-orphan"` no
    # relationship de `Listing`: o evento apaga junto com o listing
    # (`DELETE /listings/{id}`, so draft/failed), como os outros filhos.
    # `user_id` NAO cascateia: apagar um usuario nao pode sumir com auditoria.
    listing_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("listings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    action: Mapped[str] = mapped_column(String(40), nullable=False)
    mode: Mapped[str] = mapped_column(String(20), nullable=False)
    approved_count: Mapped[int] = mapped_column(Integer, nullable=False)
    # Individual grava o tempo recebido do operador (pode ser None, se ele
    # nao informou). Em massa grava sempre NULL — nunca estima nem reparte
    # tempo entre os anuncios do lote.
    review_seconds: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    listing: Mapped["Listing"] = relationship("Listing", back_populates="review_events")
