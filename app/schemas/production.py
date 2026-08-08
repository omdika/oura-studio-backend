import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ProductionBatchCreate(BaseModel):
    cutting_layout_id: uuid.UUID | None = None


class ItemQtyUpdate(BaseModel):
    qty_actual: int = Field(ge=0)


class ProductionBatchItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    production_batch_id: uuid.UUID
    product_size_id: uuid.UUID | None
    pattern_spec_id: uuid.UUID | None
    qty_actual: int
    qty_suggested: int | None
    cutting_layout_item_id: uuid.UUID | None
    material_purchase_id: uuid.UUID
    fabric_cost_per_piece: float
    fabric_length_per_unit_cm: float
    hpp_fabric: float
    hpp_pooled_material: float
    hpp_hardware: float
    hpp_labor: float
    hpp_overhead: float
    hpp_total: float


class ProductionBatchOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    cutting_layout_id: uuid.UUID | None
    produced_at: datetime
    status: str
    notes: str | None
    items: list[ProductionBatchItemOut]
