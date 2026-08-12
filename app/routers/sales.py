import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.deps import get_current_owner
from app.models.product import Product, ProductSize
from app.models.sales import SalesOrder, SalesOrderItem
from app.models.stock import StockLedger
from app.schemas.sales import (
    CancelRequest,
    SalesOrderCreate,
    SalesOrderItemOut,
    SalesOrderOut,
    SalesOrderStatusUpdate,
)

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
    row = (
        db.query(StockLedger)
        .filter(StockLedger.product_size_id == product_size_id, StockLedger.unit_hpp_snapshot.isnot(None))
        .order_by(StockLedger.created_at.desc())
        .first()
    )
    return row.unit_hpp_snapshot if row else None


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

        hpp_snapshot = _latest_hpp_snapshot(db, size.id)
        if hpp_snapshot is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"No HPP recorded yet for product_size_id {line.product_size_id} -- cannot compute profit",
            )

        line_profit = (line.unit_price - line.discount - hpp_snapshot) * line.qty
        resolved_items.append((line, hpp_snapshot, line_profit))

    order = SalesOrder(
        invoice_no=_generate_invoice_no(db),
        customer_name=body.customer_name,
        payment_method=body.payment_method,
        marketplace_fee_pct=body.marketplace_fee_pct,
        status="unpaid",
    )
    db.add(order)
    db.flush()

    for line, hpp_snapshot, line_profit in resolved_items:
        db.add(
            SalesOrderItem(
                sales_order_id=order.id,
                product_size_id=line.product_size_id,
                qty=line.qty,
                unit_price=line.unit_price,
                discount=line.discount,
                unit_hpp_snapshot=hpp_snapshot,
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
