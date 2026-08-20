import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Numeric, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Product(Base):
    __tablename__ = "product"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    sku: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    is_archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    sizes: Mapped[list["ProductSize"]] = relationship(back_populates="product")


class ProductSize(Base):
    __tablename__ = "product_size"
    __table_args__ = (UniqueConstraint("product_id", "size_label", "fabric_variant_name"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    product_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("product.id"), nullable=False)
    size_label: Mapped[str] = mapped_column(Text, nullable=False)
    # v1.7: nullable, NULL is a distinct value (not equal to other NULLs under Postgres UNIQUE semantics) —
    # duplicate (product_id, size_label, NULL) triples must be caught at the application layer, not just
    # via this DB constraint, when creating a ProductSize with no fabric variant.
    fabric_variant_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    reorder_min_qty: Mapped[float | None] = mapped_column(Numeric(asdecimal=False), nullable=True)
    # Additive beyond literal Section 3 DDL: Section 4 (PATCH .../sizes/{size} body: selling_price?, and
    # GET .../sizes/{size} response: selling_price) requires a column to persist it; the literal DDL omitted it.
    selling_price: Mapped[float | None] = mapped_column(Numeric(asdecimal=False), nullable=True)
    is_archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # v3.19: manual HPP override, per component -- fallback tier 3 in get_hpp_for_sale (routers/sales.py)
    # for sizes that never went through a confirmed ProductionBatch or have an active PatternSpec.
    # NULL means "never set"; 0 means "explicitly set to zero" -- both are distinct from "unset".
    manual_hpp_fabric: Mapped[float | None] = mapped_column(Numeric(14, 4, asdecimal=False), nullable=True)
    manual_hpp_pooled: Mapped[float | None] = mapped_column(Numeric(14, 4, asdecimal=False), nullable=True)
    manual_hpp_hardware: Mapped[float | None] = mapped_column(Numeric(14, 4, asdecimal=False), nullable=True)
    manual_hpp_labor: Mapped[float | None] = mapped_column(Numeric(14, 4, asdecimal=False), nullable=True)
    manual_hpp_overhead: Mapped[float | None] = mapped_column(Numeric(14, 4, asdecimal=False), nullable=True)

    product: Mapped["Product"] = relationship(back_populates="sizes")

    @property
    def manual_hpp_total(self) -> float:
        return (
            (self.manual_hpp_fabric or 0)
            + (self.manual_hpp_pooled or 0)
            + (self.manual_hpp_hardware or 0)
            + (self.manual_hpp_labor or 0)
            + (self.manual_hpp_overhead or 0)
        )

    @property
    def has_manual_hpp(self) -> bool:
        return self.manual_hpp_total > 0
