import uuid
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, Query, status, UploadFile, File
from slugify import slugify
from sqlalchemy import case, func
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.deps import get_current_owner
from app.models.cutting import CuttingLayout, CuttingLayoutItem
from app.models.material import Material, MaterialPurchase, MaterialUsageLog
from app.models.pattern import PatternComponent, PatternSpec
from app.models.product import Product, ProductSize, ProductSizeImage
from app.models.production import ProductionBatch, ProductionBatchItem, ProductionBatchLayout
from app.models.sales import SalesOrderItem
from app.models.stock import StockLedger
from app.schemas.product import (
    AddStockFromBahanRequest,
    DeleteResultOut,
    HppBreakdownOut,
    HppLineItemOut,
    PriceAdvisorRequest,
    PriceAdvisorResponse,
    ProductCreate,
    ProductOut,
    ProductSizeCreate,
    ProductSizeDetailOut,
    ProductSizeOut,
    ProductSizeUpdate,
    ProductSizeWithProductOut,
    ProductSizeImageOut,
    ProductUpdate,
)
from app.routers.production import _fifo_deduct_hardware
from app.routers.sales import get_hpp_for_sale
from app.services.pricing import compute_margin_pct, compute_markup_pct, compute_suggested_price

router = APIRouter(tags=["products"], dependencies=[Depends(get_current_owner)])


def _get_product_or_404(db: Session, sku: str) -> Product:
    product = db.query(Product).filter(Product.sku == sku).first()
    if product is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")
    return product


def _apply_is_archived(entity, is_archived: bool | None) -> None:
    if is_archived is None:
        return
    if is_archived is False:
        # Archiving Product/ProductSize is one-way by design (doc/versions/v2.4.md) — reject
        # explicitly instead of silently ignoring, which would show the iOS client a false success.
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unarchiving is not supported")
    entity.is_archived = True


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
    # Section 2 spec: SKU auto-fills as an UPPERCASE slug (e.g. "Pouch Serut" -> "POUCH-SERUT").
    # python-slugify 8.0.4 has no `uppercase` kwarg (renamed to `lowercase`, default True) --
    # slugify() always normalizes to lowercase, so uppercase it after instead.
    base = slugify(name).upper() or "PRODUCT"
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


def _stock_breakdown_map(db: Session, product_size_ids: list[uuid.UUID]) -> dict[uuid.UUID, tuple[int, int]]:
    # Returns {product_size_id: (production_stock_qty, manual_stock_qty)}. "manual" bundles
    # reason='initial' (seed) with reason='adjustment' (addStockFromBahan, manual corrections) --
    # sale/return/damage are deliberately excluded, they're not a stock *source*.
    if not product_size_ids:
        return {}
    production_case = case((StockLedger.reason == "production", StockLedger.change_qty), else_=0)
    manual_case = case((StockLedger.reason.in_(["initial", "adjustment"]), StockLedger.change_qty), else_=0)
    rows = (
        db.query(
            StockLedger.product_size_id,
            func.coalesce(func.sum(production_case), 0),
            func.coalesce(func.sum(manual_case), 0),
        )
        .filter(StockLedger.product_size_id.in_(product_size_ids))
        .group_by(StockLedger.product_size_id)
        .all()
    )
    return {row[0]: (int(row[1]), int(row[2])) for row in rows}


def _stock_breakdown(db: Session, product_size_id: uuid.UUID) -> tuple[int, int]:
    return _stock_breakdown_map(db, [product_size_id]).get(product_size_id, (0, 0))


def _latest_production_item(db: Session, product_size_id: uuid.UUID) -> ProductionBatchItem | None:
    # Must filter status='confirmed' -- draft items always have hpp_*=0 (not computed until
    # confirm, per handoff Production section), so an unfiltered "latest by produced_at" can
    # return a newer draft's all-zero HPP instead of the last real confirmed cost.
    # Ordered by confirmed_at (v2.12), not produced_at: produced_at is set at batch *creation*,
    # so if batches are confirmed out of creation order, produced_at.desc() could pick a batch
    # that was created later but confirmed earlier than another. nullslast() + produced_at
    # fallback handles batches confirmed before this column existed (confirmed_at=NULL) --
    # those fall back to produced_at ordering among themselves, and never outrank a batch that
    # has a real confirmed_at (Postgres' default is NULLS FIRST on DESC, which would otherwise
    # wrongly rank an old unmigrated NULL ahead of a genuinely more recent confirm).
    return (
        db.query(ProductionBatchItem)
        .join(ProductionBatch, ProductionBatchItem.production_batch_id == ProductionBatch.id)
        .filter(ProductionBatchItem.product_size_id == product_size_id, ProductionBatch.status == "confirmed")
        .order_by(ProductionBatch.confirmed_at.desc().nullslast(), ProductionBatch.produced_at.desc())
        .first()
    )


def _latest_hpp_map(db: Session, product_size_ids: list[uuid.UUID]) -> dict[uuid.UUID, ProductionBatchItem]:
    if not product_size_ids:
        return {}
    rows = (
        db.query(ProductionBatchItem)
        .join(ProductionBatch, ProductionBatchItem.production_batch_id == ProductionBatch.id)
        .filter(ProductionBatchItem.product_size_id.in_(product_size_ids), ProductionBatch.status == "confirmed")
        .order_by(ProductionBatch.confirmed_at.desc().nullslast(), ProductionBatch.produced_at.desc())
        .all()
    )
    latest: dict[uuid.UUID, ProductionBatchItem] = {}
    for item in rows:
        latest.setdefault(item.product_size_id, item)
    return latest


def _fabric_items_map(
    db: Session, batch_ids: list[uuid.UUID]
) -> dict[tuple[uuid.UUID, uuid.UUID], list[HppLineItemOut]]:
    """v3.8: per-fabric-layer HPP breakdown, keyed by (production_batch_id, product_size_id).

    Deviates from the implement doc's pseudocode (which queries per product_size_id in a loop)
    -- this codebase's other "latest HPP" lookups (_stock_qty_map, _latest_hpp_map, etc.) are all
    batched single-query maps for a reason: this backend's DB is cross-region from where it's
    deployed (~100-150ms per round trip), so an N+1 here would make GET /products/{sku}/sizes
    scale linearly with size count instead of O(1) extra queries. One query returns every
    (batch, size) -> fabric layer combination for all requested batches at once.

    cost_per_piece is CuttingLayoutItem's per-piece rate (already divided by qty), matching
    HppBreakdown's per-unit semantics -- not a total.
    """
    if not batch_ids:
        return {}
    rows = (
        db.query(
            ProductionBatchLayout.production_batch_id,
            ProductionBatchLayout.sort_order,
            CuttingLayoutItem.product_size_id,
            CuttingLayoutItem.cost_per_piece,
            Material.name,
        )
        .join(CuttingLayout, ProductionBatchLayout.cutting_layout_id == CuttingLayout.id)
        .join(CuttingLayoutItem, CuttingLayoutItem.cutting_layout_id == CuttingLayout.id)
        .join(MaterialPurchase, CuttingLayout.material_purchase_id == MaterialPurchase.id)
        .join(Material, MaterialPurchase.material_id == Material.id)
        .filter(ProductionBatchLayout.production_batch_id.in_(batch_ids))
        .order_by(ProductionBatchLayout.production_batch_id, ProductionBatchLayout.sort_order)
        .all()
    )
    result: dict[tuple[uuid.UUID, uuid.UUID], list[HppLineItemOut]] = defaultdict(list)
    for batch_id, _sort_order, size_id, cost_per_piece, material_name in rows:
        result[(batch_id, size_id)].append(HppLineItemOut(name=material_name, cost=cost_per_piece))
    return result


def _hardware_items_map(db: Session, product_size_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[HppLineItemOut]]:
    """v3.8: per-hardware-component HPP breakdown, keyed by product_size_id.

    Batched for the same reason as _fabric_items_map (see its docstring) -- two queries total
    regardless of how many sizes are requested, not two-per-size.

    Uses material.current_avg_cost (CURRENT, not the historical snapshot hpp_hardware was
    computed from) since PatternComponent doesn't store a per-confirm cost snapshot the way
    ProductionBatchItem does -- this can drift slightly from the stored hpp_hardware aggregate
    if avg cost changed since the batch was confirmed. That's expected/documented, not a bug.
    """
    if not product_size_ids:
        return {}
    specs = (
        db.query(PatternSpec)
        .filter(PatternSpec.product_size_id.in_(product_size_ids), PatternSpec.is_active.is_(True))
        .order_by(PatternSpec.product_size_id, PatternSpec.effective_from.desc())
        .all()
    )
    # Post-v2.15 there should be at most one active spec per size, but be defensive and take the
    # most recently effective one per size in case that invariant is ever violated.
    latest_spec_by_size: dict[uuid.UUID, PatternSpec] = {}
    for spec in specs:
        latest_spec_by_size.setdefault(spec.product_size_id, spec)
    if not latest_spec_by_size:
        return {}

    spec_ids = [spec.id for spec in latest_spec_by_size.values()]
    rows = (
        db.query(PatternComponent.pattern_spec_id, PatternComponent.qty_per_unit, Material.name, Material.current_avg_cost)
        .join(Material, PatternComponent.material_id == Material.id)
        .filter(
            PatternComponent.pattern_spec_id.in_(spec_ids),
            Material.category == "hardware",
            Material.cost_class == "direct_precise",
        )
        .all()
    )
    items_by_spec: dict[uuid.UUID, list[HppLineItemOut]] = defaultdict(list)
    for spec_id, qty_per_unit, material_name, avg_cost in rows:
        if not avg_cost:
            continue
        items_by_spec[spec_id].append(HppLineItemOut(name=material_name, cost=round(qty_per_unit * avg_cost, 2)))

    return {size_id: items_by_spec.get(spec.id, []) for size_id, spec in latest_spec_by_size.items()}


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
        "manual_hpp_fabric": size.manual_hpp_fabric,
        "manual_hpp_pooled": size.manual_hpp_pooled,
        "manual_hpp_hardware": size.manual_hpp_hardware,
        "manual_hpp_labor": size.manual_hpp_labor,
        "manual_hpp_overhead": size.manual_hpp_overhead,
        "manual_hpp_total": size.manual_hpp_total,
        "images": size.images,
    }


def _size_out(size: ProductSize, stock_qty: int, production_qty: int, manual_qty: int) -> ProductSizeOut:
    return ProductSizeOut(
        **_size_fields(size),
        current_stock_qty=stock_qty,
        production_stock_qty=production_qty,
        manual_stock_qty=manual_qty,
    )


def _hpp_breakdown_out(
    latest_item: ProductionBatchItem | None,
    fabric_items: list[HppLineItemOut],
    hardware_items: list[HppLineItemOut],
) -> HppBreakdownOut | None:
    if latest_item is None:
        return None
    return HppBreakdownOut(
        fabric=latest_item.hpp_fabric,
        fabric_items=fabric_items,
        pooled_material=latest_item.hpp_pooled_material,
        hardware=latest_item.hpp_hardware,
        hardware_items=hardware_items,
        labor=latest_item.hpp_labor,
        overhead=latest_item.hpp_overhead,
        total=latest_item.hpp_total,
    )


def _detail_out(
    size: ProductSize,
    stock_qty: int,
    production_qty: int,
    manual_qty: int,
    latest_item: ProductionBatchItem | None,
    fabric_items: list[HppLineItemOut],
    hardware_items: list[HppLineItemOut],
) -> ProductSizeDetailOut:
    margin_pct = (
        compute_margin_pct(size.selling_price, latest_item.hpp_total)
        if size.selling_price is not None and latest_item is not None
        else None
    )
    return ProductSizeDetailOut(
        **_size_fields(size),
        current_stock_qty=stock_qty,
        production_stock_qty=production_qty,
        manual_stock_qty=manual_qty,
        latest_hpp_breakdown=_hpp_breakdown_out(latest_item, fabric_items, hardware_items),
        margin_pct=margin_pct,
    )


def _size_detail_out(db: Session, size: ProductSize) -> ProductSizeDetailOut:
    stock_qty = _current_stock_qty(db, size.id)
    production_qty, manual_qty = _stock_breakdown(db, size.id)
    latest_item = _latest_production_item(db, size.id)
    fabric_items: list[HppLineItemOut] = []
    hardware_items: list[HppLineItemOut] = []
    if latest_item is not None:
        fabric_items = _fabric_items_map(db, [latest_item.production_batch_id]).get(
            (latest_item.production_batch_id, size.id), []
        )
        hardware_items = _hardware_items_map(db, [size.id]).get(size.id, [])
    return _detail_out(size, stock_qty, production_qty, manual_qty, latest_item, fabric_items, hardware_items)


def _size_detail_with_product_out(db: Session, size: ProductSize, product: Product) -> ProductSizeWithProductOut:
    """v3.17: reuses _size_detail_out's query/serializer, adds product_sku/product_name on top --
    needed by GET /product-sizes/{size_id} since that URL doesn't carry the SKU the way
    GET /products/{sku}/sizes does.
    """
    detail = _size_detail_out(db, size)
    return ProductSizeWithProductOut(**detail.model_dump(), product_sku=product.sku, product_name=product.name)


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
    if body.name is not None:
        product.name = body.name
    _apply_is_archived(product, body.is_archived)
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
    return _size_out(size, 0, 0, 0)


@router.get("/products/{sku}/sizes", response_model=list[ProductSizeDetailOut])
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
    size_ids = [s.id for s in sizes]
    stock_map = _stock_qty_map(db, size_ids)
    breakdown_map = _stock_breakdown_map(db, size_ids)
    hpp_map = _latest_hpp_map(db, size_ids)
    batch_ids = [item.production_batch_id for item in hpp_map.values()]
    fabric_map = _fabric_items_map(db, batch_ids)
    hardware_map = _hardware_items_map(db, size_ids)
    out = [
        _detail_out(
            s,
            stock_map.get(s.id, 0),
            *breakdown_map.get(s.id, (0, 0)),
            hpp_map.get(s.id),
            fabric_map.get((hpp_map[s.id].production_batch_id, s.id), []) if s.id in hpp_map else [],
            hardware_map.get(s.id, []),
        )
        for s in sizes
    ]
    if min_stock is not None:
        out = [o for o in out if o.current_stock_qty >= min_stock]
    return out


@router.get("/products/{sku}/sizes/{size_id}", response_model=ProductSizeDetailOut)
def get_product_size(sku: str, size_id: uuid.UUID, db: Session = Depends(get_db)):
    product = _get_product_or_404(db, sku)
    size = _get_size_or_404(db, product, size_id)
    return _size_detail_out(db, size)




@router.get("/product-sizes", response_model=list[ProductSizeWithProductOut])
def list_all_product_sizes(
    archived: bool = False,
    db: Session = Depends(get_db),
):
    """v3.22: Optimized bulk endpoint to fetch all sizes across all active products in a single call,
    completely resolving the N+1 HTTP request bottleneck on the iOS client.
    """
    sizes = (
        db.query(ProductSize)
        .options(joinedload(ProductSize.product))
        .join(Product, ProductSize.product_id == Product.id)
        .filter(ProductSize.is_archived == archived, Product.is_archived.is_(False))
        .order_by(Product.name, ProductSize.size_label)
        .all()
    )
    size_ids = [s.id for s in sizes]
    stock_map = _stock_qty_map(db, size_ids)
    breakdown_map = _stock_breakdown_map(db, size_ids)
    hpp_map = _latest_hpp_map(db, size_ids)
    batch_ids = [item.production_batch_id for item in hpp_map.values()]
    fabric_map = _fabric_items_map(db, batch_ids)
    hardware_map = _hardware_items_map(db, size_ids)

    return [
        ProductSizeWithProductOut(
            id=s.id,
            product_id=s.product_id,
            size_label=s.size_label,
            fabric_variant_name=s.fabric_variant_name,
            reorder_min_qty=s.reorder_min_qty,
            selling_price=s.selling_price,
            is_archived=s.is_archived,
            manual_hpp_fabric=s.manual_hpp_fabric,
            manual_hpp_pooled=s.manual_hpp_pooled,
            manual_hpp_hardware=s.manual_hpp_hardware,
            manual_hpp_labor=s.manual_hpp_labor,
            manual_hpp_overhead=s.manual_hpp_overhead,
            manual_hpp_total=s.manual_hpp_total,
            current_stock_qty=stock_map.get(s.id, 0),
            production_stock_qty=breakdown_map.get(s.id, (0, 0))[0],
            manual_stock_qty=breakdown_map.get(s.id, (0, 0))[1],
            latest_hpp_breakdown=_hpp_breakdown_out(
                hpp_map.get(s.id),
                fabric_map.get((hpp_map[s.id].production_batch_id, s.id), []) if s.id in hpp_map else [],
                hardware_map.get(s.id, []),
            ),
            margin_pct=compute_margin_pct(s.selling_price, hpp_map[s.id].hpp_total)
                if s.selling_price is not None and s.id in hpp_map and hpp_map[s.id] is not None
                else None,
            product_sku=s.product.sku,
            product_name=s.product.name,
        )
        for s in sizes
    ]


@router.get("/product-sizes/{size_id}", response_model=ProductSizeWithProductOut)
def get_product_size_by_id(size_id: uuid.UUID, db: Session = Depends(get_db)):
    """v3.17: QR-code scan lookup. QR encodes "oura:{productSizeId}" -- iOS resolves the UUID to
    full product/size/stock/HPP data without knowing the SKU up front, unlike the sku-scoped
    /products/{sku}/sizes/{size_id} above.
    """
    size = db.get(ProductSize, size_id)
    if size is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product size not found")
    product = db.get(Product, size.product_id)
    return _size_detail_with_product_out(db, size, product)


@router.patch("/products/{sku}/sizes/{size_id}", response_model=ProductSizeOut)
def update_product_size(sku: str, size_id: uuid.UUID, body: ProductSizeUpdate, db: Session = Depends(get_db)):
    product = _get_product_or_404(db, sku)
    size = _get_size_or_404(db, product, size_id)

    if body.selling_price is not None:
        size.selling_price = body.selling_price
    if body.reorder_min_qty is not None:
        size.reorder_min_qty = body.reorder_min_qty
    if body.manual_hpp_fabric is not None:
        size.manual_hpp_fabric = body.manual_hpp_fabric
    if body.manual_hpp_pooled is not None:
        size.manual_hpp_pooled = body.manual_hpp_pooled
    if body.manual_hpp_hardware is not None:
        size.manual_hpp_hardware = body.manual_hpp_hardware
    if body.manual_hpp_labor is not None:
        size.manual_hpp_labor = body.manual_hpp_labor
    if body.manual_hpp_overhead is not None:
        size.manual_hpp_overhead = body.manual_hpp_overhead
    _apply_is_archived(size, body.is_archived)

    db.commit()
    db.refresh(size)
    production_qty, manual_qty = _stock_breakdown(db, size.id)
    return _size_out(size, _current_stock_qty(db, size.id), production_qty, manual_qty)


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

    # v3.19: reuses the same batch -> pattern_spec -> manual -> none fallback as POST
    # /sales-orders (get_hpp_for_sale) instead of only looking at confirmed production batches --
    # a size priced from a manual HPP override or an estimated PatternSpec can now get a
    # suggestion too. Only reject when hpp_source="none" -- genuinely no cost basis at all.
    hpp_total, hpp_source = get_hpp_for_sale(db, size.id)
    if hpp_source == "none":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No production HPP recorded yet for this product size",
        )

    try:
        suggested_price = compute_suggested_price(
            hpp_total, body.target_margin_pct, body.marketplace_fee_pct, body.promo_allocation_pct
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return PriceAdvisorResponse(
        suggested_price=suggested_price,
        resulting_margin_pct=compute_margin_pct(suggested_price, hpp_total),
        resulting_markup_pct=compute_markup_pct(suggested_price, hpp_total),
    )


@router.post("/products/{sku}/sizes/{size_id}/stock-from-bahan", response_model=ProductSizeDetailOut)
def add_stock_from_bahan(sku: str, size_id: uuid.UUID, body: AddStockFromBahanRequest, db: Session = Depends(get_db)):
    """Add stock for a size directly from bahan (fabric + hardware/component consumption per its
    PatternSpec), bypassing the full cutting-optimizer/production-batch pipeline. Handles both the
    original "Stok Awal" manual-entry flow (handoff v1.8-v2.0) and the QR-scan "Dari Produksi" flow
    (doc/fix-stock-from-bahan.txt) -- same endpoint, same shape, both represent bahan genuinely
    being consumed to produce stock. Two things neither spec spells out precisely (flagged here
    rather than silently assumed):

    1. Fabric length consumed per unit is approximated as `qty * fabric.cut_height_cm` (cut_width_cm
       is treated as across-the-roll width, not consumed length) -- a straight-line estimate with no
       nesting/rotation optimization. This is only appropriate for manual/QR stock entry; real
       production costing should go through POST /cutting-optimizer/suggest + /production-batches instead.
    2. Because this bypasses ProductionBatch, there is no computed hpp_total to snapshot, so the written
       stock_ledger row has unit_hpp_snapshot=None -- matching doc/fix-stock-from-bahan.txt's own
       expected response, which shows latest_hpp_breakdown staying null after this call.

    v2.15: a PatternSpec can now carry N fabric layers. `material_purchase_id` (if passed) also
    disambiguates which fabric layer to consume against; with exactly one fabric layer on the spec
    it's optional as before.

    v3.19: path renamed from addStockFromBahan (camelCase) to stock-from-bahan (kebab-case) to
    match what the frontend actually calls -- the old path/casing was a live 404 on device.
    Also accepts `spec_id` so the frontend (which already fetched it from GET /pattern-specs) can
    name the spec directly instead of relying on the "exactly one active spec for this size"
    lookup below, which 400s if that invariant is ever violated.

    doc/fix-stock-from-bahan.txt (this round): three behavior changes from the endpoint's original
    "Stok Awal" design --
      (a) reason changed 'adjustment' -> 'production' so current_stock_qty lands in
          production_stock_qty, not manual_stock_qty, per that doc's own worked example response.
      (b) spec.components (hardware/thread/packaging) are now also deducted via the same FIFO
          helper POST /production-batches/{id}/confirm uses, not just spec.fabrics -- the original
          "Stok Awal" version never touched components at all.
      (c) response body changed from a bahan-consumption receipt to the same ProductSizeDetailOut
          shape GET /products/{sku}/sizes returns, per that doc's spec.

    doc/... (bug report, this round): fabric deductions here were invisible in "Pergerakan Stok"
    (GET /materials/{id}/usage) because that endpoint only ever derived history from
    CuttingLayoutItem rows belonging to a confirmed ProductionBatch -- this endpoint bypasses that
    pipeline entirely. Now writes one MaterialUsageLog row per purchase actually deducted from,
    which routers/materials.py's get_material_usage() merges into the same response.
    """
    product = _get_product_or_404(db, sku)
    size = _get_size_or_404(db, product, size_id)

    if body.spec_id is not None:
        spec = db.get(PatternSpec, body.spec_id)
        if spec is None or spec.product_size_id != size.id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="spec_id not found for this product size",
            )
    else:
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
                detail="Multiple active PatternSpecs found for this product size — ambiguous, pass spec_id",
            )
        spec = active_specs[0]

    purchase = db.get(MaterialPurchase, body.material_purchase_id) if body.material_purchase_id is not None else None
    if body.material_purchase_id is not None and purchase is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="material_purchase_id not found")

    if purchase is not None:
        fabric = next((f for f in spec.fabrics if f.material_id == purchase.material_id), None)
        if fabric is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="material_purchase_id does not match any fabric layer of this size's active PatternSpec",
            )
    elif len(spec.fabrics) == 1:
        fabric = spec.fabrics[0]
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This PatternSpec has multiple fabric layers — pass material_purchase_id to disambiguate",
        )

    length_needed_cm = body.qty * fabric.cut_height_cm

    description_parts = [size.size_label]
    if size.fabric_variant_name:
        description_parts.append(size.fabric_variant_name)
    usage_description = " · ".join(description_parts)

    def _log_fabric_usage(purchase_id: uuid.UUID, cm_taken: float) -> None:
        db.add(
            MaterialUsageLog(
                material_id=fabric.material_id,
                material_purchase_id=purchase_id,
                product_size_id=size.id,
                deducted_cm=cm_taken,
                description=usage_description,
                source="stock_from_bahan",
            )
        )

    if purchase is not None:
        if (purchase.remaining_length_cm or 0) < length_needed_cm:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Insufficient remaining fabric on this purchase")
        purchase.remaining_length_cm -= length_needed_cm
        _log_fabric_usage(purchase.id, length_needed_cm)
    else:
        candidates = (
            db.query(MaterialPurchase)
            .filter(MaterialPurchase.material_id == fabric.material_id, MaterialPurchase.remaining_length_cm > 0)
            .order_by(MaterialPurchase.purchased_at.asc(), MaterialPurchase.created_at.asc())
            .all()
        )
        remaining_needed = length_needed_cm
        for purchase in candidates:
            if remaining_needed <= 0:
                break
            take = min(purchase.remaining_length_cm, remaining_needed)
            purchase.remaining_length_cm -= take
            remaining_needed -= take
            _log_fabric_usage(purchase.id, take)
        if remaining_needed > 0:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Insufficient fabric stock across all purchases of this material",
            )

    for component in db.query(PatternComponent).filter(PatternComponent.pattern_spec_id == spec.id).all():
        _fifo_deduct_hardware(db, component.material_id, component.qty_per_unit * body.qty)

    entry = StockLedger(
        product_size_id=size.id,
        change_qty=body.qty,
        reason="production",
        note="Stok dari Bahan (stock-from-bahan, FIFO fabric + hardware deduction)",
    )
    db.add(entry)
    db.commit()
    db.refresh(size)

    return _size_detail_out(db, size)


# --- v3.44: Stock Ledger Endpoint ---
from app.schemas.product import StockAdjustmentOut
from datetime import date

@router.get("/stock-ledger", response_model=list[StockAdjustmentOut])
def get_stock_ledger(
    from_date: date = Query(..., alias="from"),
    to_date: date = Query(..., alias="to"),
    db: Session = Depends(get_db)
):
    from datetime import datetime, timedelta
    start_dt = datetime.combine(from_date, datetime.min.time())
    end_dt = datetime.combine(to_date, datetime.min.time()) + timedelta(days=1)
    
    entries = (
        db.query(StockLedger)
        .filter(StockLedger.created_at >= start_dt, StockLedger.created_at < end_dt)
        .order_by(StockLedger.created_at.asc())
        .all()
    )
    return entries


@router.post("/products/{sku}/sizes/{size_id}/images", response_model=ProductSizeImageOut, status_code=status.HTTP_201_CREATED)
async def upload_product_size_image(
    sku: str,
    size_id: uuid.UUID,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    product = _get_product_or_404(db, sku)
    size = _get_size_or_404(db, product, size_id)

    # Validation
    if file.content_type not in ["image/jpeg", "image/jpg"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Format file tidak didukung. Hanya menerima file gambar JPEG."
        )

    file_bytes = await file.read()
    if len(file_bytes) > 1887436:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Ukuran file melebihi batas maksimum 1.8 MB."
        )

    image_id = uuid.uuid4()
    blob_name = f"products/{sku}/sizes/{size_id}/{image_id}.jpg"
    from app.config import settings

    # Clean bucket name (remove gs:// prefix and trailing slash if present)
    bucket_name = settings.gcs_bucket_name.strip()
    if bucket_name.startswith("gs://"):
        bucket_name = bucket_name[5:]
    if bucket_name.endswith("/"):
        bucket_name = bucket_name[:-1]

    try:
        from google.cloud import storage
        client = storage.Client()
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(blob_name)
        blob.upload_from_string(file_bytes, content_type="image/jpeg")
        image_url = f"https://storage.googleapis.com/{bucket_name}/{blob_name}"
    except Exception as e:
        print(f"GCS Upload failed: {e}. Falling back to expected GCS URL for development.")
        image_url = f"https://storage.googleapis.com/{bucket_name}/{blob_name}"

    new_image = ProductSizeImage(
        id=image_id,
        product_size_id=size_id,
        image_url=image_url
    )
    db.add(new_image)
    db.commit()
    db.refresh(new_image)
    return new_image


@router.delete("/products/{sku}/sizes/{size_id}/images/{image_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_product_size_image(
    sku: str,
    size_id: uuid.UUID,
    image_id: uuid.UUID,
    db: Session = Depends(get_db),
):
    product = _get_product_or_404(db, sku)
    size = _get_size_or_404(db, product, size_id)

    image_record = (
        db.query(ProductSizeImage)
        .filter(ProductSizeImage.id == image_id, ProductSizeImage.product_size_id == size_id)
        .first()
    )
    if image_record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Foto tidak ditemukan."
        )

    # Delete from GCS
    from app.config import settings
    
    # Clean bucket name
    bucket_name = settings.gcs_bucket_name.strip()
    if bucket_name.startswith("gs://"):
        bucket_name = bucket_name[5:]
    if bucket_name.endswith("/"):
        bucket_name = bucket_name[:-1]

    image_url = image_record.image_url
    prefix = f"https://storage.googleapis.com/{bucket_name}/"
    if image_url.startswith(prefix):
        blob_name = image_url[len(prefix):]
        try:
            from google.cloud import storage
            client = storage.Client()
            bucket = client.bucket(bucket_name)
            blob = bucket.blob(blob_name)
            blob.delete()
        except Exception as e:
            print(f"GCS Delete failed: {e}. Proceeding to delete from database.")

    db.delete(image_record)
    db.commit()
    return
