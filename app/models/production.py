import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class ProductionBatch(Base):
    __tablename__ = "production_batch"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    produced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    status: Mapped[str] = mapped_column(String, nullable=False, default="draft")  # draft | confirmed
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # v2.16: denormalized from the first linked layout at creation, never changes after.
    cutting_layout_strategy: Mapped[str | None] = mapped_column(Text, nullable=True)
    material_name: Mapped[str | None] = mapped_column(Text, nullable=True)

    layouts: Mapped[list["ProductionBatchLayout"]] = relationship(
        order_by="ProductionBatchLayout.sort_order", cascade="all, delete-orphan", lazy="selectin"
    )
    items: Mapped[list["ProductionBatchItem"]] = relationship(back_populates="production_batch")

    @property
    def cutting_layout_id(self) -> uuid.UUID | None:
        """Backward-compat: first linked layout's id, or None for a manual batch."""
        return self.layouts[0].cutting_layout_id if self.layouts else None


class ProductionBatchLayout(Base):
    __tablename__ = "production_batch_layout"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    production_batch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("production_batch.id", ondelete="CASCADE"), nullable=False
    )
    cutting_layout_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cutting_layout.id"), nullable=False
    )
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    cutting_layout = relationship("CuttingLayout", lazy="joined")


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
    # v2.16: was a computed property derived from cutting_layout_item.qty_suggested, but a
    # multi-layout item's qty is the bottleneck (MIN) across N CuttingLayoutItems -- no single
    # cutting_layout_item to derive it from anymore, so this is set directly at creation instead.
    qty_suggested: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Additive beyond literal Section 3 DDL — needed to make "manual entry without a CuttingLayout" work
    # (decision confirmed with product owner during Stage 5 planning): every item needs a known fabric
    # source purchase + per-piece cost/length whether it came from an optimizer layout or was entered by
    # hand, so confirm-time HPP computation and remaining_length_cm decrement have something to read from.
    # v2.16: nullable now -- a multi-layout item aggregates across N CuttingLayoutItems / purchases /
    # fabric lengths, so these three no longer resolve to one value. Still populated exactly as
    # before for single-layout items.
    cutting_layout_item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cutting_layout_item.id"), nullable=True
    )
    material_purchase_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("material_purchase.id"), nullable=True
    )
    fabric_cost_per_piece: Mapped[float] = mapped_column(Numeric(asdecimal=False), nullable=False)
    fabric_length_per_unit_cm: Mapped[float | None] = mapped_column(Numeric(asdecimal=False), nullable=True)

    cutting_layout_item: Mapped["CuttingLayoutItem | None"] = relationship()

    hpp_fabric: Mapped[float] = mapped_column(Numeric(asdecimal=False), nullable=False)
    hpp_pooled_material: Mapped[float] = mapped_column(Numeric(asdecimal=False), nullable=False)
    hpp_hardware: Mapped[float] = mapped_column(Numeric(asdecimal=False), nullable=False)
    hpp_labor: Mapped[float] = mapped_column(Numeric(asdecimal=False), nullable=False)
    hpp_overhead: Mapped[float] = mapped_column(Numeric(asdecimal=False), nullable=False)
    hpp_total: Mapped[float] = mapped_column(Numeric(asdecimal=False), nullable=False)

    production_batch: Mapped["ProductionBatch"] = relationship(back_populates="items")
