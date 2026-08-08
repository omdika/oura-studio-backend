import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.deps import get_current_owner
from app.models.cutting import CuttingLayout
from app.models.material import Material, MaterialPurchase
from app.models.product import Product, ProductSize
from app.models.production import ProductionBatch, ProductionBatchItem
from app.models.sales import SalesOrder, SalesOrderItem
from app.models.stock import StockLedger
from app.schemas.reports import (
    DashboardResponse,
    LowStockAlert,
    MarginRankingEntry,
    SalesReportPoint,
    SalesReportResponse,
    StockCardMovement,
    StockCardResponse,
    WasteByMaterialEntry,
)
from app.services.pricing import compute_margin_pct
from app.services.reports import bucket_start

router = APIRouter(prefix="/reports", tags=["reports"], dependencies=[Depends(get_current_owner)])


def _low_stock_alerts(db: Session) -> list[LowStockAlert]:
    sizes = (
        db.query(ProductSize)
        .options(joinedload(ProductSize.product))
        .filter(ProductSize.is_archived.is_(False), ProductSize.reorder_min_qty.isnot(None))
        .all()
    )
    alerts = []
    for size in sizes:
        qty = (
            db.query(StockLedger)
            .with_entities(StockLedger.change_qty)
            .filter(StockLedger.product_size_id == size.id)
        )
        current_qty = sum(row[0] for row in qty.all())
        if current_qty < size.reorder_min_qty:
            alerts.append(
                LowStockAlert(
                    product_size_id=size.id,
                    product_name=size.product.name,
                    size_label=size.size_label,
                    fabric_variant_name=size.fabric_variant_name,
                    current_stock_qty=current_qty,
                    reorder_min_qty=size.reorder_min_qty,
                )
            )
    return alerts


@router.get("/dashboard", response_model=DashboardResponse)
def dashboard(db: Session = Depends(get_db)):
    # "today" per server timezone -- this server runs in UTC (Cloud Run default, no TZ config
    # exists anywhere else in this app), so UTC is literally the server timezone here.
    today = datetime.now(timezone.utc).date()
    start = datetime.combine(today, datetime.min.time(), tzinfo=timezone.utc)
    end = start + timedelta(days=1)

    orders = (
        db.query(SalesOrder)
        .options(joinedload(SalesOrder.items))
        .filter(SalesOrder.sold_at >= start, SalesOrder.sold_at < end, SalesOrder.status != "cancelled")
        .all()
    )

    revenue = 0.0
    profit = 0.0
    units_sold = 0
    for order in orders:
        for item in order.items:
            revenue += (item.unit_price - item.discount) * item.qty
            profit += item.line_profit
            units_sold += item.qty

    return DashboardResponse(
        today_revenue=revenue,
        today_order_count=len(orders),
        today_profit=profit,
        today_units_sold=units_sold,
        low_stock_alerts=_low_stock_alerts(db),
    )


@router.get("/sales", response_model=SalesReportResponse)
def sales_report(
    from_: date | None = Query(default=None, alias="from"),
    to: date | None = Query(default=None),
    group_by: str = Query(default="day"),
    db: Session = Depends(get_db),
):
    # handoff Section 4/skill non-negotiable rule #8: from/to are required -- omitting them must
    # be a 400, not FastAPI's default 422 for a missing required query param.
    if from_ is None or to is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="from and to are required")
    if group_by not in ("day", "week", "month"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="group_by must be day|week|month")
    if from_ > to:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="from must be <= to")

    start = datetime.combine(from_, datetime.min.time(), tzinfo=timezone.utc)
    end = datetime.combine(to, datetime.min.time(), tzinfo=timezone.utc) + timedelta(days=1)

    orders = (
        db.query(SalesOrder)
        .options(joinedload(SalesOrder.items))
        .filter(SalesOrder.sold_at >= start, SalesOrder.sold_at < end, SalesOrder.status != "cancelled")
        .all()
    )

    buckets: dict[date, dict] = defaultdict(lambda: {"revenue": 0.0, "profit": 0.0, "orders": 0})
    for order in orders:
        key = bucket_start(order.sold_at.date(), group_by)
        buckets[key]["orders"] += 1
        for item in order.items:
            buckets[key]["revenue"] += (item.unit_price - item.discount) * item.qty
            buckets[key]["profit"] += item.line_profit

    points = [
        SalesReportPoint(date=key, totalRevenue=val["revenue"], totalProfit=val["profit"], orderCount=val["orders"])
        for key, val in sorted(buckets.items())
    ]

    return SalesReportResponse(
        points=points,
        totalRevenue=sum(p.totalRevenue for p in points),
        totalProfit=sum(p.totalProfit for p in points),
    )


@router.get("/margin-ranking", response_model=list[MarginRankingEntry])
def margin_ranking(sort: str = Query(default="margin_pct"), db: Session = Depends(get_db)):
    if sort != "margin_pct":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="sort must be margin_pct")

    sizes = (
        db.query(ProductSize)
        .options(joinedload(ProductSize.product))
        .filter(ProductSize.is_archived.is_(False), ProductSize.selling_price.isnot(None))
        .all()
    )

    entries = []
    for size in sizes:
        latest_item = (
            db.query(ProductionBatchItem)
            .join(ProductionBatch, ProductionBatchItem.production_batch_id == ProductionBatch.id)
            .filter(ProductionBatchItem.product_size_id == size.id)
            .order_by(ProductionBatch.produced_at.desc())
            .first()
        )
        if latest_item is None:
            continue
        entries.append(
            MarginRankingEntry(
                product_size_id=size.id,
                product_name=size.product.name,
                size_label=size.size_label,
                fabric_variant_name=size.fabric_variant_name,
                selling_price=size.selling_price,
                hpp_total=latest_item.hpp_total,
                margin_pct=compute_margin_pct(size.selling_price, latest_item.hpp_total),
            )
        )

    entries.sort(key=lambda e: e.margin_pct, reverse=True)
    return entries


def _get_size_or_404(db: Session, size_id: uuid.UUID) -> ProductSize:
    size = db.query(ProductSize).options(joinedload(ProductSize.product)).filter(ProductSize.id == size_id).first()
    if size is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product size not found")
    return size


@router.get("/stock-card/{product_size_id}", response_model=StockCardResponse)
def stock_card(product_size_id: uuid.UUID, db: Session = Depends(get_db)):
    size = _get_size_or_404(db, product_size_id)

    ledger = (
        db.query(StockLedger)
        .filter(StockLedger.product_size_id == product_size_id)
        .order_by(StockLedger.created_at.asc())
        .all()
    )

    running = 0
    movements = []
    for row in ledger:
        running += row.change_qty
        movements.append(
            StockCardMovement(
                date=row.created_at,
                reason=row.reason,
                change_qty=row.change_qty,
                running_balance=running,
                unit_hpp_snapshot=row.unit_hpp_snapshot,
                ref_type=row.ref_type,
                ref_id=row.ref_id,
                note=row.note,
            )
        )

    return StockCardResponse(
        product_size_id=size.id,
        product_name=size.product.name,
        size_label=size.size_label,
        fabric_variant_name=size.fabric_variant_name,
        current_stock_qty=running,
        movements=movements,
    )


@router.get("/waste-by-material", response_model=list[WasteByMaterialEntry])
def waste_by_material(
    from_: date | None = Query(default=None, alias="from"),
    to: date | None = Query(default=None),
    db: Session = Depends(get_db),
):
    q = (
        db.query(CuttingLayout)
        .join(MaterialPurchase, CuttingLayout.material_purchase_id == MaterialPurchase.id)
        .filter(CuttingLayout.status == "used")
    )
    if from_ is not None:
        q = q.filter(CuttingLayout.created_at >= datetime.combine(from_, datetime.min.time(), tzinfo=timezone.utc))
    if to is not None:
        q = q.filter(
            CuttingLayout.created_at
            < datetime.combine(to, datetime.min.time(), tzinfo=timezone.utc) + timedelta(days=1)
        )
    layouts = q.all()

    by_material: dict[uuid.UUID, list[float]] = defaultdict(list)
    for layout in layouts:
        purchase = db.get(MaterialPurchase, layout.material_purchase_id)
        by_material[purchase.material_id].append(layout.waste_pct or 0.0)

    entries = []
    for material_id, waste_list in by_material.items():
        material = db.get(Material, material_id)
        entries.append(
            WasteByMaterialEntry(
                material_id=material_id,
                material_name=material.name if material else "?",
                layout_count=len(waste_list),
                avg_waste_pct=round(sum(waste_list) / len(waste_list), 2),
            )
        )

    entries.sort(key=lambda e: -e.avg_waste_pct)
    return entries


@router.get("/low-stock", response_model=list[LowStockAlert])
def low_stock(db: Session = Depends(get_db)):
    return _low_stock_alerts(db)
