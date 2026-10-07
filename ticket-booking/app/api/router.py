from fastapi import APIRouter, Depends

from app.api.routes import (
    users,
    events,
    seats,
    bookings,
    cancellations,
    food,
    payments,
    notifications,
)
from app.core.security import get_current_user


api_router = APIRouter(
    dependencies=[Depends(get_current_user)]
)

api_router.include_router(users.router, prefix="/users", tags=["Users"])
api_router.include_router(events.router, prefix="/events", tags=["Events"])
api_router.include_router(seats.router, prefix="/seats", tags=["Seats"])
api_router.include_router(bookings.router, prefix="/bookings", tags=["Bookings"])
api_router.include_router(cancellations.router, prefix="/cancellations", tags=["Cancellations"])
api_router.include_router(food.router, prefix="/food", tags=["Food"])
api_router.include_router(payments.router, prefix="/payments", tags=["Payments"])
api_router.include_router(notifications.router, prefix="/notifications", tags=["Notifications"])
