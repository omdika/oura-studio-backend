import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class StockLedger(Base):
    """Append-only. No PATCH/DELETE endpoint exists or may ever be added for this table."""

    __tablename__ = "stock_ledger"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    product_size_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("product_size.id"), nullable=False
    )
    change_qty: Mapped[int] = mapped_column(Integer, nullable=False)  # + production/return, - sale/damage
    reason: Mapped[str] = mapped_column(String, nullable=False)  # production|sale|adjustment|damage|return
    ref_type: Mapped[str | None] = mapped_column(String, nullable=True)  # production_batch|sales_order
    ref_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    unit_hpp_snapshot: Mapped[float | None] = mapped_column(Numeric(asdecimal=False), nullable=True)
    # Additive beyond literal Section 3 DDL: POST /stock/adjustments body accepts optional `note`
    # (Section 4 Products/Stock) but the literal DDL never defined a column to persist it.
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
