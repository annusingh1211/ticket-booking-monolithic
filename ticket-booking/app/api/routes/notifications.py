from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import get_current_user
from app.db.session import get_db
from app.models.notification import Notification
from app.models.user import User
from app.services.email import send_notification_email

router = APIRouter()


@router.get("/")
def list_notifications(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return db.execute(
        select(Notification)
        .where(Notification.user_id == current_user.id)
        .order_by(Notification.id.desc())
    ).scalars().all()


@router.post("/test-email")
def test_email(current_user: User = Depends(get_current_user)):
    if settings.environment != "development":
        raise HTTPException(404, "Not found")

    send_notification_email(
        current_user.email,
        current_user.name,
        "This is a TicketFlow SMTP test email. If you received this message, SMTP delivery is working.",
        "TicketFlow SMTP Test",
    )

    return {
        "message": "SMTP test executed. Check the backend logs for delivery status.",
        "recipient": current_user.email,
    }


@router.post("/{notification_id}/read")
def mark_read(
    notification_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    item = db.get(Notification, notification_id)
    if not item or item.user_id != current_user.id:
        raise HTTPException(404, "Notification not found")

    item.status = "READ"
    db.commit()
    db.refresh(item)
    return item
