import uuid

from pydantic import BaseModel, Field


class SuggestCandidateIn(BaseModel):
    product_size_id: uuid.UUID
    pattern_spec_id: uuid.UUID
    min_qty: int | None = 0


class SuggestRequest(BaseModel):
    material_purchase_id: uuid.UUID
    candidates: list[SuggestCandidateIn]


class LayoutItem(BaseModel):
    product_size_id: uuid.UUID
    pattern_spec_id: uuid.UUID
    orientation: str
    qty_suggested: int
    fabric_length_used_cm: float
    cost_per_piece: float


class SuggestedLayout(BaseModel):
    strategy: str
    waste_pct: float
    items: list[LayoutItem]


class SuggestResponse(BaseModel):
    layouts: list[SuggestedLayout]


class CreateLayoutRequest(BaseModel):
    material_purchase_id: uuid.UUID
    strategy: str
    waste_pct: float | None = None
    items: list[LayoutItem] = Field(min_length=1)


class CreateLayoutResponse(BaseModel):
    cutting_layout_id: uuid.UUID
