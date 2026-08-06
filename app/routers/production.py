import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.deps import get_current_owner
from app.models.cutting import CuttingLayout, CuttingLayoutItem
from app.models.material import Material, MaterialPurchase
from app.models.pattern import PatternComponent, PatternSpec
from app.models.production import ProductionBatch, ProductionBatchItem
from app.models.settings import Setting
from app.models.stock import StockLedger
from app.schemas.production import ItemQtyUpdate, ProductionBatchCreate, ProductionBatchOut
from app.services.hpp import compute_hpp

router = APIRouter(prefix="/production-batches", tags=["production"], dependencies=[Depends(get_current_owner)])


def _get_batch_or_404(db: Session, batch_id: uuid.UUID) -> ProductionBatch:
    batch = (
        db.query(ProductionBatch)
        .options(joinedload(ProductionBatch.items))
        .filter(ProductionBatch.id == batch_id)
        .first()
    )
    if batch is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Production batch not found")
    return batch


@router.post("", response_model=ProductionBatchOut, status_code=status.HTTP_201_CREATED)
def create_batch(body: ProductionBatchCreate, db: Session = Depends(get_db)):
    batch = ProductionBatch(status="draft")
    db.add(batch)
    db.flush()

    if body.cutting_layout_id is not None:
        layout = db.get(CuttingLayout, body.cutting_layout_id)
        if layout is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cutting layout not found")
        if layout.status != "suggested":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Cutting layout must be status='suggested' to start a production batch from it",
            )

        batch.cutting_layout_id = layout.id
        layout_items = db.query(CuttingLayoutItem).filter(CuttingLayoutItem.cutting_layout_id == layout.id).all()
        for li in layout_items:
            db.add(
                ProductionBatchItem(
                    production_batch_id=batch.id,
                    product_size_id=li.product_size_id,
                    pattern_spec_id=li.pattern_spec_id,
                    qty_actual=li.qty_suggested,
                    cutting_layout_item_id=li.id,
                    material_purchase_id=layout.material_purchase_id,
                    fabric_cost_per_piece=li.cost_per_piece,
                    fabric_length_per_unit_cm=li.fabric_length_used_cm / li.qty_suggested,
                    hpp_fabric=0,
                    hpp_pooled_material=0,
                    hpp_hardware=0,
                    hpp_labor=0,
                    hpp_overhead=0,
                    hpp_total=0,
                )
            )
        # Matches the discard endpoint's invariant: status='used' once a batch exists from this layout.
        layout.status = "used"

    db.commit()
    return _get_batch_or_404(db, batch.id)


@router.get("/{batch_id}", response_model=ProductionBatchOut)
def get_batch(batch_id: uuid.UUID, db: Session = Depends(get_db)):
    return _get_batch_or_404(db, batch_id)


@router.get("", response_model=list[ProductionBatchOut])
def list_batches(status_filter: str | None = Query(default=None, alias="status"), db: Session = Depends(get_db)):
    q = db.query(ProductionBatch).options(joinedload(ProductionBatch.items))
    if status_filter is not None:
        q = q.filter(ProductionBatch.status == status_filter)
    return q.order_by(ProductionBatch.produced_at.desc()).all()


@router.patch("/{batch_id}/items/{item_id}", response_model=ProductionBatchOut)
def update_item_qty(batch_id: uuid.UUID, item_id: uuid.UUID, body: ItemQtyUpdate, db: Session = Depends(get_db)):
    batch = _get_batch_or_404(db, batch_id)
    if batch.status != "draft":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Batch is confirmed and locked")

    item = db.query(ProductionBatchItem).filter(
        ProductionBatchItem.id == item_id, ProductionBatchItem.production_batch_id == batch_id
    ).first()
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Production batch item not found")

    item.qty_actual = body.qty_actual
    db.commit()
    return _get_batch_or_404(db, batch_id)


@router.delete("/{batch_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_batch(batch_id: uuid.UUID, db: Session = Depends(get_db)):
    batch = _get_batch_or_404(db, batch_id)
    if batch.status != "draft":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Only draft batches can be deleted")

    if batch.cutting_layout_id is not None:
        layout = db.get(CuttingLayout, batch.cutting_layout_id)
        if layout is not None and layout.status == "used":
            layout.status = "suggested"

    db.query(ProductionBatchItem).filter(ProductionBatchItem.production_batch_id == batch_id).delete()
    db.delete(batch)
    db.commit()
    return None


def _get_setting(db: Session, key: str) -> float | None:
    setting = db.get(Setting, key)
    return setting.value if setting is not None else None


def _pooled_material_rate_sum(db: Session) -> float:
    rows = db.query(Setting).filter(Setting.key.like("pooled_material_rate:%")).all()
    return sum(s.value for s in rows)


def _hardware_cost_per_unit(db: Session, pattern_spec_id: uuid.UUID) -> float:
    components = db.query(PatternComponent).filter(PatternComponent.pattern_spec_id == pattern_spec_id).all()
    total = 0.0
    for comp in components:
        material = db.get(Material, comp.material_id)
        total += comp.qty_per_unit * (material.current_avg_cost if material else 0.0)
    return total


def _fifo_deduct_hardware(db: Session, material_id: uuid.UUID, qty_needed: float) -> None:
    """Decrements remaining_qty across a hardware material's purchases, oldest first.

    Design choice (handoff doesn't specify this): unlike fabric consumption, which is tied to
    a specific material_purchase_id via CuttingLayout, hardware components (PatternComponent)
    only reference material_id, not a specific purchase -- there's no reservation mechanism
    for hardware the way there is for fabric. FIFO deduction mirrors the same approach used
    for POST .../addStockFromBahan. Clamped at 0, never blocks confirm: the physical batch was
    already produced by the time confirm runs, so a hardware bookkeeping shortfall shouldn't
    prevent recording it -- it just surfaces as remaining_qty hitting 0 rather than going
    negative, which is a real inventory-accuracy problem to flag to the user out of band.
    """
    remaining = qty_needed
    purchases = (
        db.query(MaterialPurchase)
        .filter(MaterialPurchase.material_id == material_id, MaterialPurchase.remaining_qty > 0)
        .order_by(MaterialPurchase.purchased_at.asc(), MaterialPurchase.created_at.asc())
        .all()
    )
    for purchase in purchases:
        if remaining <= 0:
            break
        take = min(purchase.remaining_qty, remaining)
        purchase.remaining_qty -= take
        remaining -= take


@router.post("/{batch_id}/confirm", response_model=ProductionBatchOut)
def confirm_batch(batch_id: uuid.UUID, db: Session = Depends(get_db)):
    batch = _get_batch_or_404(db, batch_id)
    if batch.status != "draft":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Batch is already confirmed")
    if not batch.items:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Batch has no items to confirm")

    labor_rate = _get_setting(db, "labor_rate_per_minute")
    overhead = _get_setting(db, "default_overhead_per_unit")
    if labor_rate is None or overhead is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Settings 'labor_rate_per_minute' and 'default_overhead_per_unit' must be configured before confirming",
        )
    pooled_rate = _pooled_material_rate_sum(db)

    fabric_length_by_purchase: dict[uuid.UUID, float] = {}
    hardware_qty_by_material: dict[uuid.UUID, float] = {}

    for item in batch.items:
        spec = db.get(PatternSpec, item.pattern_spec_id)
        hardware_cost = _hardware_cost_per_unit(db, item.pattern_spec_id)

        breakdown = compute_hpp(
            fabric_cost_per_piece=item.fabric_cost_per_piece,
            pooled_material_rate=pooled_rate,
            hardware_cost_per_unit=hardware_cost,
            est_labor_minutes=spec.est_labor_minutes,
            labor_rate_per_minute=labor_rate,
            overhead_per_unit=overhead,
        )
        item.hpp_fabric = breakdown.hpp_fabric
        item.hpp_pooled_material = breakdown.hpp_pooled_material
        item.hpp_hardware = breakdown.hpp_hardware
        item.hpp_labor = breakdown.hpp_labor
        item.hpp_overhead = breakdown.hpp_overhead
        item.hpp_total = breakdown.hpp_total

        db.add(
            StockLedger(
                product_size_id=item.product_size_id,
                change_qty=item.qty_actual,
                reason="production",
                ref_type="production_batch",
                ref_id=batch.id,
                unit_hpp_snapshot=item.hpp_total,
            )
        )

        fabric_length_by_purchase[item.material_purchase_id] = (
            fabric_length_by_purchase.get(item.material_purchase_id, 0.0)
            + item.fabric_length_per_unit_cm * item.qty_actual
        )

        for comp in db.query(PatternComponent).filter(PatternComponent.pattern_spec_id == item.pattern_spec_id).all():
            hardware_qty_by_material[comp.material_id] = (
                hardware_qty_by_material.get(comp.material_id, 0.0) + comp.qty_per_unit * item.qty_actual
            )

    for purchase_id, length_used in fabric_length_by_purchase.items():
        purchase = db.get(MaterialPurchase, purchase_id)
        if purchase is not None and purchase.remaining_length_cm is not None:
            purchase.remaining_length_cm = max(0.0, purchase.remaining_length_cm - length_used)

    for material_id, qty_used in hardware_qty_by_material.items():
        _fifo_deduct_hardware(db, material_id, qty_used)

    batch.status = "confirmed"
    db.commit()
    return _get_batch_or_404(db, batch_id)
