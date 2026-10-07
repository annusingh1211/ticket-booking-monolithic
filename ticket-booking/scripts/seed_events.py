from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.event import Event
from app.models.seat import Seat


EVENTS = [
    ("Cloud Native India Summit", "India Expo Mart"),
    ("Kubernetes & Platform Engineering Day", "Delhi Convention Centre"),
    ("DevOps Leadership Forum", "Grand Conference Hall"),
    ("SRE Connect India", "Tech Park Auditorium"),
    ("AI Engineering Conference", "Innovation Hub"),
    ("Backend Builders Conference", "City Arena"),
    ("Open Source India Meetup", "India Expo Mart"),
    ("Cloud Security Summit", "Delhi Convention Centre"),
    ("Data Engineering Day", "Grand Conference Hall"),
    ("Modern Software Architecture Forum", "Innovation Hub"),
    ("Observability & Reliability Summit", "Tech Park Auditorium"),
    ("FinTech Engineering Conference", "City Arena"),
    ("Cloud Cost Optimization Day", "India Expo Mart"),
    ("Developer Experience Summit", "Delhi Convention Centre"),
    ("Microservices Architecture Meetup", "Grand Conference Hall"),
    ("Infrastructure Automation Forum", "Innovation Hub"),
    ("Production Engineering Day", "Tech Park Auditorium"),
    ("API & Distributed Systems Summit", "City Arena"),
    ("Engineering Excellence Conference", "India Expo Mart"),
    ("Future of Cloud Engineering", "Delhi Convention Centre"),
]


def main() -> None:
    db = SessionLocal()
    created = 0
    skipped = 0

    try:
        for index, (name, venue) in enumerate(EVENTS, start=1):
            existing = db.execute(
                select(Event).where(Event.name == name)
            ).scalar_one_or_none()

            if existing:
                skipped += 1
                continue

            event = Event(
                name=name,
                venue=venue,
                starts_at=datetime.now(timezone.utc)
                + timedelta(days=7 + (index * 4), hours=18),
                capacity=100,
                status="PUBLISHED",
            )
            db.add(event)
            db.flush()

            db.add_all(
                [
                    Seat(
                        event_id=event.id,
                        seat_number=f"S{seat_number:03d}",
                        status="AVAILABLE",
                    )
                    for seat_number in range(1, event.capacity + 1)
                ]
            )

            created += 1

        db.commit()
        print(f"Seed complete: created={created}, skipped={skipped}")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
