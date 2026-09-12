from decimal import Decimal
from typing import Optional, Any, Literal
from uuid import UUID
from datetime import datetime
from pydantic import BaseModel, Field, field_validator


class ListingCreate(BaseModel):
    sku_external_id: Optional[str] = None
    sku_description: str
    sku_brand: str
    price: Decimal
    stock_quantity: int
    condition: str
    listing_type_id: str = "gold_special"

    @field_validator("condition")
    @classmethod
    def condition_must_be_valid(cls, v: str) -> str:
        if v not in ("new", "used"):
            raise ValueError("Condição deve ser 'new' ou 'used'")
        return v

    @field_validator("listing_type_id")
    @classmethod
    def listing_type_must_be_valid(cls, v: str) -> str:
        if v not in ("gold_special", "gold_pro"):
            raise ValueError("Tipo de anúncio deve ser 'gold_special' (Clássico) ou 'gold_pro' (Premium)")
        return v

    @field_validator("price")
    @classmethod
    def price_must_be_positive(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError("Preço deve ser maior que zero")
        return v

    @field_validator("stock_quantity")
    @classmethod
    def stock_must_be_positive(cls, v: int) -> int:
        if v < 1:
            raise ValueError("Estoque deve ser pelo menos 1")
        return v


class ListingSummary(BaseModel):
    id: UUID
    product_id: Optional[UUID] = None
    sku_external_id: Optional[str]
    sku_brand: str
    selected_title: Optional[str]
    status: str
    failed_step: Optional[str] = None
    created_via: str
    mlb_id: Optional[str]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ListingPage(BaseModel):
    items: list[ListingSummary]
    total: int
    page: int
    page_size: int


class ListingStatusCounts(BaseModel):
    """Contagem de anuncios do seller ativo por status. `by_status` traz TODOS
    os status de `LISTING_STATUSES` (zero quando nao ha anuncio) e, se o banco
    tiver algum status fora da lista (legado), ele tambem aparece — nenhum
    anuncio some da conta. `total` e' a soma."""
    by_status: dict[str, int]
    total: int


class TitleOption(BaseModel):
    id: UUID
    title_text: str
    ai_score: Optional[Decimal]
    selected: bool

    model_config = {"from_attributes": True}


class AttributeOut(BaseModel):
    id: UUID
    attribute_id: str
    attribute_name: str
    attribute_type: str
    is_required: bool
    value_id: Optional[str]
    value_name: Optional[str]
    source: str
    allowed_values: Optional[list[Any]]
    # Tags do ML como vieram; `is_editable` e' a propriedade do model
    # (`ListingAttribute.is_editable`), lida pelo `from_attributes`.
    tags: Optional[dict[str, Any]] = None
    is_editable: bool = True

    model_config = {"from_attributes": True}


class JobOut(BaseModel):
    id: UUID
    job_type: str
    celery_task_id: Optional[str]
    status: str
    error_message: Optional[str]
    attempts: int
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    created_at: datetime

    model_config = {"from_attributes": True}


class ImageOut(BaseModel):
    id: UUID
    ml_picture_id: Optional[str]
    status: str
    approved: bool
    sort_order: int
    # Para a tela de revisao: qual posicao e' (kind), se e' candidata das
    # Frentes A/B (posicao >= 90, calculado no model) e por que reprovou no QA.
    kind: str
    is_candidate: bool
    validation_error: Optional[str] = None

    model_config = {"from_attributes": True}


class ImageApproveRequest(BaseModel):
    approved_ids: list[UUID]
    # Tempo (segundos) que um humano levou comparando a imagem gerada por IA
    # contra o dado real antes de aprovar. Opcional: ausente -> fica NULL,
    # comportamento identico ao de antes deste campo existir. `ge=0`: tempo
    # negativo nao existe, e o valor cai direto em `review_seconds` do
    # evento de revisao.
    review_seconds: Optional[int] = Field(default=None, ge=0)


class ListingDetail(ListingSummary):
    sku_description: str
    price: Decimal
    stock_quantity: int
    condition: str
    listing_type_id: str
    ml_category_id: Optional[str]
    error_message: Optional[str]
    description_html: Optional[str] = None
    titles: list[TitleOption] = []
    attributes: list[AttributeOut] = []
    images: list[ImageOut] = []
    jobs: list[JobOut] = []
