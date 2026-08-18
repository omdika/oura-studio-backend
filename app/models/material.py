import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Material(Base):
    __tablename__ = "material"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String, nullable=False)  # fabric | thread | hardware | packaging
    cost_class: Mapped[str] = mapped_column(String, nullable=False)  # direct_precise | direct_pooled
    purchase_unit: Mapped[str] = mapped_column(String, nullable=False)  # meter | roll | pack | pcs
    usage_unit: Mapped[str] = mapped_column(String, nullable=False)  # cm | cm2 | pcs
    fabric_width_cm: Mapped[float | None] = mapped_column(Numeric(asdecimal=False), nullable=True)
    # v3.15: groups color/pattern variants of the same fabric type (e.g. "Satin Pink"/"Satin Merah"
    # both -> fabric_family "Satin") so iOS can input cut dimensions once per family instead of
    # once per material. Purely a client-side grouping aid -- backend doesn't use this for costing
    # or PatternSpec logic.
    fabric_family: Mapped[str | None] = mapped_column(String(100), nullable=True)
    current_avg_cost: Mapped[float] = mapped_column(Numeric(asdecimal=False), nullable=False, default=0)
    reorder_min_qty: Mapped[float | None] = mapped_column(Numeric(asdecimal=False), nullable=True)
    is_archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    purchases: Mapped[list["MaterialPurchase"]] = relationship(back_populates="material")


class Supplier(Base):
    __tablename__ = "supplier"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MaterialPurchase(Base):
    __tablename__ = "material_purchase"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    material_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("material.id"), nullable=False)
    width_cm: Mapped[float | None] = mapped_column(Numeric(asdecimal=False), nullable=True)  # fabric only
    # fabric: always required. hardware (v2.5): optional -- length-tracked hardware like elastic
    # band/ribbon, sold by the cm but purchased in rolls. thread/packaging: never used.
    length_cm: Mapped[float | None] = mapped_column(Numeric(asdecimal=False), nullable=True)
    qty: Mapped[float | None] = mapped_column(Numeric(asdecimal=False), nullable=True)  # thread/hardware only
    package_label: Mapped[str | None] = mapped_column(Text, nullable=True)  # thread only, descriptive
    total_cost: Mapped[float] = mapped_column(Numeric(asdecimal=False), nullable=False)
    supplier_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("supplier.id"), nullable=True
    )
    purchased_at: Mapped[date] = mapped_column(Date, nullable=False)
    # fabric: length_cm. hardware (v2.5, when length_cm is set): qty × length_cm. otherwise None.
    remaining_length_cm: Mapped[float | None] = mapped_column(Numeric(asdecimal=False), nullable=True)
    # Additive beyond literal Section 3 DDL: thread/hardware purchases have no dimension to decrement,
    # so remaining_qty tracks partial consumption the same way remaining_length_cm does for fabric
    # (decision confirmed with product owner during Stage 3 planning).
    remaining_qty: Mapped[float | None] = mapped_column(Numeric(asdecimal=False), nullable=True)  # thread/hardware only
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    material: Mapped["Material"] = relationship(back_populates="purchases")
    supplier: Mapped["Supplier | None"] = relationship()
