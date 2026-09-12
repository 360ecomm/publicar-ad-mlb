from datetime import datetime
from decimal import Decimal
from typing import Optional
from uuid import uuid4
from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Numeric, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base


class ListingTitle(Base):
    __tablename__ = "listing_titles"
    # Migration f7b3e9c1d2a5: lida por listing_id em 5 pontos (detalhe,
    # select_title, aprovacao em massa, retry com DELETE). DDL identico ao da
    # migration.
    __table_args__ = (Index("ix_listing_titles_listing_id", "listing_id"),)

    id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    listing_id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("listings.id"), nullable=False)
    title_text: Mapped[str] = mapped_column(String(60), nullable=False)
    ai_score: Mapped[Optional[Decimal]] = mapped_column(Numeric(4, 2))
    selected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    listing: Mapped["Listing"] = relationship("Listing", back_populates="titles")
