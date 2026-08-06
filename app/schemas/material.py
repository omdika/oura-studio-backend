import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

MaterialCategory = Literal["fabric", "thread", "hardware", "packaging"]


class MaterialCreate(BaseModel):
    name: str
    category: MaterialCategory
    purchase_unit: str
    usage_unit: str
    fabric_width_cm: float | None = None
    reorder_min_qty: float | None = None


class MaterialUpdate(BaseModel):
    name: str | None = None
    fabric_width_cm: float | None = None
    reorder_min_qty: float | None = None
    is_archived: bool | None = None


class MaterialOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    category: str
    cost_class: str
    purchase_unit: str
    usage_unit: str
    fabric_width_cm: float | None
    current_avg_cost: float
    reorder_min_qty: float | None
    is_archived: bool
    created_at: datetime
    updated_at: datetime


class SupplierCreate(BaseModel):
    name: str


class SupplierUpdate(BaseModel):
    name: str


class SupplierOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    created_at: datetime


class MaterialPurchaseCreate(BaseModel):
    width_cm: float | None = None
    length_cm: float | None = None
    qty: float | None = None
    package_label: str | None = None
    total_cost: float = Field(gt=0)
    supplier_id: uuid.UUID | None = None
    supplier_name: str | None = None
    purchased_at: date

    @model_validator(mode="after")
    def check_supplier_exclusive(self):
        if self.supplier_id is not None and self.supplier_name is not None:
            raise ValueError("provide either supplier_id or supplier_name, not both")
        return self


class MaterialPurchaseUpdate(BaseModel):
    width_cm: float | None = None
    length_cm: float | None = None
    qty: float | None = None
    total_cost: float | None = Field(default=None, gt=0)
    supplier_id: uuid.UUID | None = None
    supplier_name: str | None = None
    purchased_at: date | None = None

    @model_validator(mode="after")
    def check_supplier_exclusive(self):
        if self.supplier_id is not None and self.supplier_name is not None:
            raise ValueError("provide either supplier_id or supplier_name, not both")
        return self


class MaterialPurchaseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    material_id: uuid.UUID
    width_cm: float | None
    length_cm: float | None
    qty: float | None
    package_label: str | None
    total_cost: float
    supplier_id: uuid.UUID | None
    purchased_at: date
    remaining_length_cm: float | None
    remaining_qty: float | None
    created_at: datetime
    is_consumed: bool
