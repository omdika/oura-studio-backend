import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_owner
from app.models.cutting import CuttingLayout, CuttingLayoutItem
from app.models.material import Material, MaterialPurchase, Supplier
from app.models.product import Product, ProductSize
from app.models.production import ProductionBatch, ProductionBatchLayout
from app.schemas.material import (
    MaterialCreate,
    MaterialOut,
    MaterialPurchaseCreate,
    MaterialPurchaseOut,
    MaterialPurchaseUpdate,
    MaterialUpdate,
    MaterialUsageEntryOut,
)
from app.services.material_cost import (
    compute_weighted_avg_cost,
    is_purchase_consumed,
    purchase_quantity,
)

router = APIRouter(prefix="/materials", tags=["materials"], dependencies=[Depends(get_current_owner)])

# fabric/hardware track exact cost per unit via PatternSpec+CuttingLayout; thread/packaging are pooled flat-rate
_COST_CLASS_BY_CATEGORY = {
    "fabric": "direct_precise",
    "hardware": "direct_precise",
    "thread": "direct_pooled",
    "packaging": "direct_pooled",
}


def _recalc_avg_cost(db: Session, material: Material) -> None:
    purchases = db.query(MaterialPurchase).filter(MaterialPurchase.material_id == material.id).all()
    pairs = [
        (p.total_cost, purchase_quantity(material.category, p.length_cm, p.qty)) for p in purchases
    ]
    material.current_avg_cost = compute_weighted_avg_cost(pairs)


def _resolve_supplier(db: Session, supplier_id: uuid.UUID | None, supplier_name: str | None) -> uuid.UUID | None:
    if supplier_id is not None:
        supplier = db.get(Supplier, supplier_id)
        if supplier is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="supplier_id not found")
        return supplier.id
    if supplier_name is not None:
        existing = (
            db.query(Supplier).filter(func.lower(Supplier.name) == supplier_name.strip().lower()).first()
        )
        if existing:
            return existing.id
        new_supplier = Supplier(name=supplier_name.strip())
        db.add(new_supplier)
        db.flush()
        return new_supplier.id
    return None


def _validate_purchase_future_date(purchased_at: date | None) -> None:
    if purchased_at is not None and purchased_at > date.today():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="purchased_at cannot be a future date")


def _purchase_out(material: Material, purchase: MaterialPurchase) -> MaterialPurchaseOut:
    consumed = is_purchase_consumed(
        material.category, purchase.length_cm, purchase.remaining_length_cm, purchase.qty, purchase.remaining_qty
    )
    return MaterialPurchaseOut(
        id=purchase.id,
        material_id=purchase.material_id,
        width_cm=purchase.width_cm,
        length_cm=purchase.length_cm,
        qty=purchase.qty,
        package_label=purchase.package_label,
        total_cost=purchase.total_cost,
        supplier_id=purchase.supplier_id,
        purchased_at=purchase.purchased_at,
        remaining_length_cm=purchase.remaining_length_cm,
        remaining_qty=purchase.remaining_qty,
        created_at=purchase.created_at,
        is_consumed=consumed,
    )


def _get_material_or_404(db: Session, material_id: uuid.UUID) -> Material:
    material = db.get(Material, material_id)
    if material is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Material not found")
    return material


def _fabric_usage_entries(
    db: Session,
    material_id: uuid.UUID,
    from_date: date | None,
    to_date: date | None,
    limit: int,
) -> list[MaterialUsageEntryOut]:
    """v3.13: derives fabric consumption history from cutting_layout_item rows whose layout has
    been consumed by a confirmed ProductionBatch. There is no dedicated usage-log table -- this
    is a read-only projection over existing data (see handoff v3.13 for why hardware/thread has
    no equivalent: _fifo_deduct_hardware() only mutates remaining_* in place, it doesn't log).
    """
    query = (
        db.query(
            CuttingLayoutItem.id,
            CuttingLayoutItem.fabric_length_used_cm,
            func.date(ProductionBatch.confirmed_at).label("date"),
            ProductSize.size_label,
            ProductSize.fabric_variant_name,
            ProductSize.id.label("product_size_id"),
            Product.sku.label("product_sku"),
        )
        .join(CuttingLayout, CuttingLayout.id == CuttingLayoutItem.cutting_layout_id)
        .join(MaterialPurchase, MaterialPurchase.id == CuttingLayout.material_purchase_id)
        .join(ProductionBatchLayout, ProductionBatchLayout.cutting_layout_id == CuttingLayout.id)
        .join(ProductionBatch, ProductionBatch.id == ProductionBatchLayout.production_batch_id)
        .join(ProductSize, ProductSize.id == CuttingLayoutItem.product_size_id)
        .join(Product, Product.id == ProductSize.product_id)
        .filter(
            MaterialPurchase.material_id == material_id,
            CuttingLayout.status == "used",
            ProductionBatch.status == "confirmed",
            # confirmed_at is nullable for batches confirmed before the v2.12 migration added the
            # column -- skip those rather than crash or silently mis-date them (implement doc's
            # recommended option 1; batch volume from that era is small).
            ProductionBatch.confirmed_at.isnot(None),
        )
        .order_by(ProductionBatch.confirmed_at.desc())
    )

    if from_date:
        query = query.filter(func.date(ProductionBatch.confirmed_at) >= from_date)
    if to_date:
        query = query.filter(func.date(ProductionBatch.confirmed_at) <= to_date)

    query = query.limit(limit)

    results = []
    for row in query.all():
        parts = [row.size_label]
        if row.fabric_variant_name:
            parts.append(row.fabric_variant_name)
        description = " · ".join(parts)

        results.append(
            MaterialUsageEntryOut(
                id=row.id,
                material_id=material_id,
                deducted_cm=row.fabric_length_used_cm or 0.0,
                date=row.date,
                description=description,
                product_size_id=row.product_size_id,
                product_sku=row.product_sku,
                size_label=row.size_label,
            )
        )
    return results


@router.post("", response_model=MaterialOut, status_code=status.HTTP_201_CREATED)
def create_material(body: MaterialCreate, db: Session = Depends(get_db)):
    if body.category != "fabric" and body.fabric_width_cm is not None:
        raise HTTPException(status_code=400, detail="fabric_width_cm only applies to category='fabric'")

    material = Material(
        name=body.name,
        category=body.category,
        cost_class=_COST_CLASS_BY_CATEGORY[body.category],
        purchase_unit=body.purchase_unit,
        usage_unit=body.usage_unit,
        fabric_width_cm=body.fabric_width_cm,
        reorder_min_qty=body.reorder_min_qty,
    )
    db.add(material)
    db.commit()
    db.refresh(material)
    return material


@router.get("", response_model=list[MaterialOut])
def list_materials(
    search: str | None = None,
    include_archived: bool = False,
    limit: int = Query(default=50, le=200),
    offset: int = 0,
    db: Session = Depends(get_db),
):
    q = db.query(Material)
    if not include_archived:
        q = q.filter(Material.is_archived.is_(False))
    if search:
        q = q.filter(Material.name.ilike(f"%{search}%"))
    return q.order_by(Material.name).offset(offset).limit(limit).all()


@router.get("/{material_id}", response_model=MaterialOut)
def get_material(material_id: uuid.UUID, db: Session = Depends(get_db)):
    return _get_material_or_404(db, material_id)


@router.patch("/{material_id}", response_model=MaterialOut)
def update_material(material_id: uuid.UUID, body: MaterialUpdate, db: Session = Depends(get_db)):
    material = _get_material_or_404(db, material_id)

    if body.name is not None:
        material.name = body.name
    if body.fabric_width_cm is not None:
        if material.category != "fabric":
            raise HTTPException(status_code=400, detail="fabric_width_cm only applies to category='fabric'")
        material.fabric_width_cm = body.fabric_width_cm
    if body.reorder_min_qty is not None:
        material.reorder_min_qty = body.reorder_min_qty
    if body.is_archived is not None:
        material.is_archived = body.is_archived

    db.commit()
    db.refresh(material)
    return material


@router.post("/{material_id}/purchases", response_model=MaterialPurchaseOut, status_code=status.HTTP_201_CREATED)
def create_purchase(material_id: uuid.UUID, body: MaterialPurchaseCreate, db: Session = Depends(get_db)):
    material = _get_material_or_404(db, material_id)
    _validate_purchase_future_date(body.purchased_at)

    if material.category == "fabric":
        if body.width_cm is None or body.length_cm is None or body.width_cm <= 0 or body.length_cm <= 0:
            raise HTTPException(status_code=400, detail="width_cm and length_cm (both > 0) are required for fabric")
        if body.qty is not None or body.package_label is not None:
            raise HTTPException(status_code=400, detail="qty/package_label are not applicable to fabric purchases")
    elif material.category == "hardware":
        # v2.5: hardware may optionally track length_cm (e.g. elastic band, ribbon sold by the
        # cm but purchased in rolls) alongside the existing count-only qty tracking.
        if body.qty is None or body.qty <= 0:
            raise HTTPException(status_code=400, detail="qty (> 0) is required for hardware purchases")
        if body.length_cm is not None and body.length_cm <= 0:
            raise HTTPException(status_code=400, detail="length_cm must be > 0 when provided")
        if body.width_cm is not None:
            raise HTTPException(status_code=400, detail="width_cm only applies to fabric purchases")
        if body.package_label is not None:
            raise HTTPException(status_code=400, detail="package_label only applies to thread purchases")
    else:
        if body.qty is None or body.qty <= 0:
            raise HTTPException(status_code=400, detail="qty (> 0) is required for non-fabric purchases")
        if body.width_cm is not None or body.length_cm is not None:
            raise HTTPException(status_code=400, detail="width_cm/length_cm only apply to fabric or hardware purchases")
        if material.category != "thread" and body.package_label is not None:
            raise HTTPException(status_code=400, detail="package_label only applies to thread purchases")

    supplier_id = _resolve_supplier(db, body.supplier_id, body.supplier_name)

    if material.category == "fabric":
        remaining_length_cm = body.length_cm
    elif material.category == "hardware" and body.length_cm is not None:
        remaining_length_cm = body.qty * body.length_cm
    else:
        remaining_length_cm = None

    purchase = MaterialPurchase(
        material_id=material.id,
        width_cm=body.width_cm,
        length_cm=body.length_cm,
        qty=body.qty,
        package_label=body.package_label,
        total_cost=body.total_cost,
        supplier_id=supplier_id,
        purchased_at=body.purchased_at,
        remaining_length_cm=remaining_length_cm,
        remaining_qty=body.qty if material.category != "fabric" else None,
    )
    db.add(purchase)
    db.flush()
    _recalc_avg_cost(db, material)
    db.commit()
    db.refresh(purchase)
    return _purchase_out(material, purchase)


@router.get("/{material_id}/usage", response_model=list[MaterialUsageEntryOut])
def get_material_usage(
    material_id: uuid.UUID,
    from_date: date | None = Query(default=None, alias="from"),
    to_date: date | None = Query(default=None, alias="to"),
    limit: int = Query(default=50, ge=1, le=500),
    db: Session = Depends(get_db),
):
    material = _get_material_or_404(db, material_id)
    # Hardware/thread consumption isn't logged anywhere (_fifo_deduct_hardware in production.py
    # only mutates remaining_* in place) -- only fabric has a derivable usage history.
    if material.category != "fabric":
        return []
    return _fabric_usage_entries(db, material_id, from_date, to_date, limit)


@router.get("/{material_id}/purchases", response_model=list[MaterialPurchaseOut])
def list_purchases(
    material_id: uuid.UUID,
    limit: int = Query(default=50, le=200),
    offset: int = 0,
    db: Session = Depends(get_db),
):
    material = _get_material_or_404(db, material_id)
    purchases = (
        db.query(MaterialPurchase)
        .filter(MaterialPurchase.material_id == material_id)
        .order_by(MaterialPurchase.purchased_at.desc(), MaterialPurchase.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return [_purchase_out(material, p) for p in purchases]


def _get_purchase_or_404(db: Session, material_id: uuid.UUID, purchase_id: uuid.UUID) -> MaterialPurchase:
    purchase = (
        db.query(MaterialPurchase)
        .filter(MaterialPurchase.id == purchase_id, MaterialPurchase.material_id == material_id)
        .first()
    )
    if purchase is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Purchase not found")
    return purchase


@router.patch("/{material_id}/purchases/{purchase_id}", response_model=MaterialPurchaseOut)
def update_purchase(
    material_id: uuid.UUID,
    purchase_id: uuid.UUID,
    body: MaterialPurchaseUpdate,
    db: Session = Depends(get_db),
):
    material = _get_material_or_404(db, material_id)
    purchase = _get_purchase_or_404(db, material_id, purchase_id)
    _validate_purchase_future_date(body.purchased_at)

    consumed = is_purchase_consumed(
        material.category, purchase.length_cm, purchase.remaining_length_cm, purchase.qty, purchase.remaining_qty
    )

    dimension_fields_present = body.width_cm is not None or body.length_cm is not None or body.qty is not None
    if consumed and dimension_fields_present:
        raise HTTPException(
            status_code=400,
            detail="Sudah dipakai di produksi — dimensi tidak bisa diubah (width_cm/length_cm/qty locked)",
        )

    if not consumed:
        if body.width_cm is not None:
            purchase.width_cm = body.width_cm
        if body.length_cm is not None:
            purchase.length_cm = body.length_cm
        if body.qty is not None:
            purchase.qty = body.qty
            purchase.remaining_qty = body.qty

        if material.category == "fabric":
            if body.length_cm is not None:
                purchase.remaining_length_cm = body.length_cm
        elif material.category == "hardware" and purchase.length_cm is not None:
            # Length-tracked hardware (v2.5): remaining_length_cm tracks the total (qty ×
            # length_cm), so editing either field while unconsumed recomputes the total.
            if body.length_cm is not None or body.qty is not None:
                purchase.remaining_length_cm = (purchase.qty or 0.0) * purchase.length_cm

    if body.total_cost is not None:
        purchase.total_cost = body.total_cost
    if body.purchased_at is not None:
        purchase.purchased_at = body.purchased_at
    if body.supplier_id is not None or body.supplier_name is not None:
        purchase.supplier_id = _resolve_supplier(db, body.supplier_id, body.supplier_name)

    db.flush()
    _recalc_avg_cost(db, material)
    db.commit()
    db.refresh(purchase)
    return _purchase_out(material, purchase)


@router.delete("/{material_id}/purchases/{purchase_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_purchase(material_id: uuid.UUID, purchase_id: uuid.UUID, db: Session = Depends(get_db)):
    material = _get_material_or_404(db, material_id)
    purchase = _get_purchase_or_404(db, material_id, purchase_id)

    consumed = is_purchase_consumed(
        material.category, purchase.length_cm, purchase.remaining_length_cm, purchase.qty, purchase.remaining_qty
    )
    if consumed:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Purchase has recorded consumption and cannot be deleted",
        )

    db.delete(purchase)
    db.flush()
    _recalc_avg_cost(db, material)
    db.commit()
    return None
