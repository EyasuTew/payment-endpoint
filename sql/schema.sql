-- ---------------------------------------------------------------------------
-- Payment system schema (PostgreSQL)
--
-- This file is a human-readable reference of the schema the SQLAlchemy
-- models in app/models.py produce. In normal operation the tables are
-- created via SQLAlchemy (see README "Database setup"); this file is
-- provided as the explicit SQL deliverable and doubles as documentation.
--
-- Money columns are always an integer count of the smallest currency unit
-- (cents) plus a 3-letter currency code, to avoid floating point rounding
-- errors.
-- ---------------------------------------------------------------------------

-- ============================================================
-- Base entities (assumed to already exist in the system)
-- ============================================================

CREATE TABLE users (
    id         SERIAL PRIMARY KEY,
    email      VARCHAR(255) NOT NULL UNIQUE,
    name       VARCHAR(255) NOT NULL,
    created_at TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE TABLE products (
    id          SERIAL PRIMARY KEY,
    name        VARCHAR(255) NOT NULL,
    price_cents INTEGER      NOT NULL CHECK (price_cents >= 0),
    currency    CHAR(3)      NOT NULL DEFAULT 'USD'
);

CREATE TYPE cart_status AS ENUM ('open', 'paid', 'abandoned');

CREATE TABLE carts (
    id         SERIAL PRIMARY KEY,
    user_id    INTEGER     NOT NULL REFERENCES users (id),
    status     cart_status NOT NULL DEFAULT 'open',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ix_carts_user_id ON carts (user_id);

CREATE TABLE cart_items (
    id                SERIAL  PRIMARY KEY,
    cart_id           INTEGER NOT NULL REFERENCES carts (id) ON DELETE CASCADE,
    product_id        INTEGER NOT NULL REFERENCES products (id),
    quantity          INTEGER NOT NULL DEFAULT 1 CHECK (quantity > 0),
    -- Snapshot of the product's price at the time it was added to the
    -- cart, so later price changes don't retroactively change the cart.
    unit_price_cents  INTEGER NOT NULL CHECK (unit_price_cents >= 0)
);

CREATE INDEX ix_cart_items_cart_id ON cart_items (cart_id);

-- A user's saved, tokenized card. Only the opaque provider token is
-- stored - never raw card numbers/CVV (PCI-DSS scope reduction).
CREATE TABLE payment_methods (
    id                   SERIAL  PRIMARY KEY,
    user_id              INTEGER NOT NULL REFERENCES users (id),
    provider_card_token  VARCHAR(255) NOT NULL,
    is_default           BOOLEAN NOT NULL DEFAULT TRUE,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ix_payment_methods_user_id ON payment_methods (user_id);

-- ============================================================
-- Payment domain (built for this task)
-- ============================================================

CREATE TYPE order_status AS ENUM ('pending', 'paid', 'failed', 'cancelled');

-- A checkout attempt for a cart. A cart can produce at most one *paid*
-- order (enforced in application logic, since a cart legitimately gets
-- a fresh PENDING/FAILED order per retry until one succeeds).
CREATE TABLE orders (
    id                 SERIAL       PRIMARY KEY,
    user_id            INTEGER      NOT NULL REFERENCES users (id),
    cart_id            INTEGER      NOT NULL REFERENCES carts (id),
    status             order_status NOT NULL DEFAULT 'pending',
    total_amount_cents INTEGER      NOT NULL CHECK (total_amount_cents >= 0),
    currency           CHAR(3)      NOT NULL DEFAULT 'USD',
    created_at         TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX ix_orders_cart_id ON orders (cart_id);
CREATE INDEX ix_orders_user_id ON orders (user_id);

CREATE TYPE payment_status AS ENUM ('pending', 'succeeded', 'failed');

-- A single charge attempt against the payment provider for an order.
-- Kept separate from `orders` so a failed attempt can be retried
-- (a new row here) without losing the audit trail of earlier attempts.
CREATE TABLE payments (
    id                    SERIAL         PRIMARY KEY,
    order_id              INTEGER        NOT NULL REFERENCES orders (id),
    payment_method_id     INTEGER        NOT NULL REFERENCES payment_methods (id),
    provider              VARCHAR(50)    NOT NULL DEFAULT 'mock',
    provider_payment_id   VARCHAR(255),
    amount_cents          INTEGER        NOT NULL CHECK (amount_cents >= 0),
    currency              CHAR(3)        NOT NULL DEFAULT 'USD',
    status                payment_status NOT NULL DEFAULT 'pending',
    failure_reason        VARCHAR(255),
    -- Lets a client safely retry the "pay" HTTP call (e.g. after a
    -- network timeout) without risking a double charge.
    idempotency_key       VARCHAR(64)    NOT NULL,
    created_at            TIMESTAMPTZ    NOT NULL DEFAULT now(),
    updated_at            TIMESTAMPTZ    NOT NULL DEFAULT now(),

    CONSTRAINT uq_payments_order_idempotency_key UNIQUE (order_id, idempotency_key)
);

CREATE INDEX ix_payments_order_id ON payments (order_id);

-- Append-only audit trail of everything that happened to a payment
-- (created, provider called, succeeded/failed). Useful for support and
-- reconciliation, and something safe to log/inspect independently of the
-- mutable `payments` row.
CREATE TABLE payment_events (
    id         SERIAL      PRIMARY KEY,
    payment_id INTEGER     NOT NULL REFERENCES payments (id) ON DELETE CASCADE,
    event_type VARCHAR(50) NOT NULL,
    message    TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ix_payment_events_payment_id ON payment_events (payment_id);
