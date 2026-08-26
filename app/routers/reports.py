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
from app.models.production import ProductionBatch
from app.models.sales import SalesOrder, SalesOrderItem
from app.models.stock import StockLedger
from app.routers.sales import get_hpp_for_sale
from app.schemas.reports import (
    DashboardResponse,
    LowStockAlert,
    MarginRankingEntry,
    SalesReportPoint,
    SalesReportResponse,
    StockCardMovement,
    StockCardResponse,
    WasteByMaterialEntry,
    ProductSalesRankingEntry,
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


def _avg_margin_pct(db: Session) -> float:
    """v3.19 bug fix #2 (doc/fix-margin-ranking.txt): same issue as margin_ranking() below --
    only ever averaged confirmed-batch HPP, so a shop running entirely on manual HPP /
    PatternSpec-estimated sizes always got avg_margin_pct=0.0, shown as "0% margin" on the
    dashboard. Reuses the same batch -> manual -> pattern_spec -> none fallback.
    """
    sizes = db.query(ProductSize).filter(ProductSize.selling_price > 0).all()
    margins = []
    for size in sizes:
        hpp_total, _hpp_source = get_hpp_for_sale(db, size.id)
        if hpp_total > 0:
            margins.append(compute_margin_pct(size.selling_price, hpp_total))
    return sum(margins) / len(margins) if margins else 0.0


@router.get("/dashboard", response_model=DashboardResponse)
def dashboard(db: Session = Depends(get_db)):
    # "today"/"this month" per server timezone -- this server runs in UTC (Cloud Run default, no
    # TZ config exists anywhere else in this app), so UTC is literally the server timezone here.
    today = datetime.now(timezone.utc).date()
    day_start = datetime.combine(today, datetime.min.time(), tzinfo=timezone.utc)
    day_end = day_start + timedelta(days=1)
    month_start = day_start.replace(day=1)

    today_orders = (
        db.query(SalesOrder)
        .options(joinedload(SalesOrder.items))
        .filter(SalesOrder.sold_at >= day_start, SalesOrder.sold_at < day_end, SalesOrder.status != "cancelled")
        .all()
    )
    month_orders_list = (
        db.query(SalesOrder)
        .options(joinedload(SalesOrder.items))
        .filter(SalesOrder.sold_at >= month_start, SalesOrder.sold_at < day_end, SalesOrder.status != "cancelled")
        .all()
    )

    today_revenue = today_profit = 0.0
    today_units_sold = 0
    for order in today_orders:
        for item in order.items:
            today_revenue += (item.unit_price - item.discount) * item.qty
            today_profit += item.line_profit
            today_units_sold += item.qty

    month_revenue = 0.0
    month_units_sold = 0
    for order in month_orders_list:
        for item in order.items:
            month_revenue += (item.unit_price - item.discount) * item.qty
            month_units_sold += item.qty

    month_batches_confirmed = (
        db.query(ProductionBatch)
        .filter(ProductionBatch.status == "confirmed", ProductionBatch.confirmed_at >= month_start)
        .count()
    )

    return DashboardResponse(
        today_revenue=today_revenue,
        today_order_count=len(today_orders),
        today_profit=today_profit,
        today_units_sold=today_units_sold,
        month_revenue=month_revenue,
        month_orders=len(month_orders_list),
        month_units_sold=month_units_sold,
        month_batches_confirmed=month_batches_confirmed,
        avg_margin_pct=_avg_margin_pct(db),
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
    """v3.19 bug fix: previously only considered a confirmed ProductionBatchItem (get_hpp_for_sale's
    tier 1), so any size priced via manual HPP or a PatternSpec estimate -- with no confirmed batch
    at all -- silently never appeared here, returning []. Now reuses the same batch -> manual ->
    pattern_spec -> none fallback POST /sales-orders uses, so this ranking reflects whatever HPP a
    sale of that size would actually be costed at.
    """
    if sort != "margin_pct":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="sort must be margin_pct")

    sizes = (
        db.query(ProductSize)
        .options(joinedload(ProductSize.product))
        .filter(ProductSize.is_archived.is_(False), ProductSize.selling_price > 0)
        .all()
    )

    entries = []
    for size in sizes:
        hpp_total, _hpp_source = get_hpp_for_sale(db, size.id)
        if hpp_total <= 0:
            continue
        entries.append(
            MarginRankingEntry(
                product_size_id=size.id,
                product_name=size.product.name,
                size_label=size.size_label,
                fabric_variant_name=size.fabric_variant_name,
                selling_price=size.selling_price,
                hpp_total=hpp_total,
                margin_pct=compute_margin_pct(size.selling_price, hpp_total),
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


@router.get("/sales-by-product", response_model=list[ProductSalesRankingEntry])
def product_sales_ranking(
    from_: date | None = Query(default=None, alias="from"),
    to: date | None = Query(default=None),
    db: Session = Depends(get_db),
):
    if from_ is None or to is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="from and to are required")
    if from_ > to:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="from must be <= to")

    start = datetime.combine(from_, datetime.min.time(), tzinfo=timezone.utc)
    end = datetime.combine(to, datetime.min.time(), tzinfo=timezone.utc) + timedelta(days=1)

    from sqlalchemy import func

    results = (
        db.query(
            ProductSize.id.label("product_size_id"),
            Product.name.label("product_name"),
            ProductSize.size_label.label("size_label"),
            ProductSize.fabric_variant_name.label("fabric_variant_name"),
            func.sum(SalesOrderItem.qty).label("qty_sold"),
            func.sum((SalesOrderItem.unit_price - SalesOrderItem.discount) * SalesOrderItem.qty).label("revenue"),
            func.sum(SalesOrderItem.line_profit).label("profit"),
        )
        .join(SalesOrderItem, ProductSize.id == SalesOrderItem.product_size_id)
        .join(SalesOrder, SalesOrderItem.sales_order_id == SalesOrder.id)
        .join(Product, ProductSize.product_id == Product.id)
        .filter(
            SalesOrder.sold_at >= start,
            SalesOrder.sold_at < end,
            SalesOrder.status != "cancelled"
        )
        .group_by(
            ProductSize.id,
            Product.name,
            ProductSize.size_label,
            ProductSize.fabric_variant_name
        )
        .order_by(func.sum(SalesOrderItem.qty).desc())
        .all()
    )

    from app.routers.products import _stock_qty_map
    size_ids = [r.product_size_id for r in results]
    stock_map = _stock_qty_map(db, size_ids)

    return [
        ProductSalesRankingEntry(
            product_size_id=r.product_size_id,
            product_name=r.product_name,
            size_label=r.size_label,
            fabric_variant_name=r.fabric_variant_name,
            qty_sold=int(r.qty_sold),
            revenue=float(r.revenue),
            profit=float(r.profit),
            current_stock_qty=stock_map.get(r.product_size_id, 0),
        )
        for r in results
    ]
