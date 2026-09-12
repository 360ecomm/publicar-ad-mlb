from datetime import datetime
from typing import Optional
from uuid import uuid4
from sqlalchemy import Boolean, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base


class ListingAttribute(Base):
    __tablename__ = "listing_attributes"
    __table_args__ = (UniqueConstraint("listing_id", "attribute_id", name="uq_listing_attribute"),)

    id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    listing_id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("listings.id"), nullable=False)
    attribute_id: Mapped[str] = mapped_column(String(100), nullable=False)
    attribute_name: Mapped[str] = mapped_column(String(200), nullable=False)
    value_id: Mapped[Optional[str]] = mapped_column(String(200))
    value_name: Mapped[Optional[str]] = mapped_column(String(500))
    attribute_type: Mapped[str] = mapped_column(String(30), nullable=False)
    is_required: Mapped[bool] = mapped_column(Boolean, nullable=False)
    source: Mapped[str] = mapped_column(String(10), nullable=False)  # 'ai' ou 'seller'
    allowed_values: Mapped[Optional[list]] = mapped_column(JSONB)
    # Dicionario de tags INTEIRO, como o ML devolve (`hidden`, `read_only`,
    # `fixed`, `required`, `multivalued`...). Um campo por tag exigiria
    # migracao a cada tag nova; o JSONB acompanha sozinho (padrao de
    # `allowed_values`). NULL = linha anterior a coluna, nada preenchido.
    tags: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    @property
    def is_editable(self) -> bool:
        """O operador deve ver/editar este atributo? `False` so quando as tags
        trazem `hidden` ou `read_only` verdadeiros (67 dos 80 de MLB7863).
        Unica definicao em Python — o frontend consome o booleano, nunca
        reimplementa (mesmo principio de `ListingImage.is_candidate`).
        Sem tags gravadas (linha antiga, NULL) e' editavel: na duvida, mostrar.
        `fixed` NAO entra na regra por enquanto (decisao pendente)."""
        tags = self.tags or {}
        return not (bool(tags.get("hidden")) or bool(tags.get("read_only")))

    listing: Mapped["Listing"] = relationship("Listing", back_populates="attributes")
