import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class PatternComponentIn(BaseModel):
    material_id: uuid.UUID
    qty_per_unit: float = Field(gt=0)


class PatternComponentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    material_id: uuid.UUID
    qty_per_unit: float


class PatternSpecCreate(BaseModel):
    product_size_id: uuid.UUID
    fabric_material_id: uuid.UUID
    cut_width_cm: float = Field(gt=0)
    cut_height_cm: float = Field(gt=0)
    rotation_allowed: bool = True
    est_labor_minutes: float
    components: list[PatternComponentIn] = []

    @field_validator("est_labor_minutes")
    @classmethod
    def must_be_positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("est_labor_minutes harus lebih dari 0")
        return v


class PatternSpecOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    product_size_id: uuid.UUID
    fabric_material_id: uuid.UUID
    cut_width_cm: float
    cut_height_cm: float
    rotation_allowed: bool
    est_labor_minutes: float
    is_active: bool
    effective_from: datetime
    effective_to: datetime | None
    components: list[PatternComponentOut]

    # Read-only enrichment (v2.4 iOS integration): resolved via JOIN so the client doesn't have to
    # do N+1 lookups against /products and /products/{sku}/sizes just to render a human-readable Resep row.
    product_sku: str | None = None
    product_name: str | None = None
    size_label: str | None = None
    fabric_material_name: str | None = None
