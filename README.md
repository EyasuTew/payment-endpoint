# Payment Endpoint — Take-Home Task

A Flask + SQLAlchemy + PostgreSQL implementation of the "pay for a cart"
part of an online shop.

## What's included

1. **SQL schema** — [`sql/schema.sql`](sql/schema.sql) (PostgreSQL DDL),
   mirrored by the SQLAlchemy models in [`app/models.py`](app/models.py),
   which are what actually create the tables at runtime.
2. **HTTP endpoint** — `POST /api/v1/carts/<cart_id>/pay`, which starts
   payment for a cart, using a mocked payment provider
   ([`app/payment_provider.py`](app/payment_provider.py)).
3. **Tests** — [`tests/`](tests/), covering the happy path and every
   error path (see "Tests" below).
4. **Structured logging** throughout the request/service/provider layers
   ([`app/logging_config.py`](app/logging_config.py)).
5. **Full API documentation** with curl examples — see [API
   documentation](#api-documentation) below.

## Project layout

```
app/
  __init__.py            # Flask application factory
  config.py               # Dev / Prod / Testing configuration
  extensions.py            # shared SQLAlchemy `db` instance
  logging_config.py        # structured logging + per-request correlation id
  models.py                 # SQLAlchemy models (the SQL schema)
  errors.py                  # domain exceptions -> HTTP status mapping
  payment_provider.py         # mocked payment provider
  services/
    payment_service.py        # all payment business logic
  routes/
    payment_routes.py          # the HTTP endpoint
sql/
  schema.sql                    # plain PostgreSQL DDL (reference/deliverable)
scripts/
  seed_dev_data.py                 # optional helper to seed demo data for manual testing
tests/
  conftest.py                       # fixtures (app, client, seed data)
  test_payment_endpoint.py           # endpoint tests
client.http                            # ready-to-run requests (VS Code REST Client / JetBrains)
run.py                                   # local dev entry point
requirements.txt
.env.example
```

## How to run it

### 1. Requirements
* Python 3.11+
* PostgreSQL 13+ running locally (or reachable via `DATABASE_URL`)

### 2. Install dependencies

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Configure the database

Create a database and copy the env file:

```bash
createdb payments_db
cp .env.example .env
# edit .env if your Postgres user/password/host differ from the defaults
```

### 4. Create the tables

Either apply the SQL file directly:

```bash
psql -d payments_db -f sql/schema.sql
```

...or let SQLAlchemy create them from the models (equivalent schema):

```bash
python -c "from app import create_app; from app.extensions import db; \
app = create_app(); \
app.app_context().push(); \
db.create_all()"
```

### 5. Run the app

```bash
python run.py
# or: flask --app run.py run
```

The API is now available at `http://127.0.0.1:5000`.

> Use `127.0.0.1` rather than `localhost` in requests below - on some
> systems `localhost` resolves to `::1` (IPv6) first, which can hit a
> different process than Flask's dev server (bound to `127.0.0.1` by
> default) and produce a misleading `403`.

### 6. Try the endpoint

The endpoint expects the cart, its items, the user, and a saved payment
method (`payment_methods` row) to already exist. A helper script seeds a
small, deterministic set of demo data so you don't have to write SQL by
hand:

```bash
python scripts/seed_dev_data.py
```

This creates two users you can immediately try:

| User | Card                | Cart                         |
|------|----------------------|-------------------------------|
| `id=1` | works (`tok_visa_ok`) | `id=1`, 2x Wireless Mouse ($20.00) |
| `id=2` | always declines (`tok_visa_declined`) | `id=2`, 1x Wireless Mouse ($10.00) |

Then either:

* **Use the provided [`client.http`](client.http) file** — open it in VS
  Code (with the "REST Client" extension) or in a JetBrains IDE and click
  "Send Request" above each block. It walks through the success case,
  every error case, an idempotent replay, and a decline.

* **Or use curl** — see the full [API documentation](#api-documentation)
  below for every case, e.g.:

```bash
curl -i -X POST http://127.0.0.1:5000/api/v1/carts/1/pay \
  -H "X-User-Id: 1" \
  -H "Idempotency-Key: $(uuidgen)"
```

Successful response (`200`):
```json
{
  "payment_id": 1,
  "order_id": 1,
  "status": "succeeded",
  "amount_cents": 2000,
  "currency": "USD",
  "provider_payment_id": "mock_ch_...",
  "failure_reason": null
}
```

## Tests

Tests run against an **in-memory SQLite** database rather than requiring a
live PostgreSQL instance — see the "Testing note" under Assumptions
below for why that's a safe substitution here.

```bash
pip install -r requirements.txt   # includes pytest
python -m pytest -v
```

10 tests covering:
* successful payment (200), and that it correctly updates the cart/order/payment rows
* paying twice for the same cart (409)
* missing `X-User-Id` header (400)
* non-existent cart (404)
* paying for another user's cart (403)
* empty cart (400)
* user with no saved payment method (400)
* payment provider declining the card (402), and that the cart stays
  open so the user can retry
* retrying with a different (working) card after a decline succeeds
* replaying a request with the same `Idempotency-Key` does not create a
  second charge

## Assumptions

Since parts of the system were described as "already existing" but no
code/contract for them was given, the following decisions were made and
documented in code comments at the relevant spot:

* **Authentication.** No auth system was specified, so the endpoint reads
  the acting user from an `X-User-Id` header, and the service layer
  verifies the target cart actually belongs to that user (403 otherwise).
  A real system would replace this header with a decoded session/JWT.
* **Payment total calculation service.** The task says this already
  exists elsewhere and doesn't need to be built. `payment_service.calculate_cart_total`
  stands in for it with the simplest correct behaviour (sum of
  `quantity × unit_price_cents` across the cart), so the endpoint is
  fully runnable/testable end-to-end. Swapping in the real service only
  requires replacing that one function's body.
* **Which payment method is used.** A user can have multiple saved cards
  (`payment_methods`); the endpoint uses their `is_default` card, falling
  back to any saved card if none is marked default. There was no
  "choose a specific card for this checkout" requirement, but the schema
  (`payment_method_id` on `payments`) would support adding that later.
* **Money representation.** All amounts are stored/passed as integer
  cents (`amount_cents`) + a currency code rather than floats, to avoid
  rounding errors — standard practice for payment systems.
* **Order vs. Payment.** An `Order` represents one checkout attempt for a
  cart; a `Payment` represents one charge attempt against the provider
  for that order. This split lets a failed charge be retried (new
  `Payment` row, same `Order`) with a full audit trail, and keeps a cart
  from ever being charged twice for a *successful* payment (enforced via
  cart status + a 409 response).
* **Idempotency.** An optional `Idempotency-Key` header lets a client
  safely retry the HTTP call (e.g. after a timeout) without the risk of
  double-charging the card — replaying the same key returns the original
  result instead of calling the provider again.
* **Mock payment provider behaviour.** The mock is deterministic rather
  than random, so tests/demos are reproducible: a card token ending in
  `_declined` or `_insufficient_funds` always fails with that reason; any
  other token succeeds. See `app/payment_provider.py`.
* **Testing note.** The task requires PostgreSQL for the running
  application (and the app is wired to it by default via `DATABASE_URL`),
  but the automated tests use an in-memory SQLite database instead of
  requiring a live Postgres instance, so the suite is trivial to run
  anywhere. This is safe because all data access goes through the
  SQLAlchemy ORM — no raw/Postgres-only SQL is used in the code paths
  under test.

## Logging

All logs are emitted in a single structured format:

```
<timestamp> | <LEVEL> | request_id=<id> | <logger name> | <message>
```

Every incoming HTTP request is assigned a short correlation id
(`request_id`), which flows through every log line produced while
handling it (route → service → mock provider), making it easy to trace
"everything that happened for this one payment attempt". No sensitive
payment data (card numbers, CVV) is ever logged — only the opaque
provider token's *outcome*, never the token or card details themselves.

---

## API Documentation

Base URL (local dev): `http://127.0.0.1:5000`

All responses are JSON. There is currently a single resource: paying for
a cart.

### Authentication

There is no full auth system in this task (see [Assumptions](#assumptions)
above). Every request must identify the caller via a header:

| Header      | Required | Description                                   |
|-------------|----------|------------------------------------------------|
| `X-User-Id` | Yes      | Integer id of the user making the request.     |

Requests without a valid `X-User-Id` are rejected with `400`.

### `POST /api/v1/carts/{cart_id}/pay`

Starts (or idempotently retries) payment for the given cart, using the
requesting user's default saved card. On success the cart is marked
`paid` and can never be paid again; on failure the cart is left `open`
so the user can fix their payment method and try again.

**Path parameters**

| Name      | Type    | Description               |
|-----------|---------|----------------------------|
| `cart_id` | integer | Id of the cart to pay for. |

**Headers**

| Header             | Required | Description                                                                 |
|---------------------|----------|-------------------------------------------------------------------------------|
| `X-User-Id`         | Yes      | Id of the user paying for the cart. The cart must belong to this user.        |
| `Idempotency-Key`   | No       | Client-generated unique string. Replaying the same request with the same key returns the original result instead of charging the card again. Recommended for production clients (e.g. to safely retry after a timeout). |

**Request body**

None.

**Successful response — `200 OK`**

Returned when the charge succeeds.

```json
{
  "payment_id": 1,
  "order_id": 1,
  "status": "succeeded",
  "amount_cents": 2000,
  "currency": "USD",
  "provider_payment_id": "mock_ch_3f1e2a...",
  "failure_reason": null
}
```

| Field                  | Type          | Description                                              |
|-------------------------|---------------|-------------------------------------------------------------|
| `payment_id`            | integer       | Id of the created payment attempt.                          |
| `order_id`               | integer       | Id of the order this payment belongs to.                    |
| `status`                  | string        | `"succeeded"`, `"failed"`, or `"pending"`.                   |
| `amount_cents`             | integer       | Amount charged, in the smallest currency unit (cents).       |
| `currency`                  | string (ISO 4217) | 3-letter currency code, e.g. `"USD"`.                    |
| `provider_payment_id`         | string / null | The payment provider's own id for the charge.             |
| `failure_reason`               | string / null | Machine-readable reason the charge failed, if it did.      |

**Error responses**

All error responses share this shape:

```json
{ "error": "human readable message" }
```

| Status | When it happens                                                                 |
|--------|------------------------------------------------------------------------------------|
| `400`  | Missing/invalid `X-User-Id` header, the cart has no items, or the user has no saved payment method. |
| `402`  | The request was valid but the payment provider declined the charge (e.g. card declined, insufficient funds). The cart remains `open`. |
| `403`  | The cart exists but does not belong to the requesting user.                          |
| `404`  | No cart exists with the given `cart_id`.                                              |
| `409`  | The cart has already been successfully paid for.                                       |
| `500`  | Unexpected server error.                                                                |

Example error body (`402`):
```json
{ "error": "Payment was declined: card_declined" }
```

**Behavioural notes**

* **Idempotency.** Send the same `Idempotency-Key` if you need to safely
  retry a request (e.g. your HTTP client timed out and you don't know if
  the server actually processed it). The server will return the
  *original* result — it will not charge the card a second time. Without
  this header, a genuine retry after a real failure is treated as a new
  attempt (which is what you want if the first attempt actually failed).
* **Retrying after a decline.** A `402` response does not lock the cart.
  Update the user's saved payment method and `POST` to the same endpoint
  again to retry.
* **Paying twice.** Once a cart has been successfully paid (`200` once),
  further calls return `409` — a cart can only be paid for once.

### curl examples

**Successful payment**

```bash
curl -i -X POST http://127.0.0.1:5000/api/v1/carts/1/pay \
  -H "X-User-Id: 1" \
  -H "Idempotency-Key: 3f8e2b7a-9c1d-4e2a-8b3f-1a2b3c4d5e6f"
```

**Missing auth header → 400**

```bash
curl -i -X POST http://127.0.0.1:5000/api/v1/carts/1/pay
```

**Paying for a cart that isn't yours → 403**

```bash
curl -i -X POST http://127.0.0.1:5000/api/v1/carts/1/pay \
  -H "X-User-Id: 999"
```

**Non-existent cart → 404**

```bash
curl -i -X POST http://127.0.0.1:5000/api/v1/carts/999999/pay \
  -H "X-User-Id: 1"
```

**Declined card → 402**

Requires a `payment_methods.provider_card_token` value ending in
`_declined` or `_insufficient_funds` for the mock provider to reject it, User 2 has cart 2 but the payment method is mock to be declined 
(see `app/payment_provider.py`):

```bash
curl -i -X POST http://127.0.0.1:5000/api/v1/carts/2/pay \
  -H "X-User-Id: 2"
```

**Already-paid cart → 409**

```bash
# First call succeeds (200)...
curl -i -X POST http://127.0.0.1:5000/api/v1/carts/1/pay -H "X-User-Id: 1"
# ...second call on the same cart returns 409
curl -i -X POST http://127.0.0.1:5000/api/v1/carts/1/pay -H "X-User-Id: 1"
```

**Safe retry with an idempotency key**

```bash
KEY=$(uuidgen)

curl -i -X POST http://127.0.0.1:5000/api/v1/carts/1/pay \
  -H "X-User-Id: 1" \
  -H "Idempotency-Key: $KEY"

# Re-sending with the same key returns the original result instead of
# charging again, e.g. after a client-side timeout on the first call.
curl -i -X POST http://127.0.0.1:5000/api/v1/carts/1/pay \
  -H "X-User-Id: 1" \
  -H "Idempotency-Key: $KEY"
```

### Trying it with the provided `.http` file

See [`client.http`](client.http) in the repo root for a ready-to-run
set of requests (success, all error cases, retry-after-decline, and
idempotent replay). It works with the VS Code "REST Client" extension or
the JetBrains built-in HTTP client — open the file and click "Send
Request" above any block, top to bottom.