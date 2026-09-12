from decimal import Decimal
from typing import Optional
from uuid import uuid4
from sqlalchemy import ForeignKey, Index, Integer, Numeric, String, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base, TimestampMixin


# Ordem do pipeline (mesma do `ListingStatus` do frontend). E' a lista que a
# barra de resumo da fila usa: cada status aparece SEMPRE, com zero quando
# nao ha anuncio, pra barra nao mudar de tamanho a cada atualizacao.
#
# Espelha `ListingStatus` em `frontend/src/types/listing.ts` — a coluna
# `status` nao tem CHECK constraint no banco, entao nao ha nada que force as
# duas listas a andarem juntas. Mudou aqui, muda la tambem, a mao.
LISTING_STATUSES: tuple[str, ...] = (
    "draft",
    "generating_title",
    "pending_title_approval",
    "predicting_category",
    "pending_seller_attributes",
    "pending_description",
    "generating_images",
    "pending_raw_photos",
    "pending_ai_engine",
    "pending_image_approval",
    "generating_description",
    "ready_to_publish",
    "publishing",
    "published",
    "published_paused",
    "failed",
)


class Listing(Base, TimestampMixin):
    __tablename__ = "listings"
    __table_args__ = (
        # Fila de trabalho: filtro por seller + status, ordenado por created_at
        # desc. Ver migration d4e8b2a6f9c1 para o porque de UM composto.
        Index("ix_listings_seller_status_created", "seller_id", "status", text("created_at DESC")),
    )

    id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    seller_id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("sellers.id"), nullable=False)
    created_by: Mapped[UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)

    sku_external_id: Mapped[Optional[str]] = mapped_column(String(100))
    sku_description: Mapped[str] = mapped_column(Text, nullable=False)
    sku_brand: Mapped[str] = mapped_column(String(200), nullable=False)
    sku_model: Mapped[Optional[str]] = mapped_column(String(200))
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    stock_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    condition: Mapped[str] = mapped_column(String(10), nullable=False)
    listing_type_id: Mapped[str] = mapped_column(String(20), nullable=False, default="gold_special")

    package_weight_kg: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 3))
    package_length_cm: Mapped[Optional[int]] = mapped_column(Integer)
    package_width_cm: Mapped[Optional[int]] = mapped_column(Integer)
    package_height_cm: Mapped[Optional[int]] = mapped_column(Integer)

    product_id: Mapped[Optional[UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("products.id"), nullable=True, index=True
    )

    status: Mapped[str] = mapped_column(String(50), nullable=False, default="draft")
    failed_step: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    created_via: Mapped[str] = mapped_column(String(10), nullable=False, default="manual")
    ml_category_id: Mapped[Optional[str]] = mapped_column(String(20))
    mlb_id: Mapped[Optional[str]] = mapped_column(String(20), unique=True)
    selected_title: Mapped[Optional[str]] = mapped_column(Text)
    error_message: Mapped[Optional[str]] = mapped_column(Text)

    product: Mapped[Optional["Product"]] = relationship("Product", back_populates="listings")
    seller: Mapped["Seller"] = relationship("Seller", back_populates="listings")
    created_by_user: Mapped["User"] = relationship("User", foreign_keys=[created_by])
    jobs: Mapped[list["ListingJob"]] = relationship(
        "ListingJob", back_populates="listing", cascade="all, delete-orphan"
    )
    titles: Mapped[list["ListingTitle"]] = relationship(
        "ListingTitle", back_populates="listing", cascade="all, delete-orphan"
    )
    attributes: Mapped[list["ListingAttribute"]] = relationship(
        "ListingAttribute", back_populates="listing", cascade="all, delete-orphan"
    )
    images: Mapped[list["ListingImage"]] = relationship(
        "ListingImage", back_populates="listing", cascade="all, delete-orphan"
    )
    description: Mapped[Optional["ListingDescription"]] = relationship(
        "ListingDescription", back_populates="listing", uselist=False, cascade="all, delete-orphan"
    )
    review_events: Mapped[list["ListingReviewEvent"]] = relationship(
        "ListingReviewEvent", back_populates="listing", cascade="all, delete-orphan"
    )
