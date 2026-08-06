import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.deps import get_current_owner
from app.models.material import Material, MaterialPurchase
from app.models.pattern import PatternComponent, PatternSpec
from app.models.product import ProductSize
from app.models.production import ProductionBatchItem
from app.schemas.pattern import PatternSpecCreate, PatternSpecOut
from app.services.pattern_versioning import SpecSaveAction, decide_spec_save_action

router = APIRouter(prefix="/pattern-specs", tags=["pattern-specs"], dependencies=[Depends(get_current_owner)])


def _has_purchase_on_record(db: Session, material_id: uuid.UUID) -> bool:
    return db.query(MaterialPurchase.id).filter(MaterialPurchase.material_id == material_id).first() is not None


def _validate_material_eligibility(db: Session, body: PatternSpecCreate) -> Material:
    fabric = db.get(Material, body.fabric_material_id)
    if fabric is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="fabric_material_id not found")
    if fabric.category != "fabric":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="fabric_material_id must reference a fabric material")
    if not _has_purchase_on_record(db, fabric.id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="fabric_material_id has no purchase on record — no cost basis to compute HPP",
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

    return fabric


def _spec_out(spec: PatternSpec) -> PatternSpecOut:
    return PatternSpecOut.model_validate(spec, from_attributes=True)


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
            PatternSpec.fabric_material_id == body.fabric_material_id,
            PatternSpec.is_active.is_(True),
        )
        .first()
    )

    has_batches = (
        existing is not None
        and db.query(ProductionBatchItem.id).filter(ProductionBatchItem.pattern_spec_id == existing.id).first()
        is not None
    )

    action = decide_spec_save_action(active_spec_exists=existing is not None, has_production_batch_items=has_batches)

    if action == SpecSaveAction.CREATE:
        spec = PatternSpec(
            product_size_id=body.product_size_id,
            fabric_material_id=body.fabric_material_id,
            cut_width_cm=body.cut_width_cm,
            cut_height_cm=body.cut_height_cm,
            rotation_allowed=body.rotation_allowed,
            est_labor_minutes=body.est_labor_minutes,
        )
        db.add(spec)
        db.flush()
    elif action == SpecSaveAction.UPDATE_IN_PLACE:
        spec = existing
        spec.cut_width_cm = body.cut_width_cm
        spec.cut_height_cm = body.cut_height_cm
        spec.rotation_allowed = body.rotation_allowed
        spec.est_labor_minutes = body.est_labor_minutes
        db.query(PatternComponent).filter(PatternComponent.pattern_spec_id == spec.id).delete()
        db.flush()
    else:  # NEW_VERSION
        now = datetime.now(timezone.utc)
        existing.is_active = False
        existing.effective_to = now
        spec = PatternSpec(
            product_size_id=body.product_size_id,
            fabric_material_id=body.fabric_material_id,
            cut_width_cm=body.cut_width_cm,
            cut_height_cm=body.cut_height_cm,
            rotation_allowed=body.rotation_allowed,
            est_labor_minutes=body.est_labor_minutes,
            effective_from=now,
        )
        db.add(spec)
        db.flush()

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
    return _spec_out(spec)


@router.get("", response_model=list[PatternSpecOut])
def list_pattern_specs(
    product_id: uuid.UUID | None = None,
    product_size_id: uuid.UUID | None = None,
    size_label: str | None = None,
    fabric_material_id: uuid.UUID | None = None,
    include_inactive: bool = False,
    db: Session = Depends(get_db),
):
    q = db.query(PatternSpec).options(joinedload(PatternSpec.components))
    if product_id is not None or size_label is not None:
        q = q.join(ProductSize, PatternSpec.product_size_id == ProductSize.id)
        if product_id is not None:
            q = q.filter(ProductSize.product_id == product_id)
        if size_label is not None:
            q = q.filter(ProductSize.size_label == size_label)
    if product_size_id is not None:
        q = q.filter(PatternSpec.product_size_id == product_size_id)
    if fabric_material_id is not None:
        q = q.filter(PatternSpec.fabric_material_id == fabric_material_id)
    if not include_inactive:
        q = q.filter(PatternSpec.is_active.is_(True))
    return [_spec_out(s) for s in q.order_by(PatternSpec.effective_from.desc()).all()]


@router.get("/{spec_id}", response_model=PatternSpecOut)
def get_pattern_spec(spec_id: uuid.UUID, db: Session = Depends(get_db)):
    spec = (
        db.query(PatternSpec).options(joinedload(PatternSpec.components)).filter(PatternSpec.id == spec_id).first()
    )
    if spec is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pattern spec not found")
    return _spec_out(spec)


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

    db.query(PatternComponent).filter(PatternComponent.pattern_spec_id == spec_id).delete()
    db.delete(spec)
    db.commit()
    return None
