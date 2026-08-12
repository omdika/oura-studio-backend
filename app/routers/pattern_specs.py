import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.deps import get_current_owner
from app.models.material import Material, MaterialPurchase
from app.models.pattern import PatternComponent, PatternSpec, PatternSpecFabric
from app.models.product import Product, ProductSize
from app.models.production import ProductionBatchItem
from app.schemas.pattern import PatternSpecCreate, PatternSpecOut
from app.services.pattern_versioning import SpecSaveAction, decide_spec_save_action

router = APIRouter(prefix="/pattern-specs", tags=["pattern-specs"], dependencies=[Depends(get_current_owner)])


def _has_purchase_on_record(db: Session, material_id: uuid.UUID) -> bool:
    return db.query(MaterialPurchase.id).filter(MaterialPurchase.material_id == material_id).first() is not None


def _validate_material_eligibility(db: Session, body: PatternSpecCreate) -> None:
    for fabric in body.fabrics:
        material = db.get(Material, fabric.material_id)
        if material is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="fabrics[].material_id not found")
        if material.category != "fabric":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="fabrics[].material_id must reference a fabric material"
            )
        if not _has_purchase_on_record(db, material.id):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"fabric material '{material.name}' has no purchase on record — no cost basis to compute HPP",
            )

    for component in body.components:
        material = db.get(Material, component.material_id)
        if material is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="component material_id not found")
        if not _has_purchase_on_record(db, material.id):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"component material '{material.name}' has no purchase on record",
            )


def _batch_count(db: Session, spec_id: uuid.UUID) -> int:
    return db.query(ProductionBatchItem.id).filter(ProductionBatchItem.pattern_spec_id == spec_id).count()


def _spec_out(
    spec: PatternSpec,
    db: Session,
    product_sku: str | None = None,
    product_name: str | None = None,
    size_label: str | None = None,
) -> PatternSpecOut:
    out = PatternSpecOut.model_validate(spec, from_attributes=True)
    out.product_sku = product_sku
    out.product_name = product_name
    out.size_label = size_label
    out.used_in_batch_count = _batch_count(db, spec.id)
    for fabric_out, fabric in zip(out.fabrics, spec.fabrics):
        fabric_out.material_name = fabric.material.name if fabric.material else None
    return out


def _spec_out_enriched(db: Session, spec: PatternSpec) -> PatternSpecOut:
    """Single-row enrichment lookup for POST/GET {id} — the list endpoint uses a JOIN instead
    since it's the N+1-prone path the iOS client actually hits per Resep tab load."""
    size = db.get(ProductSize, spec.product_size_id)
    product = db.get(Product, size.product_id) if size else None
    return _spec_out(
        spec,
        db,
        product_sku=product.sku if product else None,
        product_name=product.name if product else None,
        size_label=size.size_label if size else None,
    )


def _insert_fabrics(db: Session, spec_id: uuid.UUID, fabrics) -> None:
    for sort_order, fabric in enumerate(fabrics):
        db.add(
            PatternSpecFabric(
                pattern_spec_id=spec_id,
                material_id=fabric.material_id,
                cut_width_cm=fabric.cut_width_cm,
                cut_height_cm=fabric.cut_height_cm,
                rotation_allowed=fabric.rotation_allowed,
                fabric_label=fabric.fabric_label,
                sort_order=sort_order,
            )
        )


@router.post("", response_model=PatternSpecOut)
def save_pattern_spec(body: PatternSpecCreate, db: Session = Depends(get_db)):
    product_size = db.get(ProductSize, body.product_size_id)
    if product_size is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="product_size_id not found")

    _validate_material_eligibility(db, body)

    existing = (
        db.query(PatternSpec)
        .filter(
            PatternSpec.product_size_id == body.product_size_id,
            PatternSpec.is_active.is_(True),
        )
        .first()
    )

    has_batches = existing is not None and _batch_count(db, existing.id) > 0

    action = decide_spec_save_action(active_spec_exists=existing is not None, has_production_batch_items=has_batches)

    if action == SpecSaveAction.CREATE:
        spec = PatternSpec(product_size_id=body.product_size_id, est_labor_minutes=body.est_labor_minutes)
        db.add(spec)
        db.flush()
    elif action == SpecSaveAction.UPDATE_IN_PLACE:
        spec = existing
        spec.est_labor_minutes = body.est_labor_minutes
        db.query(PatternSpecFabric).filter(PatternSpecFabric.pattern_spec_id == spec.id).delete()
        db.query(PatternComponent).filter(PatternComponent.pattern_spec_id == spec.id).delete()
        db.flush()
    else:  # NEW_VERSION
        now = datetime.now(timezone.utc)
        existing.is_active = False
        existing.effective_to = now
        spec = PatternSpec(
            product_size_id=body.product_size_id,
            est_labor_minutes=body.est_labor_minutes,
            effective_from=now,
        )
        db.add(spec)
        db.flush()

    _insert_fabrics(db, spec.id, body.fabrics)

    for component in body.components:
        db.add(PatternComponent(pattern_spec_id=spec.id, material_id=component.material_id, qty_per_unit=component.qty_per_unit))

    db.commit()
    db.refresh(spec)
    spec = (
        db.query(PatternSpec)
        .options(joinedload(PatternSpec.components))
        .filter(PatternSpec.id == spec.id)
        .first()
    )
    return _spec_out_enriched(db, spec)


@router.get("", response_model=list[PatternSpecOut])
def list_pattern_specs(
    product_id: uuid.UUID | None = None,
    product_size_id: uuid.UUID | None = None,
    size_label: str | None = None,
    fabric_material_id: uuid.UUID | None = None,
    include_inactive: bool = False,
    db: Session = Depends(get_db),
):
    # Single JOIN query resolves product/size names for every spec in one round trip, instead of
    # the client doing N+1 lookups against /products + /products/{sku}/sizes. Fabrics + their
    # material are eager-loaded via the model's lazy="selectin" relationships (one extra batched
    # query total, not per-row).
    q = (
        db.query(PatternSpec, Product.sku, Product.name, ProductSize.size_label)
        .options(joinedload(PatternSpec.components))
        .join(ProductSize, PatternSpec.product_size_id == ProductSize.id)
        .join(Product, ProductSize.product_id == Product.id)
    )
    if product_id is not None:
        q = q.filter(ProductSize.product_id == product_id)
    if size_label is not None:
        q = q.filter(ProductSize.size_label == size_label)
    if product_size_id is not None:
        q = q.filter(PatternSpec.product_size_id == product_size_id)
    if fabric_material_id is not None:
        q = q.join(PatternSpecFabric, PatternSpecFabric.pattern_spec_id == PatternSpec.id).filter(
            PatternSpecFabric.material_id == fabric_material_id
        )
    if not include_inactive:
        q = q.filter(PatternSpec.is_active.is_(True))
    rows = q.order_by(PatternSpec.effective_from.desc()).all()
    return [
        _spec_out(spec, db, product_sku=sku, product_name=pname, size_label=slabel)
        for spec, sku, pname, slabel in rows
    ]


@router.get("/{spec_id}", response_model=PatternSpecOut)
def get_pattern_spec(spec_id: uuid.UUID, db: Session = Depends(get_db)):
    spec = (
        db.query(PatternSpec).options(joinedload(PatternSpec.components)).filter(PatternSpec.id == spec_id).first()
    )
    if spec is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pattern spec not found")
    return _spec_out_enriched(db, spec)


@router.delete("/{spec_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_pattern_spec(spec_id: uuid.UUID, db: Session = Depends(get_db)):
    spec = db.get(PatternSpec, spec_id)
    if spec is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pattern spec not found")

    has_batches = (
        db.query(ProductionBatchItem.id).filter(ProductionBatchItem.pattern_spec_id == spec_id).first() is not None
    )
    if has_batches:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This pattern spec version has production batches against it and cannot be deleted",
        )

    db.query(PatternSpecFabric).filter(PatternSpecFabric.pattern_spec_id == spec_id).delete()
    db.query(PatternComponent).filter(PatternComponent.pattern_spec_id == spec_id).delete()
    db.delete(spec)
    db.commit()
    return None
