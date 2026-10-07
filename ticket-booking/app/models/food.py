from sqlalchemy import ForeignKey, String, Numeric
from sqlalchemy.orm import Mapped, mapped_column
from app.db.session import Base

class FoodItem(Base):
    __tablename__ = "food_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    price: Mapped[float] = mapped_column(Numeric(10, 2))
    available: Mapped[bool] = mapped_column(default=True)

class FoodOrder(Base):
    __tablename__ = "food_orders"
    id: Mapped[int] = mapped_column(primary_key=True)
    booking_id: Mapped[int] = mapped_column(ForeignKey("bookings.id"), index=True)
    status: Mapped[str] = mapped_column(String(30), default="PLACED")

class FoodOrderItem(Base):
    __tablename__ = "food_order_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    food_order_id: Mapped[int] = mapped_column(ForeignKey("food_orders.id"))
    food_item_id: Mapped[int] = mapped_column(ForeignKey("food_items.id"))
    quantity: Mapped[int] = mapped_column(default=1)
