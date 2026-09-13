"""Operacoes sobre a conta do Mercado Livre (Seller) fora do OAuth."""
from datetime import datetime, timezone
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.seller import Seller
from app.models.user_seller_access import UserSellerAccess


class SellerService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def disconnect(self, seller_id: UUID, user_id: UUID) -> tuple[Seller, datetime]:
        """Apaga SO os tokens e marca a conta como desconectada.

        A linha do seller e tudo que aponta pra ela (listings, products,
        listing_images, listing_review_events, user_seller_access) ficam:
        decisao de dominio, o historico e' do negocio, o token e' do ML.
        Efeitos: `get_active_seller` passa a recusar a conta (403) — os
        anuncios param —, `get_valid_access_token` recusa nos workers, e o
        callback do OAuth (`handle_callback`) devolve `is_active = True` com
        token novo quando o operador reconectar.

        Idempotente: desconectar uma conta ja desconectada devolve o mesmo
        estado, sem erro. Devolve tambem o `granted_at` do acesso, que o
        `SellerOut` exige.
        """
        result = await self.db.execute(
            select(Seller, UserSellerAccess.granted_at)
            .join(UserSellerAccess, UserSellerAccess.seller_id == Seller.id)
            .where(UserSellerAccess.user_id == user_id, Seller.id == seller_id)
        )
        row = result.one_or_none()
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Conta não encontrada ou sem acesso.",
            )
        seller, granted_at = row
        seller.access_token_enc = None
        seller.refresh_token_enc = None
        seller.token_expires_at = datetime.now(timezone.utc)
        seller.is_active = False
        await self.db.commit()
        await self.db.refresh(seller)
        return seller, granted_at
