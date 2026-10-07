# TicketFlow — Engineering Roadmap

TicketFlow is intentionally being built as a **production-style modular monolith first**.

> **Important:** Kafka, microservices, and Kubernetes are **not part of the current implementation target**. They will be introduced only after we have identified real scalability, reliability, and asynchronous-processing problems in the monolith.

## 1. Current Architecture

```
React Frontend
      |
    Nginx
      |
 FastAPI Monolith
      |
 PostgreSQL
      |
 Background Email
```

The application remains one deployable backend. Domain boundaries should stay clear inside the monolith so they can be extracted later without prematurely introducing distributed-system complexity.

## 2. Current Product Target

### Authentication
- Register
- Login
- JWT authentication
- Current-user endpoint
- Roles and authorization: CUSTOMER, EVENT_MANAGER, ADMIN

### Events
- Create event
- Bulk event creation
- List/search/filter events
- Event details
- Update event
- Publish/cancel/complete lifecycle
- Capacity
- Controlled deletion
- Customer notifications for relevant event changes

### Seats
- Event-specific seat inventory
- Generate seats
- Availability
- Hold/release
- Book/release
- Prevent double booking
- Reservation timeout

### Tickets / Bookings
- Select seats
- Create booking
- Booking reference
- Ticket lifecycle
- My tickets
- Ticket details
- Ticket cancellation
- QR/verification
- Check-in lifecycle

### Food
- Event-specific food catalog
- Food item CRUD
- Enable/disable items
- Price and stock
- Food orders
- Order items
- Update/cancel food orders
- Inventory validation

### Payments
- Payment record
- Pending/success/failed states
- Refund lifecycle
- Payment reference
- Idempotent payment operations
- Initially simulated payment flow; real gateway later

### Notifications
- In-app notifications
- Email notifications
- Ticket, food, payment, and event lifecycle notifications
- Notification read/unread state
- Background email processing

### Dashboards
Customer:
- My tickets
- My bookings
- My food orders
- Payments/refunds
- Notifications

Admin/Event Manager:
- Events
- Seats
- Food
- Tickets/bookings
- Payments/refunds
- Customers
- Notifications

## 3. Data Integrity Requirements

Booking must be transaction-safe:

```
BEGIN
  |
  +-- validate user/event
  +-- lock/check seats
  +-- create ticket
  +-- reserve seats
  +-- create food order
  +-- create payment
  |
COMMIT
```

Any failure must roll back the transaction.

The system should also use:
- Database constraints
- Foreign keys
- Unique constraints
- Soft deletion where history matters
- Audit records
- Idempotency for retry-sensitive operations
- Pagination
- Validation
- Consistent API error responses

## 4. Event Cancellation Rule

Event cancellation is a business operation, not a blind database delete.

Expected lifecycle:

```
Event CANCELLED
      |
      +--> Tickets CANCELLED
      |
      +--> Seats RELEASED
      |
      +--> Food Orders CANCELLED
      |
      +--> Refunds INITIATED
      |
      +--> Customers NOTIFIED
```

Historical tickets and payment records should normally be retained with status changes rather than physically deleted.

Hard deletion is reserved for controlled administrative/data-cleanup operations.

## 5. Failure Scenarios We Must Test

Before introducing Kafka, deliberately test:

- Database unavailable
- Database connection exhaustion
- SMTP/email failure
- Backend restart during a booking
- Duplicate booking request
- Duplicate payment request
- Payment failure
- Seat contention
- Concurrent booking of the same seat
- Event cancellation with many customers
- Large notification/email workload
- Large bulk event creation
- Slow API requests
- Network failure
- Container restart

The goal is to observe actual failure modes instead of assuming them.

## 6. Load Testing

We will progressively test:

```
10 users
  ↓
100 users
  ↓
1,000 users
  ↓
10,000 requests
```

Measure:

- Requests/sec
- p50/p95/p99 latency
- Error rate
- DB connections
- CPU
- Memory
- Transaction conflicts
- Seat contention
- Background-task behavior

We should keep measurements before and after optimizations.

## 7. Problems We Expect to Discover

The monolith should expose real engineering constraints such as:

### Synchronous work
A single request may trigger:
- Database work
- Payment work
- Notification creation
- Email delivery
- Inventory changes

This can increase latency and failure coupling.

### Notification fan-out
One event cancellation may require thousands of customer notifications.

### Dual-write consistency
A business transaction may succeed while an external/event operation fails, or vice versa.

### Scaling boundaries
All domains initially scale together even when only one workload is hot.

### Background processing limitations
In-process background work is not a durable distributed job system.

### Database contention
High booking concurrency can create row locks, connection pressure, and transaction latency.

These are hypotheses to validate with measurements, not assumptions to blindly architect around.

## 8. Optimization Before Distribution

When a problem is discovered, first improve the monolith where appropriate:

- Better SQL queries
- Indexes
- Transactions
- Row locking
- Connection-pool tuning
- Pagination
- Caching where justified
- Background processing
- Batch operations
- Idempotency
- Rate limiting
- Retry policies
- Structured logging
- Metrics

We should document the problem and the measured impact of each change.

## 9. Outbox Pattern

Before Kafka, we will study the transactional outbox problem.

Target concept:

```
Business Transaction
      |
      +--> Business Tables
      |
      +--> Outbox Event
             |
             v
       Outbox Publisher
```

The outbox pattern is introduced only after we encounter a real need for reliable event publication.

## 10. Kafka — Later, Not Now

Kafka is intentionally deferred.

Only after the monolith has been:

1. Completed
2. Load tested
3. Failure tested
4. Optimized
5. Measured
6. Shown to have real asynchronous/distribution requirements

will we introduce Kafka.

Then the evolution will be:

```
Modular Monolith
      |
      v
Outbox
      |
      v
Kafka
      |
      +--> Notification Consumer
      +--> Analytics Consumer
      +--> Inventory Consumer
      +--> Other independent consumers
```

At that stage we will study:
- Topics
- Partitions
- Consumer groups
- Offsets
- Consumer lag
- Replication
- ISR
- Leader election
- Rebalancing
- At-least-once delivery
- Idempotent consumers
- Broker failure

## 11. Learning Principle

The project follows this sequence:

```
BUILD
  ↓
LOAD
  ↓
BREAK
  ↓
OBSERVE
  ↓
MEASURE
  ↓
FIX
  ↓
IDENTIFY LIMIT
  ↓
ARCHITECTURAL EVOLUTION
```

We do **not** add technology merely because it is popular.

Every major architectural component should solve a problem that we have actually experienced or can demonstrate with a concrete engineering requirement.

## 12. Current Implementation Priority

The immediate repository priority is:

1. Database relationships
2. Seat inventory and concurrency
3. Ticket booking
4. Ticket cancellation
5. Food catalog
6. Food ordering
7. Food cancellation/update
8. Payment lifecycle
9. Event cancellation cascade
10. Notifications/email
11. Customer dashboard
12. Admin dashboard
13. Transactions and idempotency
14. Failure testing
15. Load testing
16. Performance/observability
17. Outbox pattern
18. **Kafka — only after the above**

**Current status: Modular Monolith.**

**Kafka status: Deferred intentionally.**
