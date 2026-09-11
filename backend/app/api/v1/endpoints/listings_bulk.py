# backend/app/api/v1/endpoints/listings_bulk.py
import uuid
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.dependencies import get_db, get_current_user, get_active_seller
from app.models.user import User
from app.models.seller import Seller
from app.models.listing import Listing
from app.models.listing_attribute import ListingAttribute
from app.schemas.bulk import (
    BulkListingRequest, BulkAttributeRequest, BulkResult,
    ListingAttributesRow, AttributeItem,
)
from app.services.listing_service import ListingService

router = APIRouter(prefix="/bulk", tags=["listings-bulk"])


def _svc(db: AsyncSession, active_seller: Seller) -> ListingService:
    return ListingService(db, active_seller.id)


@router.get("/attributes", response_model=list[ListingAttributesRow])
async def get_listings_for_attribute_grid(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    active_seller: Seller = Depends(get_active_seller),
):
    result = await db.execute(
        select(Listing)
        .where(
            Listing.seller_id == active_seller.id,
            Listing.status.in_(["pending_seller_attributes", "pending_description"]),
        )
        .order_by(Listing.ml_category_id.nulls_last(), Listing.created_at)
    )
    listings = result.scalars().all()
    rows: list[ListingAttributesRow] = []
    for listing in listings:
        attrs_r = await db.execute(
            select(ListingAttribute)
            .where(ListingAttribute.listing_id == listing.id)
            .order_by(ListingAttribute.is_required.desc(), ListingAttribute.attribute_name)
        )
        rows.append(ListingAttributesRow(
            listing_id=listing.id,
            sku_external_id=listing.sku_external_id,
            selected_title=listing.selected_title,
            ml_category_id=listing.ml_category_id,
            status=listing.status,
            attributes=[
                AttributeItem(
                    attribute_id=a.attribute_id,
                    attribute_name=a.attribute_name,
                    value_name=a.value_name,
                    value_id=a.value_id,
                    is_required=a.is_required,
                )
                for a in attrs_r.scalars().all()
            ],
        ))
    return rows


@router.post("/start-pipeline", response_model=BulkResult)
async def bulk_start_pipeline(
    payload: BulkListingRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    active_seller: Seller = Depends(get_active_seller),
):
    return await _svc(db, active_seller).bulk_start_pipeline(payload.listing_ids)


@router.post("/approve-titles", response_model=BulkResult)
async def bulk_approve_titles(
    payload: BulkListingRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    active_seller: Seller = Depends(get_active_seller),
):
    return await _svc(db, active_seller).bulk_approve_titles(payload.listing_ids)


@router.post("/reject-titles", response_model=BulkResult)
async def bulk_reject_titles(
    payload: BulkListingRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    active_seller: Seller = Depends(get_active_seller),
):
    return await _svc(db, active_seller).bulk_reject_titles(payload.listing_ids)


@router.post("/approve-images", response_model=BulkResult)
async def bulk_approve_images(
    payload: BulkListingRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    active_seller: Seller = Depends(get_active_seller),
):
    return await _svc(db, active_seller).bulk_approve_images(payload.listing_ids, user_id=current_user.id)


@router.post("/generate-images", response_model=BulkResult)
async def bulk_generate_images(
    payload: BulkListingRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    active_seller: Seller = Depends(get_active_seller),
):
    return await _svc(db, active_seller).bulk_generate_images(payload.listing_ids)


@router.post("/publish", response_model=BulkResult)
async def bulk_publish(
    payload: BulkListingRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    active_seller: Seller = Depends(get_active_seller),
):
    return await _svc(db, active_seller).bulk_publish(payload.listing_ids)


@router.put("/attribute", response_model=BulkResult)
async def bulk_fill_attribute(
    payload: BulkAttributeRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    active_seller: Seller = Depends(get_active_seller),
):
    return await _svc(db, active_seller).bulk_fill_attribute(
        listing_ids=payload.listing_ids,
        attribute_id=payload.attribute_id,
        value_name=payload.value_name,
        value_id=payload.value_id,
    )
