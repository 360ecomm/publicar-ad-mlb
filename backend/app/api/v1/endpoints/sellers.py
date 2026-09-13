from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from app.core.dependencies import get_db, get_current_user
from app.models.seller import Seller
from app.models.listing import Listing
from app.models.user_seller_access import UserSellerAccess
from app.schemas.seller import DashboardResponse, SellerDashboardEntry, SellerOut
from app.services.seller_service import SellerService

router = APIRouter(tags=["sellers"])


@router.get("/sellers", response_model=list[SellerOut])
async def list_sellers(
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Lista todas as contas ML que o usuário autenticado tem acesso."""
    result = await db.execute(
        select(Seller, UserSellerAccess.granted_at)
        .join(UserSellerAccess, UserSellerAccess.seller_id == Seller.id)
        .where(UserSellerAccess.user_id == current_user.id)
        .order_by(UserSellerAccess.granted_at.asc())
    )
    rows = result.all()
    sellers_out = []
    for seller, granted_at in rows:
        sellers_out.append(
            SellerOut(
                id=seller.id,
                ml_user_id=seller.ml_user_id,
                ml_nickname=seller.ml_nickname,
                ml_site_id=seller.ml_site_id,
                token_expires_at=seller.token_expires_at,
                is_active=seller.is_active,
                granted_at=granted_at,
            )
        )
    return sellers_out


@router.post("/sellers/{seller_id}/disconnect", response_model=SellerOut)
async def disconnect_seller(
    seller_id: UUID,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Desconecta a conta ML: apaga so os tokens, mantem o historico.

    Nao passa por `get_active_seller` de proposito: aquela dependencia
    recusa conta inativa, e desconectar tem que ser idempotente.
    """
    seller, granted_at = await SellerService(db).disconnect(seller_id, current_user.id)
    return SellerOut(
        id=seller.id,
        ml_user_id=seller.ml_user_id,
        ml_nickname=seller.ml_nickname,
        ml_site_id=seller.ml_site_id,
        token_expires_at=seller.token_expires_at,
        is_active=seller.is_active,
        granted_at=granted_at,
    )


@router.get("/dashboard", response_model=DashboardResponse)
async def get_dashboard(
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Dashboard global: status de listings agrupado por conta ML."""
    # Busca todos os sellers do usuário
    sellers_result = await db.execute(
        select(Seller)
        .join(UserSellerAccess, UserSellerAccess.seller_id == Seller.id)
        # Inclui contas desconectadas de proposito: /contas mostra o
        # historico delas com "Reconectar".
        .where(UserSellerAccess.user_id == current_user.id)
        .order_by(UserSellerAccess.granted_at.asc())
    )
    sellers = sellers_result.scalars().all()

    if not sellers:
        return DashboardResponse(sellers=[])

    seller_ids = [s.id for s in sellers]

    # Agrega contagens de listings por seller + status em uma única query
    counts_result = await db.execute(
        select(Listing.seller_id, Listing.status, func.count().label("cnt"))
        .where(Listing.seller_id.in_(seller_ids))
        .group_by(Listing.seller_id, Listing.status)
    )
    counts_rows = counts_result.all()

    # Última atividade por seller
    activity_result = await db.execute(
        select(Listing.seller_id, func.max(Listing.updated_at).label("last_at"))
        .where(Listing.seller_id.in_(seller_ids))
        .group_by(Listing.seller_id)
    )
    activity_map: dict = {row.seller_id: row.last_at for row in activity_result.all()}

    # Monta o mapa seller_id → {status: count}
    status_map: dict = {s.id: {} for s in sellers}
    for row in counts_rows:
        status_map[row.seller_id][row.status] = row.cnt

    entries = []
    for seller in sellers:
        by_status = status_map.get(seller.id, {})
        entries.append(
            SellerDashboardEntry(
                seller_id=seller.id,
                ml_nickname=seller.ml_nickname,
                is_active=seller.is_active,
                listings_by_status=by_status,
                total_listings=sum(by_status.values()),
                last_activity_at=activity_map.get(seller.id),
            )
        )

    return DashboardResponse(sellers=entries)
