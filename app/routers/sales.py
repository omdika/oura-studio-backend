import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.deps import get_current_owner
from app.models.material import Material
from app.models.pattern import PatternComponent, PatternSpec
from app.models.product import Product, ProductSize
from app.models.sales import SalesOrder, SalesOrderItem
from app.models.settings import Setting
from app.models.stock import StockLedger
from app.schemas.sales import (
    CancelRequest,
    SalesOrderCreate,
    SalesOrderItemOut,
    SalesOrderOut,
    SalesOrderStatusUpdate,
)
from app.services.cutting_optimizer import estimate_fabric_cost_per_piece_from_rate
from app.services.hpp import compute_hpp

router = APIRouter(prefix="/sales-orders", tags=["sales"], dependencies=[Depends(get_current_owner)])


def _get_order_or_404(db: Session, order_id: uuid.UUID) -> SalesOrder:
    order = (
        db.query(SalesOrder).options(joinedload(SalesOrder.items)).filter(SalesOrder.id == order_id).first()
    )
    if order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sales order not found")
    return order


def _size_display_map(db: Session, product_size_ids: list[uuid.UUID]) -> dict[uuid.UUID, tuple[str, str]]:
    # {product_size_id: (product_name, size_label)}. size_label mirrors the "{size_label} · {fabric}"
    # display convention used elsewhere (e.g. handoff Section 4 sizes list) when fabric_variant_name is set.
    if not product_size_ids:
        return {}
    rows = (
        db.query(ProductSize, Product.name)
        .join(Product, ProductSize.product_id == Product.id)
        .filter(ProductSize.id.in_(product_size_ids))
        .all()
    )
    result = {}
    for size, product_name in rows:
        size_label = size.size_label
        if size.fabric_variant_name:
            size_label = f"{size_label} · {size.fabric_variant_name}"
        result[size.id] = (product_name, size_label)
    return result


def _item_out(item: SalesOrderItem, size_map: dict[uuid.UUID, tuple[str, str]]) -> SalesOrderItemOut:
    product_name, size_label = size_map.get(item.product_size_id, ("Produk", "-"))
    return SalesOrderItemOut(
        id=item.id,
        sales_order_id=item.sales_order_id,
        product_size_id=item.product_size_id,
        product_name=product_name,
        size_label=size_label,
        qty=item.qty,
        unit_price=item.unit_price,
        discount=item.discount,
        unit_hpp_snapshot=item.unit_hpp_snapshot,
        hpp_source=item.hpp_source,
        line_profit=item.line_profit,
        line_revenue=(item.unit_price - item.discount) * item.qty,
    )


def _order_out(order: SalesOrder, size_map: dict[uuid.UUID, tuple[str, str]]) -> SalesOrderOut:
    items_out = [_item_out(item, size_map) for item in order.items]
    return SalesOrderOut(
        id=order.id,
        invoice_no=order.invoice_no,
        customer_name=order.customer_name,
        payment_method=order.payment_method,
        marketplace_fee_pct=order.marketplace_fee_pct,
        status=order.status,
        sold_at=order.sold_at,
        total_revenue=sum(i.line_revenue for i in items_out),
        total_profit=sum(i.line_profit for i in items_out),
        items=items_out,
    )


def _orders_out(db: Session, orders: list[SalesOrder]) -> list[SalesOrderOut]:
    size_map = _size_display_map(db, [item.product_size_id for order in orders for item in order.items])
    return [_order_out(order, size_map) for order in orders]


def _single_order_out(db: Session, order: SalesOrder) -> SalesOrderOut:
    return _orders_out(db, [order])[0]


def _current_stock_qty(db: Session, product_size_id: uuid.UUID) -> int:
    total = (
        db.query(func.coalesce(func.sum(StockLedger.change_qty), 0))
        .filter(StockLedger.product_size_id == product_size_id)
        .scalar()
    )
    return int(total)


def _latest_hpp_snapshot(db: Session, product_size_id: uuid.UUID) -> float | None:
    # Bug fix (found while implementing v3.19): must filter reason='production' -- without it,
    # this picks up the unit_hpp_snapshot this same function wrote onto the *previous* sale's own
    # stock_ledger row (POST /sales-orders writes one on every sale, reason='sale', even when the
    # HPP fallback used was tier 2/3/4 and the snapshot is 0 or an estimate). That made Tier 1
    # wrongly "lock in" whatever the last sale used -- including a stale/zero value -- and
    # permanently short-circuit tiers 2-4 (pattern_spec/manual/none) for every sale after the
    # first one, since 0.0 is `is not None`. Only a batch confirm should ever satisfy Tier 1.
    row = (
        db.query(StockLedger)
        .filter(
            StockLedger.product_size_id == product_size_id,
            StockLedger.reason == "production",
            StockLedger.unit_hpp_snapshot.isnot(None),
        )
        .order_by(StockLedger.created_at.desc())
        .first()
    )
    return row.unit_hpp_snapshot if row else None


def _get_setting(db: Session, key: str) -> float | None:
    setting = db.get(Setting, key)
    return setting.value if setting is not None else None


def _pooled_material_rate_sum(db: Session) -> float:
    rows = db.query(Setting).filter(Setting.key.like("pooled_material_rate:%")).all()
    return sum(s.value for s in rows)


def _hardware_cost_per_unit(db: Session, pattern_spec_id: uuid.UUID) -> float:
    # Mirrors routers/production.py's confirm-time hardware cost -- filtered defensively to
    # category='hardware' since POST /pattern-specs doesn't enforce that server-side either.
    rows = (
        db.query(PatternComponent, Material)
        .join(Material, PatternComponent.material_id == Material.id)
        .filter(
            PatternComponent.pattern_spec_id == pattern_spec_id,
            Material.category == "hardware",
            Material.cost_class == "direct_precise",
        )
        .all()
    )
    return sum(comp.qty_per_unit * material.current_avg_cost for comp, material in rows)


def _latest_active_pattern_spec(db: Session, product_size_id: uuid.UUID) -> PatternSpec | None:
    return (
        db.query(PatternSpec)
        .filter(PatternSpec.product_size_id == product_size_id, PatternSpec.is_active.is_(True))
        .order_by(PatternSpec.effective_from.desc())
        .first()
    )


def get_hpp_for_sale(db: Session, product_size_id: uuid.UUID) -> tuple[float, str]:
    """v3.18/v3.19 -- four-tier HPP fallback so a sale is never blocked just because stock was
    entered manually (adjustStock / stok awal) rather than through a confirmed production batch.

    Tier 1: latest confirmed production_batch_item HPP, via stock_ledger's snapshot (existing
            behavior, unchanged) -- hpp_source = "batch".
    Tier 2 (reordered, see bug note below): product_size has a manual HPP override (manual_hpp_*
            columns, sum > 0) -- hpp_source = "manual".
    Tier 3: no batch, no manual override, but an active PatternSpec exists -- estimate HPP from
            its fabric/hardware/labor/overhead, same formula as batch confirm
            (services/hpp.compute_hpp) -- hpp_source = "pattern_spec".
    Tier 4: none of the above -- hpp=0, sale still allowed -- hpp_source = "none". This was
            hpp_source="manual" in v3.18; renamed because v3.19 needed "manual" to mean an actual
            owner-entered override, not "no cost data available". Existing sales_order_item rows
            written before this change keep hpp_source="manual" with hpp=0 from that older meaning.

    Bug fix: manual was originally tier 3 (below pattern_spec), per doc/implement-v3.19-manual-
    hpp-stock.txt's literal ordering. In practice this made manual overrides dead code for any
    size entered via the QR "Dari Produksi" flow (POST .../stock-from-bahan), since that flow
    *requires* an active PatternSpec (spec_id) to run at all -- so tier "pattern_spec" always won
    before manual was ever checked. Confirmed live: a scrunchie material with no fabric_width_cm
    on record made the pattern_spec estimate compute fabric cost as cost_per_cm * cut_height_cm
    (whole-roll-width fallback in estimate_fabric_cost_per_piece_from_rate) = Rp 9,062.5/piece,
    vs. the owner's manual entry of Rp 90.625/piece -- a ~100x inflation that pushed hpp_total
    (Rp 12,572.5) above realistic selling prices, producing negative margin in the sales report.
    An explicit owner-entered override should win over an automated estimate, so manual now runs
    before pattern_spec.
    """
    batch_hpp = _latest_hpp_snapshot(db, product_size_id)
    if batch_hpp is not None:
        return batch_hpp, "batch"

    size = db.get(ProductSize, product_size_id)
    if size is not None and size.has_manual_hpp:
        return size.manual_hpp_total, "manual"

    spec = _latest_active_pattern_spec(db, product_size_id)
    if spec is not None:
        labor_rate = _get_setting(db, "labor_rate_per_minute") or 0.0
        overhead = _get_setting(db, "default_overhead_per_unit") or 0.0
        pooled_rate = _pooled_material_rate_sum(db)
        hardware_cost = _hardware_cost_per_unit(db, spec.id)

        fabric_cost = sum(
            estimate_fabric_cost_per_piece_from_rate(
                fabric_width_cm=fabric.material.fabric_width_cm,
                cut_width_cm=fabric.cut_width_cm,
                cut_height_cm=fabric.cut_height_cm,
                rotation_allowed=fabric.rotation_allowed,
                cost_per_cm=fabric.material.current_avg_cost,
            )
            for fabric in spec.fabrics
            if fabric.material is not None
        )

        breakdown = compute_hpp(
            fabric_cost_per_piece=fabric_cost,
            pooled_material_rate=pooled_rate,
            hardware_cost_per_unit=hardware_cost,
            est_labor_minutes=spec.est_labor_minutes,
            labor_rate_per_minute=labor_rate,
            overhead_per_unit=overhead,
        )
        return breakdown.hpp_total, "pattern_spec"

    return 0.0, "none"


def _generate_invoice_no(db: Session) -> str:
    # Format not specified by the handoff -- this implementation's own choice: sequential,
    # zero-padded. Safe against gaps since sales_order rows are never hard-deleted (Section 5:
    # corrections happen via /cancel, not deletion), so count-based numbering never reuses a number.
    count = db.query(SalesOrder).count()
    return f"INV-{count + 1:06d}"


@router.post("", response_model=SalesOrderOut, status_code=status.HTTP_201_CREATED)
def create_sales_order(body: SalesOrderCreate, db: Session = Depends(get_db)):
    resolved_items = []
    for line in body.items:
        size = db.get(ProductSize, line.product_size_id)
        if size is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"product_size_id {line.product_size_id} not found")

        available = _current_stock_qty(db, size.id)
        if available < line.qty:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Insufficient stock for product_size_id {line.product_size_id}: have {available}, need {line.qty}",
            )

        hpp_snapshot, hpp_source = get_hpp_for_sale(db, size.id)

        line_profit = (line.unit_price - line.discount - hpp_snapshot) * line.qty
        resolved_items.append((line, hpp_snapshot, hpp_source, line_profit))

    order = SalesOrder(
        invoice_no=_generate_invoice_no(db),
        customer_name=body.customer_name,
        payment_method=body.payment_method,
        marketplace_fee_pct=body.marketplace_fee_pct,
        status="unpaid",
    )
    db.add(order)
    db.flush()

    for line, hpp_snapshot, hpp_source, line_profit in resolved_items:
        db.add(
            SalesOrderItem(
                sales_order_id=order.id,
                product_size_id=line.product_size_id,
                qty=line.qty,
                unit_price=line.unit_price,
                discount=line.discount,
                unit_hpp_snapshot=hpp_snapshot,
                hpp_source=hpp_source,
                line_profit=line_profit,
            )
        )
        db.add(
            StockLedger(
                product_size_id=line.product_size_id,
                change_qty=-line.qty,
                reason="sale",
                ref_type="sales_order",
                ref_id=order.id,
                unit_hpp_snapshot=hpp_snapshot,
            )
        )

    db.commit()
    return _single_order_out(db, _get_order_or_404(db, order.id))


@router.get("", response_model=list[SalesOrderOut])
def list_sales_orders(
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, le=200),
    offset: int = 0,
    db: Session = Depends(get_db),
):
    q = db.query(SalesOrder).options(joinedload(SalesOrder.items))
    if status_filter is not None:
        q = q.filter(SalesOrder.status == status_filter)
    orders = q.order_by(SalesOrder.sold_at.desc()).offset(offset).limit(limit).all()
    return _orders_out(db, orders)


@router.get("/{order_id}", response_model=SalesOrderOut)
def get_sales_order(order_id: uuid.UUID, db: Session = Depends(get_db)):
    return _single_order_out(db, _get_order_or_404(db, order_id))


@router.patch("/{order_id}", response_model=SalesOrderOut)
def update_sales_order_status(order_id: uuid.UUID, body: SalesOrderStatusUpdate, db: Session = Depends(get_db)):
    order = _get_order_or_404(db, order_id)
    if order.status == "cancelled":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Order is cancelled")
    if body.status not in ("paid", "unpaid"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="status must be 'paid' or 'unpaid' -- use POST /sales-orders/{id}/cancel to cancel",
        )

    order.status = body.status
    db.commit()
    return _single_order_out(db, _get_order_or_404(db, order_id))


@router.post("/{order_id}/cancel", response_model=SalesOrderOut)
def cancel_sales_order(order_id: uuid.UUID, body: CancelRequest, db: Session = Depends(get_db)):
    order = _get_order_or_404(db, order_id)
    if order.status == "cancelled":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Order is already cancelled")

    for item in order.items:
        db.add(
            StockLedger(
                product_size_id=item.product_size_id,
                change_qty=item.qty,
                reason="return",
                ref_type="sales_order",
                ref_id=order.id,
                unit_hpp_snapshot=item.unit_hpp_snapshot,
                note=body.reason,
            )
        )

    order.status = "cancelled"
    db.commit()
    return _single_order_out(db, _get_order_or_404(db, order_id))
