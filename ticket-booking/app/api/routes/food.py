from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from app.models.notification import Notification
from sqlalchemy.orm import Session
from app.core.security import get_current_user
from app.db.session import get_db
from app.models.food import FoodItem, FoodOrder, FoodOrderItem
from app.models.booking import Booking
from app.models.payment import Payment
from app.models.user import User
from app.services.email import send_notification_email

router = APIRouter()


class FoodRequest(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    price: float = Field(gt=0)
    available: bool = True


class FoodOrderRequest(BaseModel):
    booking_id: int
    items: list[tuple[int, int]]


@router.get("/items")
def list_food(db: Session = Depends(get_db)):
    return db.execute(select(FoodItem).order_by(FoodItem.id)).scalars().all()


@router.post("/items", status_code=status.HTTP_201_CREATED)
def create_food(
    payload: FoodRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    item = FoodItem(name=payload.name, price=payload.price, available=payload.available)
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


@router.put("/items/{item_id}")
def update_food(
    item_id: int,
    payload: FoodRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    item = db.get(FoodItem, item_id)
    if not item:
        raise HTTPException(404, "Food item not found")
    item.name, item.price, item.available = payload.name, payload.price, payload.available
    db.commit()
    db.refresh(item)
    return item


@router.post("/orders", status_code=status.HTTP_201_CREATED)
def create_food_order(
    payload: FoodOrderRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    booking = db.get(Booking, payload.booking_id)
    if not booking or booking.user_id != current_user.id or booking.status != "CONFIRMED":
        raise HTTPException(400, "Valid confirmed booking required")

    if not payload.items:
        raise HTTPException(400, "At least one food item is required")

    order = FoodOrder(booking_id=booking.id, status="PLACED")
    db.add(order)
    db.flush()

    total = 0
    for food_id, quantity in payload.items:
        if quantity < 1:
            raise HTTPException(400, "Quantity must be positive")
        item = db.get(FoodItem, food_id)
        if not item or not item.available:
            raise HTTPException(400, "Food item unavailable")
        total += float(item.price) * quantity
        db.add(FoodOrderItem(
            food_order_id=order.id,
            food_item_id=item.id,
            quantity=quantity,
        ))

    message = f"Food order #{order.id} placed for booking {booking.reference}"
    db.add(Notification(
        user_id=current_user.id,
        channel="IN_APP",
        message=message,
        status="PENDING",
    ))
    db.commit()
    db.refresh(order)

    background_tasks.add_task(
        send_notification_email,
        current_user.email,
        current_user.name,
        f"{message}. Total: ₹{total:.2f}",
        "Food Order Placed",
    )

    return {
        "order_id": order.id,
        "booking_id": booking.id,
        "status": order.status,
        "total": total,
    }


@router.post("/orders/bulk", status_code=status.HTTP_201_CREATED)
def create_bulk_food_orders(
    booking_id: int,
    food_item_id: int,
    background_tasks: BackgroundTasks,
    quantity: int = 1,
    count: int = 10,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if quantity < 1 or count < 1 or count > 500:
        raise HTTPException(
            400,
            "quantity must be positive and count must be between 1 and 500",
        )

    booking = db.get(Booking, booking_id)
    if not booking or booking.user_id != current_user.id or booking.status != "CONFIRMED":
        raise HTTPException(400, "Valid confirmed booking required")

    item = db.get(FoodItem, food_item_id)
    if not item or not item.available:
        raise HTTPException(400, "Food item unavailable")

    orders = []
    total = float(item.price) * quantity

    for _ in range(count):
        order = FoodOrder(booking_id=booking.id, status="PLACED")
        db.add(order)
        db.flush()

        db.add(FoodOrderItem(
            food_order_id=order.id,
            food_item_id=item.id,
            quantity=quantity,
        ))

        db.add(Notification(
            user_id=current_user.id,
            channel="IN_APP",
            message=f"Food order #{order.id} placed for booking {booking.reference}",
            status="PENDING",
        ))
        orders.append({"order_id": order.id, "total": total})

    db.commit()

    if orders:
        message = (
            f"{len(orders)} food order(s) placed for booking {booking.reference}. "
            f"Total: ₹{total * len(orders):.2f}"
        )
        background_tasks.add_task(
            send_notification_email,
            current_user.email,
            current_user.name,
            message,
            "Bulk Food Orders Placed",
        )

    return {
        "requested": count,
        "created": len(orders),
        "food_item_id": food_item_id,
        "quantity": quantity,
        "orders": orders,
        "total": total * len(orders),
    }


@router.get("/orders")
def list_my_food_orders(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    rows = db.execute(
        select(FoodOrder, Booking, User)
        .join(Booking, Booking.id == FoodOrder.booking_id)
        .join(User, User.id == Booking.user_id)
        .where(Booking.user_id == current_user.id)
        .order_by(FoodOrder.id.desc())
    ).all()

    result = []
    for order, booking, user in rows:
        items = db.execute(
            select(FoodOrderItem).where(FoodOrderItem.food_order_id == order.id)
        ).scalars().all()

        total = 0.0
        for item in items:
            food = db.get(FoodItem, item.food_item_id)
            if food:
                total += float(food.price) * item.quantity

        notification = db.execute(
            select(Notification)
            .where(
                Notification.user_id == current_user.id,
                Notification.message.like(f"%Food order #{order.id}%"),
            )
            .order_by(Notification.id.desc())
        ).scalars().first()

        payment = db.execute(
            select(Payment)
            .where(Payment.booking_id == booking.id)
            .order_by(Payment.id.desc())
        ).scalars().first()

        result.append({
            "order_id": order.id,
            "booking_id": booking.id,
            "booking_reference": booking.reference,
            "email": user.email,
            "amount": total,
            "payment_status": payment.status if payment else "NOT_PAID",
            "order_status": order.status,
            "notification_status": notification.status if notification else "PENDING",
        })

    return result


@router.post("/orders/{order_id}/cancel")
def cancel_food_order(
    order_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    order = db.get(FoodOrder, order_id)
    if not order:
        raise HTTPException(404, "Food order not found")

    booking = db.get(Booking, order.booking_id)
    if not booking or booking.user_id != current_user.id:
        raise HTTPException(404, "Food order not found")

    if order.status == "CANCELLED":
        return order

    order.status = "CANCELLED"
    message = f"Food order #{order.id} cancelled"
    db.add(Notification(
        user_id=current_user.id,
        channel="IN_APP",
        message=message,
        status="PENDING",
    ))
    db.commit()
    db.refresh(order)

    background_tasks.add_task(
        send_notification_email,
        current_user.email,
        current_user.name,
        message,
        "Food Order Cancelled",
    )

    return order
