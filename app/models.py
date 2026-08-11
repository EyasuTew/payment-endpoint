"""
SQLAlchemy models.

This module contains both the "base" entities the task said already exist
(User, Product, Cart, CartItem, PaymentMethod) and the new tables built to
support the payment flow (Order, Payment, PaymentEvent).

Design notes / assumptions
---------------------------
* Money is stored as an integer number of the smallest currency unit
  (cents) - ``amount_cents`` - plus a ``currency`` code. This avoids the
  classic floating point rounding problems that come from storing money
  as ``float``/``Numeric`` with implicit conversions.

* ``Order`` represents "a cart that is going through/has been through
  checkout". A cart can only ever produce **one** successful order - once
  an order tied to a cart succeeds, the cart is marked ``paid`` and can no
  longer be paid again. This models the real-world rule "you can't pay
  for the same cart twice".

* ``Payment`` represents a single charge *attempt* against the payment
  provider for an order. Modelling it separately from ``Order`` (rather
  than folding payment status into the order row) allows an order to be
  retried after a failed payment attempt (e.g. card declined, then the
  user tries again) while keeping a full audit trail of every attempt.

* ``PaymentEvent`` is an append-only audit log of everything that happened
  to a payment (created, provider called, succeeded, failed...). This is
  the kind of table a real payments system needs for support/debugging
  and reconciliation with the payment provider, and it also gives us
  something safe to log/inspect without touching the mutable ``Payment``
  row.

* An ``idempotency_key`` column on ``Payment`` lets the client safely
  retry the "pay for this cart" HTTP call (e.g. after a network timeout)
  without risking a double charge - the second call finds the existing
  payment row instead of creating (and charging) a new one.
"""

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    CheckConstraint,
    Enum,
    ForeignKey,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db


def _utcnow() -> datetime:
    """Timezone-aware "now", used as a default for timestamp columns."""
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class CartStatus(str, enum.Enum):
    OPEN = "open"          # user is still adding/removing products
    PAID = "paid"          # checkout succeeded, cart is now locked
    ABANDONED = "abandoned"


class OrderStatus(str, enum.Enum):
    PENDING = "pending"     # order created, payment not yet confirmed
    PAID = "paid"           # a payment attempt for this order succeeded
    FAILED = "failed"       # the (most recent) payment attempt failed
    CANCELLED = "cancelled"


class PaymentStatus(str, enum.Enum):
    PENDING = "pending"     # attempt created, provider not yet called
    SUCCEEDED = "succeeded"
    FAILED = "failed"


# ---------------------------------------------------------------------------
# Base entities (pre-existing parts of the system, per the task description)
# ---------------------------------------------------------------------------

class User(db.Model):
    """A registered shop user."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(db.String(255), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(db.String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(db.DateTime(timezone=True), default=_utcnow, nullable=False)

    carts: Mapped[list["Cart"]] = relationship(back_populates="user")
    payment_methods: Mapped[list["PaymentMethod"]] = relationship(back_populates="user")


class Product(db.Model):
    """A physical product that can be sold."""

    __tablename__ = "products"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(db.String(255), nullable=False)
    # Price is stored in cents (smallest currency unit) - see module docstring.
    price_cents: Mapped[int] = mapped_column(db.Integer, nullable=False)
    currency: Mapped[str] = mapped_column(db.String(3), nullable=False, default="USD")

    __table_args__ = (
        CheckConstraint("price_cents >= 0", name="ck_products_price_non_negative"),
    )


class Cart(db.Model):
    """A user's shopping cart."""

    __tablename__ = "carts"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    status: Mapped[CartStatus] = mapped_column(
        Enum(CartStatus, name="cart_status"), nullable=False, default=CartStatus.OPEN
    )
    created_at: Mapped[datetime] = mapped_column(db.DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        db.DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )

    user: Mapped["User"] = relationship(back_populates="carts")
    items: Mapped[list["CartItem"]] = relationship(back_populates="cart", cascade="all, delete-orphan")
    orders: Mapped[list["Order"]] = relationship(back_populates="cart")


class CartItem(db.Model):
    """A single product line inside a cart."""

    __tablename__ = "cart_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    cart_id: Mapped[int] = mapped_column(ForeignKey("carts.id"), nullable=False)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False)
    quantity: Mapped[int] = mapped_column(db.Integer, nullable=False, default=1)
    # Snapshot of the unit price at the time it was added to the cart, so
    # that later price changes on the Product don't retroactively change
    # what a cart is worth.
    unit_price_cents: Mapped[int] = mapped_column(db.Integer, nullable=False)

    cart: Mapped["Cart"] = relationship(back_populates="items")
    product: Mapped["Product"] = relationship()

    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_cart_items_quantity_positive"),
        CheckConstraint("unit_price_cents >= 0", name="ck_cart_items_price_non_negative"),
    )


class PaymentMethod(db.Model):
    """
    A saved card for a user, represented only by an opaque token issued by
    the external payment provider (e.g. Stripe). We deliberately never
    store raw card numbers/CVV - that is the entire point of using a
    tokenized payment provider, and is required for PCI-DSS compliance.
    """

    __tablename__ = "payment_methods"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    provider_card_token: Mapped[str] = mapped_column(db.String(255), nullable=False)
    is_default: Mapped[bool] = mapped_column(db.Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(db.DateTime(timezone=True), default=_utcnow, nullable=False)

    user: Mapped["User"] = relationship(back_populates="payment_methods")


# ---------------------------------------------------------------------------
# Payment domain (the part built for this task)
# ---------------------------------------------------------------------------

class Order(db.Model):
    """
    A checkout attempt for a cart. Created the moment a payment is
    initiated, before we know whether it will succeed.
    """

    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    cart_id: Mapped[int] = mapped_column(ForeignKey("carts.id"), nullable=False)
    status: Mapped[OrderStatus] = mapped_column(
        Enum(OrderStatus, name="order_status"), nullable=False, default=OrderStatus.PENDING
    )
    total_amount_cents: Mapped[int] = mapped_column(db.Integer, nullable=False)
    currency: Mapped[str] = mapped_column(db.String(3), nullable=False, default="USD")
    created_at: Mapped[datetime] = mapped_column(db.DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        db.DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )

    user: Mapped["User"] = relationship()
    cart: Mapped["Cart"] = relationship(back_populates="orders")
    payments: Mapped[list["Payment"]] = relationship(back_populates="order", cascade="all, delete-orphan")

    __table_args__ = (
        CheckConstraint("total_amount_cents >= 0", name="ck_orders_total_non_negative"),
    )


class Payment(db.Model):
    """A single charge attempt against the payment provider for an order."""

    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), nullable=False)
    payment_method_id: Mapped[int] = mapped_column(ForeignKey("payment_methods.id"), nullable=False)

    # Free-form identifier of which provider handled this charge
    # (e.g. "mock", "stripe"). Kept as a plain string so a real provider
    # integration can be swapped in without a schema migration.
    provider: Mapped[str] = mapped_column(db.String(50), nullable=False, default="mock")
    # The provider's own id for this charge, once known.
    provider_payment_id: Mapped[str | None] = mapped_column(db.String(255), nullable=True)

    amount_cents: Mapped[int] = mapped_column(db.Integer, nullable=False)
    currency: Mapped[str] = mapped_column(db.String(3), nullable=False, default="USD")
    status: Mapped[PaymentStatus] = mapped_column(
        Enum(PaymentStatus, name="payment_status"), nullable=False, default=PaymentStatus.PENDING
    )
    failure_reason: Mapped[str | None] = mapped_column(db.String(255), nullable=True)

    # Lets a client safely retry the "pay" HTTP call without double
    # charging - see module docstring.
    idempotency_key: Mapped[str] = mapped_column(
        db.String(64), nullable=False, default=lambda: uuid.uuid4().hex
    )

    created_at: Mapped[datetime] = mapped_column(db.DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        db.DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )

    order: Mapped["Order"] = relationship(back_populates="payments")
    payment_method: Mapped["PaymentMethod"] = relationship()
    events: Mapped[list["PaymentEvent"]] = relationship(back_populates="payment", cascade="all, delete-orphan")

    __table_args__ = (
        CheckConstraint("amount_cents >= 0", name="ck_payments_amount_non_negative"),
        # Idempotency keys only need to be unique per order: two different
        # orders are free to reuse the same client-generated key.
        UniqueConstraint("order_id", "idempotency_key", name="uq_payments_order_idempotency_key"),
    )


class PaymentEvent(db.Model):
    """Append-only audit trail of everything that happened to a Payment."""

    __tablename__ = "payment_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    payment_id: Mapped[int] = mapped_column(ForeignKey("payments.id"), nullable=False)
    event_type: Mapped[str] = mapped_column(db.String(50), nullable=False)
    message: Mapped[str | None] = mapped_column(db.Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(db.DateTime(timezone=True), default=_utcnow, nullable=False)

    payment: Mapped["Payment"] = relationship(back_populates="events")
