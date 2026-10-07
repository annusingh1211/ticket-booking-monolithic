# Ticketing Monolith Backend

FastAPI modular monolith for a ticket-booking platform. Designed so a React frontend can be added later without changing the backend contract.

Modules: Users, Events, Seats, Bookings, Cancellations, Food Orders, Payments, Notifications.

Stack: FastAPI, SQLAlchemy 2, PostgreSQL, Alembic, Pydantic v2, JWT, pytest.

Setup:
1. python3 -m venv .venv
2. source .venv/bin/activate
3. pip install -r requirements.txt
4. cp .env.example .env
5. alembic upgrade head
6. uvicorn app.main:app --reload

API docs: /docs

This is intentionally one deployable modular monolith. Kafka and microservices are deliberately deferred. We will first complete the business flows, load-test and failure-test the monolith, measure the real bottlenecks, and only then evolve the architecture when a demonstrated problem justifies it. See [PROJECT_ROADMAP.md](PROJECT_ROADMAP.md) for the engineering roadmap.
