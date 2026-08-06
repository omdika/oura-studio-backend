from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_owner
from app.models.product import ProductSize
from app.models.stock import StockLedger
from app.schemas.product import StockAdjustmentCreate, StockAdjustmentOut

router = APIRouter(prefix="/stock", tags=["stock"], dependencies=[Depends(get_current_owner)])


@router.post("/adjustments", response_model=StockAdjustmentOut, status_code=status.HTTP_201_CREATED)
def create_stock_adjustment(body: StockAdjustmentCreate, db: Session = Depends(get_db)):
    size = db.get(ProductSize, body.product_size_id)
    if size is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product size not found")
    if body.change_qty == 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="change_qty cannot be 0")

    entry = StockLedger(
        product_size_id=body.product_size_id,
        change_qty=body.change_qty,
        reason=body.reason,
        note=body.note,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry
