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



class ProductSizeImageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    product_size_id: uuid.UUID
    image_url: str
    created_at: datetime


class ProductSizeCreate(BaseModel):
    size_label: str
    fabric_variant_name: str | None = None
    reorder_min_qty: float | None = None


class ProductSizeUpdate(BaseModel):
    selling_price: float | None = None
    reorder_min_qty: float | None = None
    is_archived: bool | None = None
    # v3.19: manual HPP override, per component. PATCH semantics: omitted (None) = leave existing
    # value unchanged, not "reset to null" -- consistent with selling_price/reorder_min_qty above.
    manual_hpp_fabric: float | None = None
    manual_hpp_pooled: float | None = None
    manual_hpp_hardware: float | None = None
    manual_hpp_labor: float | None = None
    manual_hpp_overhead: float | None = None


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
    production_stock_qty: int  # sum of stock_ledger.change_qty where reason='production' (added v2.10)
    manual_stock_qty: int  # sum of stock_ledger.change_qty where reason IN ('initial','adjustment') (added v2.10)
    # v3.19: manual HPP override, per component. manual_hpp_total is always a number (0 if every
    # component is unset), never null -- frontend treats it as a number directly.
    manual_hpp_fabric: float | None = None
    manual_hpp_pooled: float | None = None
    manual_hpp_hardware: float | None = None
    manual_hpp_labor: float | None = None
    manual_hpp_overhead: float | None = None
    manual_hpp_total: float = 0
    images: list[ProductSizeImageOut] = []


class HppLineItemOut(BaseModel):
    name: str
    cost: float


class HppBreakdownOut(BaseModel):
    fabric: float
    fabric_items: list[HppLineItemOut] = []  # v3.8: per-fabric-layer breakdown, best-effort
    pooled_material: float
    hardware: float
    hardware_items: list[HppLineItemOut] = []  # v3.8: per-hardware-component breakdown, best-effort
    labor: float
    overhead: float
    total: float


class ProductSizeDetailOut(ProductSizeOut):
    latest_hpp_breakdown: HppBreakdownOut | None
    margin_pct: float | None


class ProductSizeWithProductOut(ProductSizeDetailOut):
    """v3.17: same shape as ProductSizeDetailOut, plus product_sku/product_name.

    Used by GET /product-sizes/{size_id} (QR-code lookup) -- unlike GET /products/{sku}/sizes,
    that endpoint's URL doesn't carry the SKU, so the response has to embed it. Kept as a
    separate model rather than adding these fields to ProductSizeDetailOut so the existing
    GET /products/{sku}/sizes response shape doesn't change (no regression).
    """

    product_sku: str
    product_name: str


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
    # v3.19: frontend fetches the spec from GET /pattern-specs and passes its id explicitly,
    # rather than relying on the "single active PatternSpec for this size" implicit lookup below.
    # Optional for backward compatibility -- omit to fall back to that implicit lookup.
    spec_id: uuid.UUID | None = None
    # Optional: pin consumption to one specific purchase batch (also disambiguates which fabric
    # layer to use when the active PatternSpec has more than one). Omit to FIFO across all
    # purchases of the spec's single fabric layer's material (oldest purchased_at first).
    material_purchase_id: uuid.UUID | None = None
