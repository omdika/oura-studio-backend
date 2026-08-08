import uuid
from datetime import date, datetime

from pydantic import BaseModel


class LowStockAlert(BaseModel):
    product_size_id: uuid.UUID
    product_name: str
    size_label: str
    fabric_variant_name: str | None
    current_stock_qty: int
    reorder_min_qty: float


class DashboardResponse(BaseModel):
    today_revenue: float
    today_order_count: int
    today_profit: float
    today_units_sold: int
    low_stock_alerts: list[LowStockAlert]


class SalesReportPoint(BaseModel):
    date: date
    totalRevenue: float
    totalProfit: float
    orderCount: int


class SalesReportResponse(BaseModel):
    points: list[SalesReportPoint]
    totalRevenue: float
    totalProfit: float


class MarginRankingEntry(BaseModel):
    product_size_id: uuid.UUID
    product_name: str
    size_label: str
    fabric_variant_name: str | None
    selling_price: float
    hpp_total: float
    margin_pct: float


class StockCardMovement(BaseModel):
    date: datetime
    reason: str
    change_qty: int
    running_balance: int
    unit_hpp_snapshot: float | None
    ref_type: str | None
    ref_id: uuid.UUID | None
    note: str | None


class StockCardResponse(BaseModel):
    product_size_id: uuid.UUID
    product_name: str
    size_label: str
    fabric_variant_name: str | None
    current_stock_qty: int
    movements: list[StockCardMovement]


class WasteByMaterialEntry(BaseModel):
    material_id: uuid.UUID
    material_name: str
    layout_count: int
    avg_waste_pct: float
