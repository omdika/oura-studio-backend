import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class ProductionBatch(Base):
    __tablename__ = "production_batch"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    cutting_layout_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cutting_layout.id"), nullable=True
    )
    produced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    status: Mapped[str] = mapped_column(String, nullable=False, default="draft")  # draft | confirmed
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    items: Mapped[list["ProductionBatchItem"]] = relationship(back_populates="production_batch")


class ProductionBatchItem(Base):
    __tablename__ = "production_batch_item"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    production_batch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("production_batch.id"), nullable=False
    )
    product_size_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("product_size.id"), nullable=False
    )
    pattern_spec_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pattern_spec.id"), nullable=False
    )
    qty_actual: Mapped[int] = mapped_column(Integer, nullable=False)

    # Additive beyond literal Section 3 DDL — needed to make "manual entry without a CuttingLayout" work
    # (decision confirmed with product owner during Stage 5 planning): every item needs a known fabric
    # source purchase + per-piece cost/length whether it came from an optimizer layout or was entered by
    # hand, so confirm-time HPP computation and remaining_length_cm decrement have something to read from.
    cutting_layout_item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cutting_layout_item.id"), nullable=True
    )
    material_purchase_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("material_purchase.id"), nullable=False
    )
    fabric_cost_per_piece: Mapped[float] = mapped_column(Numeric(asdecimal=False), nullable=False)
    fabric_length_per_unit_cm: Mapped[float] = mapped_column(Numeric(asdecimal=False), nullable=False)

    cutting_layout_item: Mapped["CuttingLayoutItem | None"] = relationship()

    hpp_fabric: Mapped[float] = mapped_column(Numeric(asdecimal=False), nullable=False)
    hpp_pooled_material: Mapped[float] = mapped_column(Numeric(asdecimal=False), nullable=False)
    hpp_hardware: Mapped[float] = mapped_column(Numeric(asdecimal=False), nullable=False)
    hpp_labor: Mapped[float] = mapped_column(Numeric(asdecimal=False), nullable=False)
    hpp_overhead: Mapped[float] = mapped_column(Numeric(asdecimal=False), nullable=False)
    hpp_total: Mapped[float] = mapped_column(Numeric(asdecimal=False), nullable=False)

    production_batch: Mapped["ProductionBatch"] = relationship(back_populates="items")

    @property
    def qty_suggested(self) -> int | None:
        # No cutting_layout_item for manually-entered batch items (no optimizer layout involved).
        return self.cutting_layout_item.qty_suggested if self.cutting_layout_item is not None else None
