from fastapi import FastAPI

from app.core.config import settings
from app.api.router import api_router
from app.api.routes import auth, events, seats


app = FastAPI(
    title=settings.app_name,
    version="1.0.0",
    description="Ticketing platform modular monolith.",
    root_path="/api",
)

# Public authentication endpoints
app.include_router(
    auth.router,
    prefix="/v1/auth",
    tags=["Authentication"],
)

# Public customer catalog. Only read-only event/seat discovery is public.
app.include_router(events.public_router, prefix="/v1/events", tags=["Public Events"])
app.include_router(seats.public_router, prefix="/v1/seats", tags=["Public Seats"])

# All transactional/business services require a valid Bearer token.
app.include_router(
    api_router,
    prefix="/v1",
)


@app.get("/health", tags=["Health"])
def health():
    return {
        "status": "ok",
        "service": "ticketing-monolith",
    }
