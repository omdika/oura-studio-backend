import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class SalesOrderItemCreate(BaseModel):
    product_size_id: uuid.UUID
    qty: int = Field(gt=0)
    unit_price: float = Field(ge=0)
    discount: float = Field(default=0, ge=0)


class SalesOrderCreate(BaseModel):
    customer_name: str | None = None
    payment_method: str
    marketplace_fee_pct: float = Field(default=0, ge=0, lt=1)
    items: list[SalesOrderItemCreate] = Field(min_length=1)


class SalesOrderItemOut(BaseModel):
    id: uuid.UUID
    sales_order_id: uuid.UUID
    product_size_id: uuid.UUID
    product_name: str
    size_label: str
    qty: int
    unit_price: float
    discount: float
    unit_hpp_snapshot: float
    line_profit: float
    line_revenue: float


class SalesOrderOut(BaseModel):
    id: uuid.UUID
    invoice_no: str
    customer_name: str | None
    payment_method: str | None
    marketplace_fee_pct: float
    status: str
    sold_at: datetime
    total_revenue: float
    total_profit: float
    items: list[SalesOrderItemOut]


class SalesOrderStatusUpdate(BaseModel):
    status: str


class CancelRequest(BaseModel):
    reason: str | None = None
