import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_owner
from app.models.cutting import CuttingLayout, CuttingLayoutItem
from app.models.material import MaterialPurchase
from app.models.pattern import PatternSpec
from app.models.product import ProductSize
from app.models.production import ProductionBatch
from app.schemas.cutting import (
    CreateLayoutRequest,
    CreateLayoutResponse,
    LayoutItem,
    SuggestedLayout,
    SuggestRequest,
    SuggestResponse,
)
from app.services.cutting_optimizer import (
    Candidate,
    allocate_cost_per_piece,
    build_layout,
    estimate_fabric_cost_per_piece,
)

router = APIRouter(prefix="/cutting-optimizer", tags=["cutting-optimizer"], dependencies=[Depends(get_current_owner)])

STRATEGIES = ["min_waste", "max_qty", "max_profit"]


def _get_purchase_or_404(db: Session, purchase_id: uuid.UUID) -> MaterialPurchase:
    purchase = db.get(MaterialPurchase, purchase_id)
    if purchase is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Material purchase not found")
    return purchase


@router.post("/suggest", response_model=SuggestResponse)
def suggest_layouts(body: SuggestRequest, db: Session = Depends(get_db)):
    purchase = _get_purchase_or_404(db, body.material_purchase_id)
    if purchase.width_cm is None or not purchase.remaining_length_cm:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Purchase has no fabric dimensions or is fully consumed")

    if not body.candidates:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="At least one candidate is required")

    candidates: list[Candidate] = []
    for c in body.candidates:
        spec = db.get(PatternSpec, c.pattern_spec_id)
        if spec is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"pattern_spec_id {c.pattern_spec_id} not found")
        if spec.product_size_id != c.product_size_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"pattern_spec_id {c.pattern_spec_id} does not belong to product_size_id {c.product_size_id}",
            )
        # v1.3 rule: a candidate's fabric must match the submitted purchase's material -- the
        # optimizer packs one fabric roll at a time.
        if spec.fabric_material_id != purchase.material_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"pattern_spec_id {c.pattern_spec_id} is for a different fabric material than this purchase",
            )

        product_size = db.get(ProductSize, c.product_size_id)
        selling_price = product_size.selling_price if product_size else None
        profit_hint = None
        if selling_price is not None:
            fabric_cost_estimate = estimate_fabric_cost_per_piece(
                purchase.width_cm, purchase.total_cost, purchase.length_cm,
                Candidate(
                    product_size_id=c.product_size_id, pattern_spec_id=c.pattern_spec_id,
                    cut_width_cm=spec.cut_width_cm, cut_height_cm=spec.cut_height_cm,
                    rotation_allowed=spec.rotation_allowed,
                ),
            )
            profit_hint = selling_price - fabric_cost_estimate

        candidates.append(
            Candidate(
                product_size_id=c.product_size_id,
                pattern_spec_id=c.pattern_spec_id,
                cut_width_cm=spec.cut_width_cm,
                cut_height_cm=spec.cut_height_cm,
                rotation_allowed=spec.rotation_allowed,
                min_qty=c.min_qty or 0,
                profit_per_piece_hint=profit_hint,
            )
        )

    layouts = []
    for strategy in STRATEGIES:
        result = build_layout(strategy, purchase.width_cm, purchase.remaining_length_cm, candidates)
        costs = allocate_cost_per_piece(purchase.total_cost, result.items)
        layouts.append(
            SuggestedLayout(
                strategy=result.strategy,
                waste_pct=result.waste_pct,
                items=[
                    LayoutItem(
                        product_size_id=item.product_size_id,
                        pattern_spec_id=item.pattern_spec_id,
                        orientation=item.orientation,
                        qty_suggested=item.qty_suggested,
                        fabric_length_used_cm=item.fabric_length_used_cm,
                        cost_per_piece=cost,
                    )
                    for item, cost in zip(result.items, costs)
                ],
            )
        )

    layouts.sort(key=lambda layout: layout.waste_pct)
    return SuggestResponse(layouts=layouts)


@router.post("/layouts", response_model=CreateLayoutResponse, status_code=status.HTTP_201_CREATED)
def create_layout(body: CreateLayoutRequest, db: Session = Depends(get_db)):
    purchase = _get_purchase_or_404(db, body.material_purchase_id)

    total_length_used = sum(item.fabric_length_used_cm for item in body.items)
    if total_length_used > (purchase.remaining_length_cm or 0):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Chosen layout uses more fabric than this purchase has remaining",
        )

    layout = CuttingLayout(
        material_purchase_id=purchase.id,
        status="suggested",
        waste_pct=body.waste_pct,
        total_fabric_cost=purchase.total_cost,
    )
    db.add(layout)
    db.flush()

    for item in body.items:
        db.add(
            CuttingLayoutItem(
                cutting_layout_id=layout.id,
                product_size_id=item.product_size_id,
                pattern_spec_id=item.pattern_spec_id,
                orientation=item.orientation,
                qty_suggested=item.qty_suggested,
                fabric_length_used_cm=item.fabric_length_used_cm,
                cost_per_piece=item.cost_per_piece,
            )
        )

    # NOTE: remaining_length_cm is intentionally NOT decremented here -- handoff Section 4
    # Production says POST /production-batches/{id}/confirm is what "decrements
    # material_purchase.remaining_length_cm". A layout is still just a suggestion made
    # concrete (status='suggested') until a production batch actually confirms it; that's
    # also why /discard exists -- to abandon a layout before it ever touches real stock.
    db.commit()
    db.refresh(layout)
    return CreateLayoutResponse(cutting_layout_id=layout.id)


@router.post("/layouts/{layout_id}/discard", status_code=status.HTTP_204_NO_CONTENT)
def discard_layout(layout_id: uuid.UUID, db: Session = Depends(get_db)):
    layout = db.get(CuttingLayout, layout_id)
    if layout is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cutting layout not found")

    used = db.query(ProductionBatch.id).filter(ProductionBatch.cutting_layout_id == layout_id).first() is not None
    if layout.status != "suggested" or used:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only layouts with status='suggested' (not yet used by a production batch) can be discarded",
        )

    layout.status = "discarded"
    db.commit()
    return None
