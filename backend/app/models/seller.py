from datetime import datetime
from uuid import uuid4
from sqlalchemy import BigInteger, Boolean, DateTime, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base, TimestampMixin


class Seller(Base, TimestampMixin):
    __tablename__ = "sellers"

    id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    ml_user_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    ml_nickname: Mapped[str] = mapped_column(String(100), nullable=False)
    ml_site_id: Mapped[str] = mapped_column(String(5), nullable=False, default="MLB")
    # Anulaveis desde b8e2d4f6a1c3: desconectar a conta apaga SO os tokens e
    # mantem a linha (historico de anuncios, produtos e imagens). NULL = sem
    # token; quem le tem que passar por `get_valid_access_token`, que recusa
    # seller inativo ou sem token com erro claro em vez de estourar no Fernet.
    access_token_enc: Mapped[str | None] = mapped_column(String, nullable=True)
    refresh_token_enc: Mapped[str | None] = mapped_column(String, nullable=True)
    token_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    user_accesses: Mapped[list["UserSellerAccess"]] = relationship(
        "UserSellerAccess", back_populates="seller", cascade="all, delete-orphan"
    )
    listings: Mapped[list["Listing"]] = relationship("Listing", back_populates="seller")
    products: Mapped[list["Product"]] = relationship("Product", back_populates="seller")
