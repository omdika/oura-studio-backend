import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ProductCreate(BaseModel):
    name: str
    sku: str | None = None


class ProductUpdate(BaseModel):
    name: str | None = None
    is_archived: bool | None = None


class ProductOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    sku: str
    name: str
    is_archived: bool
    created_at: datetime


class ProductSizeCreate(BaseModel):
    size_label: str
    fabric_variant_name: str | None = None
    reorder_min_qty: float | None = None


class ProductSizeUpdate(BaseModel):
    selling_price: float | None = None
    reorder_min_qty: float | None = None
    is_archived: bool | None = None


class ProductSizeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    product_id: uuid.UUID
    size_label: str
    fabric_variant_name: str | None
    reorder_min_qty: float | None
    selling_price: float | None
    is_archived: bool
    current_stock_qty: int


class HppBreakdownOut(BaseModel):
    hpp_fabric: float
    hpp_pooled_material: float
    hpp_hardware: float
    hpp_labor: float
    hpp_overhead: float
    hpp_total: float


class ProductSizeDetailOut(ProductSizeOut):
    latest_hpp_breakdown: HppBreakdownOut | None
    margin_pct: float | None


class DeleteResultOut(BaseModel):
    deleted: bool  # True = hard-deleted, False = archived instead


class PriceAdvisorRequest(BaseModel):
    target_margin_pct: float = Field(ge=0, lt=1)
    marketplace_fee_pct: float = Field(default=0, ge=0, lt=1)
    promo_allocation_pct: float = Field(default=0, ge=0, lt=1)


class PriceAdvisorResponse(BaseModel):
    suggested_price: float
    resulting_margin_pct: float
    resulting_markup_pct: float


class StockAdjustmentCreate(BaseModel):
    product_size_id: uuid.UUID
    change_qty: int
    reason: str
    note: str | None = None


class StockAdjustmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    product_size_id: uuid.UUID
    change_qty: int
    reason: str
    ref_type: str | None
    ref_id: uuid.UUID | None
    created_at: datetime


class AddStockFromBahanRequest(BaseModel):
    qty: int = Field(gt=0)
    # Optional: pin consumption to one specific purchase batch. Omit to FIFO across all purchases
    # of the active PatternSpec's fabric_material_id (oldest purchased_at first).
    material_purchase_id: uuid.UUID | None = None


class PurchaseConsumedOut(BaseModel):
    material_purchase_id: uuid.UUID
    length_cm_consumed: float


class AddStockFromBahanResponse(BaseModel):
    stock_ledger_id: uuid.UUID
    change_qty: int
    fabric_length_consumed_cm: float
    purchases_consumed: list[PurchaseConsumedOut]
