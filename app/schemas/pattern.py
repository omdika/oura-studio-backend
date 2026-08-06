import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


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
    est_labor_minutes: float = Field(gt=0)
    components: list[PatternComponentIn] = []


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
