import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ProductionBatchCreate(BaseModel):
    cutting_layout_ids: list[uuid.UUID] = []  # empty = manual batch
    notes: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _coerce_single_layout(cls, data):
        # Backward compat: older clients send singular cutting_layout_id.
        if isinstance(data, dict) and "cutting_layout_id" in data and "cutting_layout_ids" not in data:
            lid = data.pop("cutting_layout_id")
            data["cutting_layout_ids"] = [lid] if lid else []
        return data


class ItemQtyUpdate(BaseModel):
    qty_actual: int = Field(ge=0)


# v2.14: New schema for adding items to a manual batch
class ProductionBatchItemCreate(BaseModel):
    product_size_id: uuid.UUID
    qty_actual: int = Field(ge=1)


class ProductionBatchItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    production_batch_id: uuid.UUID
    product_size_id: uuid.UUID
    pattern_spec_id: uuid.UUID | None # v2.14: nullable for manual batches
    qty_actual: int
    qty_suggested: int | None
    cutting_layout_item_id: uuid.UUID | None
    material_purchase_id: uuid.UUID | None
    fabric_cost_per_piece: float | None # v2.14: nullable for manual batches
    fabric_length_per_unit_cm: float | None
    hpp_fabric: float
    hpp_pooled_material: float
    hpp_hardware: float
    hpp_labor: float
    hpp_overhead: float
    hpp_total: float


class ProductionBatchOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    cutting_layout_ids: list[uuid.UUID] = []  # ordered by sort_order
    cutting_layout_id: uuid.UUID | None = None  # alias = cutting_layout_ids[0] if any
    cutting_layout_strategy: str | None
    material_name: str | None
    produced_at: datetime
    status: str
    notes: str | None
    confirmed_at: datetime | None
    items: list[ProductionBatchItemOut]
