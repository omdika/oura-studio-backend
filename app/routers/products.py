import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from slugify import slugify
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_owner
from app.models.material import MaterialPurchase
from app.models.pattern import PatternSpec
from app.models.product import Product, ProductSize
from app.models.production import ProductionBatch, ProductionBatchItem
from app.models.sales import SalesOrderItem
from app.models.stock import StockLedger
from app.schemas.product import (
    AddStockFromBahanRequest,
    AddStockFromBahanResponse,
    DeleteResultOut,
    HppBreakdownOut,
    PriceAdvisorRequest,
    PriceAdvisorResponse,
    ProductCreate,
    ProductOut,
    ProductSizeCreate,
    ProductSizeDetailOut,
    ProductSizeOut,
    ProductSizeUpdate,
    ProductUpdate,
    PurchaseConsumedOut,
)
from app.services.pricing import compute_margin_pct, compute_markup_pct, compute_suggested_price

router = APIRouter(tags=["products"], dependencies=[Depends(get_current_owner)])


def _get_product_or_404(db: Session, sku: str) -> Product:
    product = db.query(Product).filter(Product.sku == sku).first()
    if product is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")
    return product


def _get_size_or_404(db: Session, product: Product, size_id: uuid.UUID) -> ProductSize:
    size = (
        db.query(ProductSize)
        .filter(ProductSize.id == size_id, ProductSize.product_id == product.id)
        .first()
    )
    if size is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product size not found")
    return size


def _generate_unique_sku(db: Session, name: str) -> str:
    base = slugify(name) or "product"
    sku = base
    suffix = 2
    while db.query(Product).filter(Product.sku == sku).first() is not None:
        sku = f"{base}-{suffix}"
        suffix += 1
    return sku


def _stock_qty_map(db: Session, product_size_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
    if not product_size_ids:
        return {}
    rows = (
        db.query(StockLedger.product_size_id, func.coalesce(func.sum(StockLedger.change_qty), 0))
        .filter(StockLedger.product_size_id.in_(product_size_ids))
        .group_by(StockLedger.product_size_id)
        .all()
    )
    return {row[0]: int(row[1]) for row in rows}


def _current_stock_qty(db: Session, product_size_id: uuid.UUID) -> int:
    total = (
        db.query(func.coalesce(func.sum(StockLedger.change_qty), 0))
        .filter(StockLedger.product_size_id == product_size_id)
        .scalar()
    )
    return int(total)


def _latest_production_item(db: Session, product_size_id: uuid.UUID) -> ProductionBatchItem | None:
    return (
        db.query(ProductionBatchItem)
        .join(ProductionBatch, ProductionBatchItem.production_batch_id == ProductionBatch.id)
        .filter(ProductionBatchItem.product_size_id == product_size_id)
        .order_by(ProductionBatch.produced_at.desc())
        .first()
    )


def _product_size_has_history(db: Session, product_size_id: uuid.UUID) -> bool:
    has_pattern_spec = (
        db.query(PatternSpec.id).filter(PatternSpec.product_size_id == product_size_id).first() is not None
    )
    has_stock_movement = (
        db.query(StockLedger.id).filter(StockLedger.product_size_id == product_size_id).first() is not None
    )
    has_sale = (
        db.query(SalesOrderItem.id).filter(SalesOrderItem.product_size_id == product_size_id).first()
        is not None
    )
    return has_pattern_spec or has_stock_movement or has_sale


def _size_fields(size: ProductSize) -> dict:
    return {
        "id": size.id,
        "product_id": size.product_id,
        "size_label": size.size_label,
        "fabric_variant_name": size.fabric_variant_name,
        "reorder_min_qty": size.reorder_min_qty,
        "selling_price": size.selling_price,
        "is_archived": size.is_archived,
    }


def _size_out(size: ProductSize, stock_qty: int) -> ProductSizeOut:
    return ProductSizeOut(**_size_fields(size), current_stock_qty=stock_qty)


def _size_detail_out(db: Session, size: ProductSize) -> ProductSizeDetailOut:
    stock_qty = _current_stock_qty(db, size.id)
    latest_item = _latest_production_item(db, size.id)
    hpp_breakdown = HppBreakdownOut.model_validate(latest_item, from_attributes=True) if latest_item else None
    margin_pct = (
        compute_margin_pct(size.selling_price, latest_item.hpp_total)
        if size.selling_price is not None and latest_item is not None
        else None
    )
    return ProductSizeDetailOut(
        **_size_fields(size),
        current_stock_qty=stock_qty,
        latest_hpp_breakdown=hpp_breakdown,
        margin_pct=margin_pct,
    )


@router.post("/products", response_model=ProductOut, status_code=status.HTTP_201_CREATED)
def create_product(body: ProductCreate, db: Session = Depends(get_db)):
    if body.sku is not None:
        if db.query(Product).filter(Product.sku == body.sku).first() is not None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="sku already exists")
        sku = body.sku
    else:
        sku = _generate_unique_sku(db, body.name)

    product = Product(sku=sku, name=body.name)
    db.add(product)
    db.commit()
    db.refresh(product)
    return product


@router.get("/products", response_model=list[ProductOut])
def list_products(
    include_archived: bool = False,
    search: str | None = None,
    limit: int = Query(default=50, le=200),
    offset: int = 0,
    db: Session = Depends(get_db),
):
    q = db.query(Product)
    if not include_archived:
        q = q.filter(Product.is_archived.is_(False))
    if search:
        q = q.filter(Product.name.ilike(f"%{search}%"))
    return q.order_by(Product.name).offset(offset).limit(limit).all()


@router.patch("/products/{sku}", response_model=ProductOut)
def update_product(sku: str, body: ProductUpdate, db: Session = Depends(get_db)):
    product = _get_product_or_404(db, sku)
    product.name = body.name
    db.commit()
    db.refresh(product)
    return product


@router.delete("/products/{sku}", response_model=DeleteResultOut)
def delete_product(sku: str, db: Session = Depends(get_db)):
    product = _get_product_or_404(db, sku)
    sizes = db.query(ProductSize).filter(ProductSize.product_id == product.id).all()

    any_size_kept = False
    for size in sizes:
        if _product_size_has_history(db, size.id):
            size.is_archived = True
            any_size_kept = True
        else:
            db.delete(size)

    db.flush()

    if any_size_kept:
        product.is_archived = True
        db.commit()
        return DeleteResultOut(deleted=False)

    db.delete(product)
    db.commit()
    return DeleteResultOut(deleted=True)


@router.post(
    "/products/{sku}/sizes", response_model=ProductSizeOut, status_code=status.HTTP_201_CREATED
)
def create_product_size(sku: str, body: ProductSizeCreate, db: Session = Depends(get_db)):
    product = _get_product_or_404(db, sku)

    # Postgres UNIQUE(product_id, size_label, fabric_variant_name) does not catch duplicate NULLs
    # (NULL != NULL), so the fabric_variant_name IS NULL case must be checked at the application layer.
    duplicate_q = db.query(ProductSize).filter(
        ProductSize.product_id == product.id, ProductSize.size_label == body.size_label
    )
    if body.fabric_variant_name is None:
        duplicate_q = duplicate_q.filter(ProductSize.fabric_variant_name.is_(None))
    else:
        duplicate_q = duplicate_q.filter(ProductSize.fabric_variant_name == body.fabric_variant_name)
    if duplicate_q.first() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A ProductSize with this size_label + fabric_variant_name already exists",
        )

    size = ProductSize(
        product_id=product.id,
        size_label=body.size_label,
        fabric_variant_name=body.fabric_variant_name,
        reorder_min_qty=body.reorder_min_qty,
    )
    db.add(size)
    db.commit()
    db.refresh(size)
    return _size_out(size, 0)


@router.get("/products/{sku}/sizes", response_model=list[ProductSizeOut])
def list_product_sizes(
    sku: str,
    archived: bool = False,
    min_stock: int | None = None,
    db: Session = Depends(get_db),
):
    product = _get_product_or_404(db, sku)
    sizes = (
        db.query(ProductSize)
        .filter(ProductSize.product_id == product.id, ProductSize.is_archived == archived)
        .order_by(ProductSize.size_label)
        .all()
    )
    stock_map = _stock_qty_map(db, [s.id for s in sizes])
    out = [_size_out(s, stock_map.get(s.id, 0)) for s in sizes]
    if min_stock is not None:
        out = [o for o in out if o.current_stock_qty >= min_stock]
    return out


@router.get("/products/{sku}/sizes/{size_id}", response_model=ProductSizeDetailOut)
def get_product_size(sku: str, size_id: uuid.UUID, db: Session = Depends(get_db)):
    product = _get_product_or_404(db, sku)
    size = _get_size_or_404(db, product, size_id)
    return _size_detail_out(db, size)


@router.patch("/products/{sku}/sizes/{size_id}", response_model=ProductSizeOut)
def update_product_size(sku: str, size_id: uuid.UUID, body: ProductSizeUpdate, db: Session = Depends(get_db)):
    product = _get_product_or_404(db, sku)
    size = _get_size_or_404(db, product, size_id)

    if body.selling_price is not None:
        size.selling_price = body.selling_price
    if body.reorder_min_qty is not None:
        size.reorder_min_qty = body.reorder_min_qty

    db.commit()
    db.refresh(size)
    return _size_out(size, _current_stock_qty(db, size.id))


@router.delete("/products/{sku}/sizes/{size_id}", response_model=DeleteResultOut)
def delete_product_size(sku: str, size_id: uuid.UUID, db: Session = Depends(get_db)):
    product = _get_product_or_404(db, sku)
    size = _get_size_or_404(db, product, size_id)

    if _product_size_has_history(db, size.id):
        size.is_archived = True
        db.commit()
        return DeleteResultOut(deleted=False)

    db.delete(size)
    db.commit()
    return DeleteResultOut(deleted=True)


@router.post("/products/{sku}/sizes/{size_id}/price-advisor", response_model=PriceAdvisorResponse)
def price_advisor(sku: str, size_id: uuid.UUID, body: PriceAdvisorRequest, db: Session = Depends(get_db)):
    product = _get_product_or_404(db, sku)
    size = _get_size_or_404(db, product, size_id)

    latest_item = _latest_production_item(db, size.id)
    if latest_item is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No production HPP recorded yet for this product size",
        )

    try:
        suggested_price = compute_suggested_price(
            latest_item.hpp_total, body.target_margin_pct, body.marketplace_fee_pct, body.promo_allocation_pct
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return PriceAdvisorResponse(
        suggested_price=suggested_price,
        resulting_margin_pct=compute_margin_pct(suggested_price, latest_item.hpp_total),
        resulting_markup_pct=compute_markup_pct(suggested_price, latest_item.hpp_total),
    )


@router.post("/products/{sku}/sizes/{size_id}/addStockFromBahan", response_model=AddStockFromBahanResponse)
def add_stock_from_bahan(sku: str, size_id: uuid.UUID, body: AddStockFromBahanRequest, db: Session = Depends(get_db)):
    """Optional endpoint (handoff v1.8-v2.0): seed initial stock for a new fabric variant directly
    from an existing bahan (fabric) purchase, bypassing the full cutting-optimizer/production-batch
    pipeline. Two things the handoff never spells out (flagged here rather than silently assumed):

    1. Fabric length consumed per unit is approximated as `qty * pattern_spec.cut_height_cm` (cut_width_cm
       is treated as across-the-roll width, not consumed length) -- a straight-line estimate with no
       nesting/rotation optimization. This is only appropriate for manual initial-stock entry; real
       production costing should go through POST /cutting-optimizer/suggest + /production-batches instead.
    2. Because this bypasses ProductionBatch, there is no computed hpp_total to snapshot, so the written
       stock_ledger row has unit_hpp_snapshot=None and reason='adjustment' (not 'production').
    """
    product = _get_product_or_404(db, sku)
    size = _get_size_or_404(db, product, size_id)

    active_specs = db.query(PatternSpec).filter(
        PatternSpec.product_size_id == size.id, PatternSpec.is_active.is_(True)
    ).all()
    if not active_specs:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No active PatternSpec for this product size — cannot compute fabric consumption",
        )
    if len(active_specs) > 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Multiple active PatternSpecs found for this product size — ambiguous, pass material_purchase_id",
        )
    spec = active_specs[0]

    length_needed_cm = body.qty * spec.cut_height_cm

    if body.material_purchase_id is not None:
        purchase = db.get(MaterialPurchase, body.material_purchase_id)
        if purchase is None or purchase.material_id != spec.fabric_material_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="material_purchase_id not found or does not match this size's fabric material",
            )
        if (purchase.remaining_length_cm or 0) < length_needed_cm:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Insufficient remaining fabric on this purchase")
        purchase.remaining_length_cm -= length_needed_cm
        consumed = [PurchaseConsumedOut(material_purchase_id=purchase.id, length_cm_consumed=length_needed_cm)]
    else:
        candidates = (
            db.query(MaterialPurchase)
            .filter(MaterialPurchase.material_id == spec.fabric_material_id, MaterialPurchase.remaining_length_cm > 0)
            .order_by(MaterialPurchase.purchased_at.asc(), MaterialPurchase.created_at.asc())
            .all()
        )
        remaining_needed = length_needed_cm
        consumed = []
        for purchase in candidates:
            if remaining_needed <= 0:
                break
            take = min(purchase.remaining_length_cm, remaining_needed)
            purchase.remaining_length_cm -= take
            remaining_needed -= take
            consumed.append(PurchaseConsumedOut(material_purchase_id=purchase.id, length_cm_consumed=take))
        if remaining_needed > 0:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Insufficient fabric stock across all purchases of this material",
            )

    entry = StockLedger(
        product_size_id=size.id,
        change_qty=body.qty,
        reason="adjustment",
        note="Initial stock dari Bahan (addStockFromBahan, FIFO fabric deduction)",
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)

    return AddStockFromBahanResponse(
        stock_ledger_id=entry.id,
        change_qty=body.qty,
        fabric_length_consumed_cm=length_needed_cm,
        purchases_consumed=consumed,
    )
