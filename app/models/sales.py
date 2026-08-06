import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class SalesOrder(Base):
    __tablename__ = "sales_order"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    invoice_no: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    customer_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    payment_method: Mapped[str | None] = mapped_column(String, nullable=True)  # cash|transfer|qris|marketplace
    marketplace_fee_pct: Mapped[float] = mapped_column(Numeric(asdecimal=False), nullable=False, default=0)
    status: Mapped[str] = mapped_column(String, nullable=False, default="unpaid")  # unpaid|paid|cancelled
    sold_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    items: Mapped[list["SalesOrderItem"]] = relationship(back_populates="sales_order")


class SalesOrderItem(Base):
    __tablename__ = "sales_order_item"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    sales_order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sales_order.id"), nullable=False
    )
    product_size_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("product_size.id"), nullable=False
    )
    qty: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price: Mapped[float] = mapped_column(Numeric(asdecimal=False), nullable=False)
    discount: Mapped[float] = mapped_column(Numeric(asdecimal=False), nullable=False, default=0)
    unit_hpp_snapshot: Mapped[float] = mapped_column(Numeric(asdecimal=False), nullable=False)
    line_profit: Mapped[float] = mapped_column(Numeric(asdecimal=False), nullable=False)

    sales_order: Mapped["SalesOrder"] = relationship(back_populates="items")
