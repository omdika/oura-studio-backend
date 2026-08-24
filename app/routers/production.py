import uuid
from collections import defaultdict
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.deps import get_current_owner
from app.models.cutting import CuttingLayout, CuttingLayoutItem
from app.models.material import Material, MaterialPurchase
from app.models.pattern import PatternComponent, PatternSpec
from app.models.production import ProductionBatch, ProductionBatchItem, ProductionBatchLayout
from app.models.settings import Setting
from app.models.stock import StockLedger
from app.schemas.production import ItemQtyUpdate, ProductionBatchCreate, ProductionBatchItemCreate, ProductionBatchItemOut, ProductionBatchOut
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


def _batch_out(batch: ProductionBatch) -> ProductionBatchOut:
    out = ProductionBatchOut.model_validate(batch, from_attributes=True)
    out.cutting_layout_ids = [pbl.cutting_layout_id for pbl in batch.layouts]
    out.cutting_layout_id = out.cutting_layout_ids[0] if out.cutting_layout_ids else None
    return out


@router.post("", response_model=ProductionBatchOut, status_code=status.HTTP_201_CREATED)
def create_batch(body: ProductionBatchCreate, db: Session = Depends(get_db)):
    layout_ids = body.cutting_layout_ids
    if len(set(layout_ids)) != len(layout_ids):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="cutting_layout_ids has duplicates")

    layouts_by_id: dict[uuid.UUID, CuttingLayout] = {}
    if layout_ids:
        found = db.query(CuttingLayout).filter(CuttingLayout.id.in_(layout_ids)).all()
        layouts_by_id = {layout.id: layout for layout in found}
        missing = [str(lid) for lid in layout_ids if lid not in layouts_by_id]
        if missing:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=f"Cutting layout(s) not found: {', '.join(missing)}"
            )
        for lid in layout_ids:
            layout = layouts_by_id[lid]
            if layout.status != "suggested":
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Cutting layout {lid} must be status='suggested' to start a production batch from it",
                )

    layouts = [layouts_by_id[lid] for lid in layout_ids]  # preserves caller-specified order

    # Name/strategy come from the first layout (order as given by the caller), matching the
    # implement doc's "layout pertama" -- these are denormalized once at creation and never change.
    first_layout = layouts[0] if layouts else None
    strategy = first_layout.strategy if first_layout else None
    material_name = None
    if first_layout is not None:
        purchase = db.get(MaterialPurchase, first_layout.material_purchase_id)
        material = db.get(Material, purchase.material_id) if purchase else None
        material_name = material.name if material else None

    batch = ProductionBatch(
        status="draft", notes=body.notes, cutting_layout_strategy=strategy, material_name=material_name
    )
    db.add(batch)
    db.flush()

    for sort_order, layout in enumerate(layouts):
        db.add(ProductionBatchLayout(production_batch_id=batch.id, cutting_layout_id=layout.id, sort_order=sort_order))
        # Matches the discard endpoint's invariant: status='used' once a batch exists from this layout.
        layout.status = "used"

    # Build batch items, one per product_size_id across all linked layouts. Two-level aggregation,
    # not a flat "group everything by product_size_id" (the implement doc's pseudocode does that,
    # but it's wrong when a *single* layout already places one size across two orientations to use
    # leftover space -- e.g. 48 normal + 7 rotated pieces of the same size from the same roll. Those
    # must SUM (55 total pieces from one fabric, qty-weighted average cost), not MIN/bottleneck --
    # MIN only makes sense *across different fabrics* (a joint product limited by whichever fabric
    # runs out first), not across orientations of the same fabric.
    per_layout_size: dict[tuple[uuid.UUID, uuid.UUID], dict] = {}
    for layout in layouts:
        items = db.query(CuttingLayoutItem).filter(CuttingLayoutItem.cutting_layout_id == layout.id).all()
        for li in items:
            key = (li.product_size_id, layout.id)
            agg = per_layout_size.setdefault(
                key, {"qty": 0, "cost_total": 0.0, "len_total": 0.0, "spec_id": li.pattern_spec_id, "items": []}
            )
            agg["qty"] += li.qty_suggested
            agg["cost_total"] += li.cost_per_piece * li.qty_suggested
            agg["len_total"] += li.fabric_length_used_cm
            agg["items"].append(li)

    by_size: dict[uuid.UUID, list[tuple[uuid.UUID, dict]]] = defaultdict(list)
    for (size_id, layout_id), agg in per_layout_size.items():
        by_size[size_id].append((layout_id, agg))

    # Manual batch (cutting_layout_ids empty): no layouts means no items here -- user adds them
    # via POST /production-batches/{id}/items (v2.14, not yet implemented as a live endpoint).
    for size_id, layout_aggs in by_size.items():
        spec_id = layout_aggs[0][1]["spec_id"]  # all items for one size should share one spec
        qty = min(agg["qty"] for _, agg in layout_aggs)
        fabric_cost_per_piece = sum(agg["cost_total"] / agg["qty"] for _, agg in layout_aggs if agg["qty"] > 0)

        if len(layout_aggs) == 1:
            layout_id, agg = layout_aggs[0]
            material_purchase_id = layouts_by_id[layout_id].material_purchase_id
            fabric_length_per_unit_cm = agg["len_total"] / agg["qty"] if agg["qty"] > 0 else None
            cutting_layout_item_id = agg["items"][0].id if len(agg["items"]) == 1 else None
        else:
            material_purchase_id = None
            fabric_length_per_unit_cm = None
            cutting_layout_item_id = None

        db.add(
            ProductionBatchItem(
                production_batch_id=batch.id,
                product_size_id=size_id,
                pattern_spec_id=spec_id,
                qty_actual=qty,
                qty_suggested=qty,
                cutting_layout_item_id=cutting_layout_item_id,
                material_purchase_id=material_purchase_id,
                fabric_cost_per_piece=fabric_cost_per_piece,
                fabric_length_per_unit_cm=fabric_length_per_unit_cm,
                hpp_fabric=0,
                hpp_pooled_material=0,
                hpp_hardware=0,
                hpp_labor=0,
                hpp_overhead=0,
                hpp_total=0,
            )
        )

    db.commit()
    return _batch_out(_get_batch_or_404(db, batch.id))


@router.get("/{batch_id}", response_model=ProductionBatchOut)
def get_batch(batch_id: uuid.UUID, db: Session = Depends(get_db)):
    return _batch_out(_get_batch_or_404(db, batch_id))


@router.get("", response_model=list[ProductionBatchOut])
def list_batches(status_filter: str | None = Query(default=None, alias="status"), db: Session = Depends(get_db)):
    q = db.query(ProductionBatch).options(joinedload(ProductionBatch.items))
    if status_filter is not None:
        q = q.filter(ProductionBatch.status == status_filter)
    batches = q.order_by(ProductionBatch.produced_at.desc()).all()
    return [_batch_out(b) for b in batches]


@router.patch("/{batch_id}/items/{item_id}", response_model=ProductionBatchItemOut)
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
    db.refresh(item)
    return item


@router.delete("/{batch_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_batch(batch_id: uuid.UUID, db: Session = Depends(get_db)):
    batch = _get_batch_or_404(db, batch_id)
    if batch.status != "draft":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Only draft batches can be deleted")

    for pbl in batch.layouts:
        layout = db.get(CuttingLayout, pbl.cutting_layout_id)
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
    # PatternComponent is designed to hold hardware-only rows (handoff 1.3: fabric is tracked via
    # PatternSpecFabric rows (v2.15), pooled materials via settings, not PatternComponent) -- but
    # POST /pattern-specs doesn't enforce that server-side (only the iOS picker UI restricts it), so
    # filter defensively here rather than trust every component row is actually hardware.
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


def _fifo_deduct_hardware(db: Session, material_id: uuid.UUID, qty_needed: float) -> None:
    """Decrements stock across a hardware material's purchases, oldest first.

    Design choice (handoff doesn't specify this): unlike fabric consumption, which is tied to
    a specific material_purchase_id via CuttingLayout, hardware components (PatternComponent)
    only reference material_id, not a specific purchase -- there's no reservation mechanism
    for hardware the way there is for fabric. FIFO deduction mirrors the same approach used
    for POST .../addStockFromBahan. Clamped at 0, never blocks confirm: the physical batch was
    already produced by the time confirm runs, so a hardware bookkeeping shortfall shouldn't
    prevent recording it -- it just surfaces as remaining hitting 0 rather than going negative,
    which is a real inventory-accuracy problem to flag to the user out of band.

    v2.5 -- length-tracked hardware: PatternComponent still only carries one qty_per_unit
    (no separate "length per unit" field exists, and the handoff explicitly says no migration
    is needed for v2.5). So qty_per_unit is read as already being expressed in whatever unit
    a given purchase batch is stocked in -- cm for a purchase with remaining_length_cm set,
    pcs otherwise -- and qty_needed (qty_per_unit × batch qty, computed by the caller) is
    deducted from whichever field that purchase tracks. This only stays dimensionally correct
    if a material's purchase history is consistently one tracking type; see purchase_quantity()
    in services/material_cost.py for the same caveat on mixed history.
    """
    remaining = qty_needed
    purchases = (
        db.query(MaterialPurchase)
        .filter(MaterialPurchase.material_id == material_id)
        .order_by(MaterialPurchase.purchased_at.asc(), MaterialPurchase.created_at.asc())
        .all()
    )
    for purchase in purchases:
        if remaining <= 0:
            break
        if purchase.remaining_length_cm is not None:
            available = purchase.remaining_length_cm
            if available <= 0:
                continue
            take = min(available, remaining)
            purchase.remaining_length_cm = max(0.0, available - take)
        else:
            available = purchase.remaining_qty or 0.0
            if available <= 0:
                continue
            take = min(available, remaining)
            purchase.remaining_qty = max(0.0, available - take)
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

    hardware_qty_by_material: dict[uuid.UUID, float] = {}

    for item in batch.items:
        # For manual batches, pattern_spec_id can be None. Handle this gracefully.
        spec = db.get(PatternSpec, item.pattern_spec_id) if item.pattern_spec_id else None
        
        # If no spec, HPP components from spec (labor, hardware) will be 0.
        est_labor_minutes = spec.est_labor_minutes if spec else 0.0
        hardware_cost = _hardware_cost_per_unit(db, item.pattern_spec_id) if item.pattern_spec_id else 0.0

        breakdown = compute_hpp(
            fabric_cost_per_piece=item.fabric_cost_per_piece or 0.0, # Use 0.0 if fabric_cost_per_piece is None
            pooled_material_rate=pooled_rate,
            hardware_cost_per_unit=hardware_cost,
            est_labor_minutes=est_labor_minutes,
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

        if spec: # Only deduct components if a spec exists
            for comp in db.query(PatternComponent).filter(PatternComponent.pattern_spec_id == item.pattern_spec_id).all():
                hardware_qty_by_material[comp.material_id] = (
                    hardware_qty_by_material.get(comp.material_id, 0.0) + comp.qty_per_unit * item.qty_actual
                )

    # v2.16: deduct fabric from every linked layout's own purchase directly (not via the batch
    # items' material_purchase_id, which is only set for single-fabric items) -- this is also
    # naturally correct when a layout placed one size across two orientations, since it sums the
    # layout's own raw CuttingLayoutItems rather than going through per-item aggregation.
    for pbl in batch.layouts:
        layout = pbl.cutting_layout
        total_used = sum(li.fabric_length_used_cm for li in layout.items)
        purchase = db.get(MaterialPurchase, layout.material_purchase_id)
        if purchase is not None and purchase.remaining_length_cm is not None:
            purchase.remaining_length_cm = max(0.0, purchase.remaining_length_cm - total_used)
        layout.status = "used"

    for material_id, qty_used in hardware_qty_by_material.items():
        _fifo_deduct_hardware(db, material_id, qty_used)

    batch.status = "confirmed"
    batch.confirmed_at = datetime.now(timezone.utc)
    db.commit()
    return _batch_out(_get_batch_or_404(db, batch_id))


# v2.14: New endpoint to add items to a manual batch
@router.post("/{batch_id}/items", response_model=ProductionBatchItemOut, status_code=status.HTTP_201_CREATED)
def add_batch_item(batch_id: uuid.UUID, body: ProductionBatchItemCreate, db: Session = Depends(get_db)):
    batch = _get_batch_or_404(db, batch_id)
    if batch.status != "draft":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Only draft batches can have items added")

    # Validate product_size_id
    product_size = db.get(ProductSize, body.product_size_id)
    if product_size is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product size not found")

    # Resolve latest active PatternSpec for this product_size_id
    pattern_spec = (
        db.query(PatternSpec)
        .filter(PatternSpec.product_size_id == body.product_size_id, PatternSpec.is_active.is_(True))
        .order_by(PatternSpec.effective_from.desc())
        .first()
    )

    item = ProductionBatchItem(
        production_batch_id=batch.id,
        product_size_id=body.product_size_id,
        pattern_spec_id=pattern_spec.id if pattern_spec else None,
        qty_actual=body.qty_actual,
        qty_suggested=None,  # No cutting layout for manual entry
        cutting_layout_item_id=None,
        material_purchase_id=None,
        fabric_cost_per_piece=None, # No fabric cost from cutting layout for manual entry
        fabric_length_per_unit_cm=None,
        hpp_fabric=0,
        hpp_pooled_material=0,
        hpp_hardware=0,
        hpp_labor=0,
        hpp_overhead=0,
        hpp_total=0,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


# v2.14: New endpoint to delete items from a manual batch
@router.delete("/{batch_id}/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_batch_item(batch_id: uuid.UUID, item_id: uuid.UUID, db: Session = Depends(get_db)):
    batch = _get_batch_or_404(db, batch_id)
    if batch.status != "draft":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Only draft batches can have items deleted")

    item = db.query(ProductionBatchItem).filter(
        ProductionBatchItem.id == item_id, ProductionBatchItem.production_batch_id == batch_id
    ).first()
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Production batch item not found")

    db.delete(item)
    db.commit()
    return None
