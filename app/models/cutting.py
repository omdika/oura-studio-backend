import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class CuttingLayout(Base):
    __tablename__ = "cutting_layout"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    material_purchase_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("material_purchase.id"), nullable=False
    )
    status: Mapped[str] = mapped_column(String, nullable=False, default="suggested")  # suggested|used|discarded
    waste_pct: Mapped[float | None] = mapped_column(Numeric(asdecimal=False), nullable=True)
    total_fabric_cost: Mapped[float] = mapped_column(Numeric(asdecimal=False), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    items: Mapped[list["CuttingLayoutItem"]] = relationship(back_populates="cutting_layout")


class CuttingLayoutItem(Base):
    __tablename__ = "cutting_layout_item"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    cutting_layout_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cutting_layout.id"), nullable=False
    )
    product_size_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("product_size.id"), nullable=False
    )
    pattern_spec_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pattern_spec.id"), nullable=False
    )
    orientation: Mapped[str] = mapped_column(String, nullable=False)  # normal | rotated
    qty_suggested: Mapped[int] = mapped_column(Integer, nullable=False)
    fabric_length_used_cm: Mapped[float] = mapped_column(Numeric(asdecimal=False), nullable=False)
    cost_per_piece: Mapped[float] = mapped_column(Numeric(asdecimal=False), nullable=False)

    cutting_layout: Mapped["CuttingLayout"] = relationship(back_populates="items")
