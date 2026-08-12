import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class PatternSpec(Base):
    __tablename__ = "pattern_spec"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    product_size_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("product_size.id"), nullable=False
    )
    est_labor_minutes: Mapped[float] = mapped_column(Numeric(asdecimal=False), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    fabrics: Mapped[list["PatternSpecFabric"]] = relationship(
        order_by="PatternSpecFabric.sort_order", cascade="all, delete-orphan", lazy="selectin"
    )
    components: Mapped[list["PatternComponent"]] = relationship(
        back_populates="pattern_spec", cascade="all, delete-orphan"
    )


class PatternSpecFabric(Base):
    __tablename__ = "pattern_spec_fabric"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    pattern_spec_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pattern_spec.id", ondelete="CASCADE"), nullable=False
    )
    material_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("material.id"), nullable=False)
    cut_width_cm: Mapped[float] = mapped_column(Numeric(asdecimal=False), nullable=False)
    cut_height_cm: Mapped[float] = mapped_column(Numeric(asdecimal=False), nullable=False)
    rotation_allowed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    fabric_label: Mapped[str | None] = mapped_column(Text, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    material = relationship("Material", lazy="selectin")


class PatternComponent(Base):
    __tablename__ = "pattern_component"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    pattern_spec_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pattern_spec.id"), nullable=False
    )
    material_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("material.id"), nullable=False)
    qty_per_unit: Mapped[float] = mapped_column(Numeric(asdecimal=False), nullable=False)

    pattern_spec: Mapped["PatternSpec"] = relationship(back_populates="components")
