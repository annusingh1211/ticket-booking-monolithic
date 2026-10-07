from datetime import datetime, timedelta, timezone
from random import choice, randint
from time import perf_counter

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy import delete, select
from app.models.booking import Booking, BookingSeat
from app.models.seat import Seat
from app.models.payment import Payment
from app.models.food import FoodOrder, FoodOrderItem
from sqlalchemy.orm import Session

from app.core.security import get_current_user
from app.db.session import get_db
from app.models.event import Event
from app.models.user import User
from app.models.notification import Notification
from app.services.email import send_event_created_email, send_event_notification_email


router = APIRouter()
public_router = APIRouter()


RANDOM_EVENT_NAMES = [
    "DevOps Summit", "Cloud Engineering Meetup", "Kubernetes Conference",
    "Platform Engineering Day", "SRE Connect", "Backend Builders Night",
    "AI Engineering Forum", "Open Source Conference",
]

RANDOM_VENUES = [
    "Delhi Convention Centre", "India Expo Mart", "Tech Park Auditorium",
    "City Arena", "Innovation Hub", "Grand Conference Hall",
]


def random_event_data(index: int) -> tuple[str, str, datetime]:
    name = f"{choice(RANDOM_EVENT_NAMES)} #{index}"
    venue = choice(RANDOM_VENUES)
    starts_at = datetime.now(timezone.utc) + timedelta(
        days=randint(7, 180),
        hours=randint(0, 23),
        minutes=randint(0, 59),
    )
    return name, venue, starts_at


@router.post("/", status_code=status.HTTP_201_CREATED)
def create_event(
    name: str,
    venue: str,
    starts_at: datetime,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    event = Event(name=name, venue=venue, starts_at=starts_at)

    db.add(event)
    db.flush()

    db.add_all([
        Seat(event_id=event.id, seat_number=f"S{i:03d}", status="AVAILABLE")
        for i in range(1, event.capacity + 1)
    ])
    db.commit()
    db.refresh(event)

    db.add(Notification(
        user_id=current_user.id,
        channel="IN_APP",
        message=f"Event '{event.name}' created successfully",
        status="PENDING",
    ))
    db.commit()

    background_tasks.add_task(
        send_event_created_email,
        current_user.email,
        current_user.name,
        event.name,
        event.venue,
        event.starts_at.isoformat(),
    )

    return event


@router.post("/bulk", status_code=status.HTTP_201_CREATED)
def create_bulk_events(
    count: int = Query(..., ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    started = perf_counter()
    results = []

    for index in range(1, count + 1):
        item_started = perf_counter()
        try:
            name, venue, starts_at = random_event_data(index)
            event = Event(name=name, venue=venue, starts_at=starts_at)
            db.add(event)
            db.commit()
            db.refresh(event)

            results.append({
                "index": index,
                "status": "PASS",
                "event_id": event.id,
                "name": event.name,
                "venue": event.venue,
                "time_ms": round((perf_counter() - item_started) * 1000, 2),
            })
        except Exception as exc:
            db.rollback()
            results.append({
                "index": index,
                "status": "FAIL",
                "error": str(exc)[:200],
                "time_ms": round((perf_counter() - item_started) * 1000, 2),
            })

    total_ms = round((perf_counter() - started) * 1000, 2)
    passed = sum(item["status"] == "PASS" for item in results)

    return {
        "requested": count,
        "passed": passed,
        "failed": count - passed,
        "total_time_ms": total_ms,
        "average_time_ms": round(total_ms / count, 2),
        "results": results,
        "created_by": current_user.email,
    }


@public_router.get("/")
def public_list_events(db: Session = Depends(get_db)):
    result = db.execute(
        select(Event).where(Event.status == "PUBLISHED").order_by(Event.starts_at)
    )
    return result.scalars().all()


@public_router.get("/{event_id}")
def public_get_event(event_id: int, db: Session = Depends(get_db)):
    event = db.get(Event, event_id)
    if event is None or event.status != "PUBLISHED":
        raise HTTPException(status_code=404, detail="Event not found")
    return event


@router.get("/")
def list_events(db: Session = Depends(get_db)):
    result = db.execute(select(Event).order_by(Event.starts_at))
    return result.scalars().all()


@router.get("/{event_id}")
def get_event(event_id: int, db: Session = Depends(get_db)):
    event = db.get(Event, event_id)
    if event is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Event not found",
        )
    return event


@router.put("/{event_id}")
def update_event(
    event_id: int,
    name: str,
    venue: str,
    starts_at: datetime,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    event = db.get(Event, event_id)

    if event is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Event not found",
        )

    previous_details = (
        f"Previous event details:\n"
        f"Previous venue: {event.venue}\n"
        f"Previous starts at: {event.starts_at.isoformat()}\n"
    )

    event.name = name
    event.venue = venue
    event.starts_at = starts_at

    db.commit()
    db.refresh(event)

    db.add(Notification(
        user_id=current_user.id,
        channel="IN_APP",
        message=f"Event '{event.name}' updated successfully",
        status="PENDING",
    ))
    db.commit()

    background_tasks.add_task(
        send_event_notification_email,
        current_user.email,
        current_user.name,
        "Updated",
        event.name,
        event.venue,
        event.starts_at.isoformat(),
        previous_details,
    )

    return event


@router.delete("/all")
def delete_all_events(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    event_ids = db.execute(select(Event.id)).scalars().all()

    if not event_ids:
        return {
            "message": "No events found",
            "deleted_events": 0,
            "deleted_bookings": 0,
            "deleted_seats": 0,
            "deleted_food_orders": 0,
            "deleted_payments": 0,
        }

    booking_ids = select(Booking.id).where(Booking.event_id.in_(event_ids))
    food_order_ids = select(FoodOrder.id).where(FoodOrder.booking_id.in_(booking_ids))

    deleted_food_items = db.execute(
        delete(FoodOrderItem).where(FoodOrderItem.food_order_id.in_(food_order_ids))
    ).rowcount or 0

    deleted_food_orders = db.execute(
        delete(FoodOrder).where(FoodOrder.booking_id.in_(booking_ids))
    ).rowcount or 0

    deleted_booking_seats = db.execute(
        delete(BookingSeat).where(BookingSeat.booking_id.in_(booking_ids))
    ).rowcount or 0

    deleted_payments = db.execute(
        delete(Payment).where(Payment.booking_id.in_(booking_ids))
    ).rowcount or 0

    deleted_bookings = db.execute(
        delete(Booking).where(Booking.id.in_(booking_ids))
    ).rowcount or 0

    deleted_seats = db.execute(
        delete(Seat).where(Seat.event_id.in_(event_ids))
    ).rowcount or 0

    deleted_events = db.execute(
        delete(Event).where(Event.id.in_(event_ids))
    ).rowcount or 0

    db.commit()

    return {
        "message": "All events and their dependent booking data permanently deleted",
        "deleted_events": deleted_events,
        "deleted_bookings": deleted_bookings,
        "deleted_seats": deleted_seats,
        "deleted_food_orders": deleted_food_orders,
        "deleted_food_order_items": deleted_food_items,
        "deleted_payments": deleted_payments,
        "deleted_booking_seats": deleted_booking_seats,
    }


@router.delete("/{event_id}")
def delete_event(
    event_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    event = db.get(Event, event_id)

    if event is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Event not found",
        )

    event_name = event.name
    venue = event.venue
    starts_at = event.starts_at.isoformat()

    bookings = db.execute(select(Booking).where(Booking.event_id == event.id)).scalars().all()
    for booking in bookings:
        booking.status = "CANCELLED"
        links = db.execute(select(BookingSeat).where(BookingSeat.booking_id == booking.id)).scalars().all()
        for link in links:
            seat = db.get(Seat, link.seat_id)
            if seat: seat.status = "AVAILABLE"
        payments = db.execute(select(Payment).where(Payment.booking_id == booking.id)).scalars().all()
        for payment in payments:
            if payment.status == "SUCCESS": payment.status = "REFUND_PENDING"

    event.status = "CANCELLED"
    db.add(Notification(
        user_id=current_user.id,
        channel="IN_APP",
        message=f"Event '{event_name}' cancelled",
        status="PENDING",
    ))
    db.commit()

    background_tasks.add_task(
        send_event_notification_email,
        current_user.email,
        current_user.name,
        "Deleted",
        event_name,
        venue,
        starts_at,
    )

    return {
        "message": "Event cancelled and active bookings released",
        "event_id": event_id,
    }
