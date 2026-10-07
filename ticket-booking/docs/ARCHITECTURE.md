# Monolith Architecture

One deployable FastAPI application with clear domain boundaries.

Users | Events | Seats | Bookings | Cancellations | Food | Payments | Notifications
-> PostgreSQL ticketing_db

Later extraction path:
Booking Service, Cancellation Service, Food Service, Payment Service, Notification Service.

Later Kafka events:
TICKET_BOOKED, TICKET_CANCELLED, FOOD_ORDERED, PAYMENT_COMPLETED, PAYMENT_FAILED.

Kafka is intentionally absent from this phase so the limitations of the monolith can be experienced first.
