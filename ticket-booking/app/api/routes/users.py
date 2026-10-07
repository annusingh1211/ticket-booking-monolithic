from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import get_current_user
from app.db.session import get_db
from app.models.user import User

router = APIRouter()


@router.get("/")
def list_users(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return all registered users for the authenticated global dashboard."""
    users = db.execute(
        select(User).order_by(User.id.desc())
    ).scalars().all()

    return {
        "items": [
            {
                "id": user.id,
                "name": user.name,
                "email": user.email,
                "role": user.role,
            }
            for user in users
        ],
        "total": len(users),
    }
