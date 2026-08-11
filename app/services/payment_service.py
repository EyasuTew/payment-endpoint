"""
Payment service.

This module contains all the business logic for "pay for a cart":
validating the cart/user/payment method, calculating the total, creating
the Order/Payment bookkeeping rows, calling out to the payment provider,
and updating everything based on the result.

It is deliberately kept free of any Flask/HTTP concerns (no request/
response objects) so it can be unit tested directly and reused from other
entry points (e.g. a future async worker) if needed.
"""

import logging
import uuid

from app.errors import (
    CartAlreadyPaidError,
    CartNotFoundError,
    EmptyCartError,
    ForbiddenCartAccessError,
    NoPaymentMethodError,
    PaymentDeclinedError,
)
from app.extensions import db
from app.models import (
    Cart,
    CartStatus,
    Order,
    OrderStatus,
    Payment,
    PaymentEvent,
    PaymentMethod,
    PaymentStatus,
)
from app.payment_provider import MockPaymentProvider

logger = logging.getLogger(__name__)

# A single shared provider instance is fine here since the mock is
# stateless. A real provider client (e.g. wrapping an HTTP session) would
# likely be created once per app and injected the same way.
_payment_provider = MockPaymentProvider()


def calculate_cart_total(cart: Cart) -> tuple[int, str]:
    """
    Return ``(total_amount_cents, currency)`` for the given cart.

    Assumption: the task states a "payment total calculation service"
    already exists elsewhere in the system (tax, discounts, shipping,
    etc. presumably live there) and that this task does not need to
    reimplement it. This function stands in for that service with the
    simplest possible correct behaviour - summing `quantity * unit_price`
    across the cart's line items - so the endpoint is fully runnable and
    testable. Swapping in the real service means replacing the body of
    this function only; nothing else in the payment flow needs to change.
    """

    if not cart.items:
        raise EmptyCartError(f"Cart {cart.id} has no items to pay for.")

    currency = cart.items[0].product.currency if cart.items[0].product else "USD"
    total_cents = sum(item.quantity * item.unit_price_cents for item in cart.items)
    return total_cents, currency


def _get_default_payment_method(user_id: int) -> PaymentMethod:
    """Return the user's default saved card, or raise if they have none."""

    payment_method = (
        PaymentMethod.query.filter_by(user_id=user_id, is_default=True).first()
        or PaymentMethod.query.filter_by(user_id=user_id).first()
    )
    if payment_method is None:
        raise NoPaymentMethodError(f"User {user_id} has no saved payment method.")
    return payment_method


def _get_or_create_pending_order(cart: Cart, user_id: int, amount_cents: int, currency: str) -> Order:
    """
    Reuse an existing, not-yet-paid order for this cart if one exists
    (e.g. the user's first charge attempt failed and they are retrying),
    otherwise create a new one.

    Reusing the order keeps a single order as the "source of truth" for a
    given checkout, with multiple Payment rows underneath it representing
    each attempt - rather than creating a new order per retry.
    """

    existing_order = (
        Order.query.filter_by(cart_id=cart.id)
        .filter(Order.status.in_([OrderStatus.PENDING, OrderStatus.FAILED]))
        .order_by(Order.created_at.desc())
        .first()
    )
    if existing_order is not None:
        # The total could differ from a previous attempt if the cart
        # changed in between (not expected once checkout starts, but
        # cheap to keep in sync).
        existing_order.total_amount_cents = amount_cents
        existing_order.currency = currency
        existing_order.status = OrderStatus.PENDING
        return existing_order

    order = Order(
        user_id=user_id,
        cart_id=cart.id,
        total_amount_cents=amount_cents,
        currency=currency,
        status=OrderStatus.PENDING,
    )
    db.session.add(order)
    db.session.flush()  # populate order.id for the Payment FK below
    return order


def pay_for_cart(*, user_id: int, cart_id: int, idempotency_key: str | None) -> Payment:
    """
    Run the full "pay for a cart" flow and return the resulting Payment.

    Raises a ``PaymentDomainError`` subclass (see ``app.errors``) for any
    expected failure - not found, forbidden, empty cart, already paid, no
    payment method, or the provider declining the charge. The caller
    (the HTTP route) is responsible for translating these into HTTP
    responses.
    """

    cart = db.session.get(Cart, cart_id)
    if cart is None:
        raise CartNotFoundError(f"Cart {cart_id} was not found.")

    if cart.user_id != user_id:
        # Deliberately raised as "not found"-adjacent (403) rather than
        # leaking whether the cart id exists to a user who doesn't own it.
        raise ForbiddenCartAccessError(f"Cart {cart_id} does not belong to user {user_id}.")

    # --- Idempotency check -------------------------------------------------
    # If the caller supplied an Idempotency-Key and we already have a
    # Payment for *any* order tied to this cart with that same key, this
    # is a retried HTTP request (e.g. the client's first response was
    # lost to a network timeout even though the charge itself went
    # through) rather than a brand new payment attempt. Return the
    # existing result instead of charging the card again.
    #
    # This check intentionally runs before the "already paid" check
    # below, so that replaying the request that *caused* the cart to
    # become paid still returns the original successful result instead
    # of a confusing 409.
    if idempotency_key:
        existing_payment = (
            Payment.query.join(Order, Payment.order_id == Order.id)
            .filter(Order.cart_id == cart.id, Payment.idempotency_key == idempotency_key)
            .first()
        )
        if existing_payment is not None:
            logger.info(
                "Idempotent replay detected for cart_id=%s idempotency_key=%s - "
                "returning existing payment_id=%s without charging again.",
                cart.id,
                idempotency_key,
                existing_payment.id,
            )
            return existing_payment

    if cart.status == CartStatus.PAID:
        raise CartAlreadyPaidError(f"Cart {cart_id} has already been paid for.")

    amount_cents, currency = calculate_cart_total(cart)
    payment_method = _get_default_payment_method(user_id)

    order = _get_or_create_pending_order(cart, user_id, amount_cents, currency)

    payment = Payment(
        order_id=order.id,
        payment_method_id=payment_method.id,
        provider="mock",
        amount_cents=amount_cents,
        currency=currency,
        status=PaymentStatus.PENDING,
        idempotency_key=idempotency_key or uuid.uuid4().hex,
    )
    db.session.add(payment)
    db.session.flush()  # populate payment.id for the PaymentEvent FK below
    _record_event(payment, "payment_created", f"Payment attempt created for order {order.id}.")

    logger.info(
        "Starting payment attempt: payment_id=%s order_id=%s cart_id=%s user_id=%s amount_cents=%s currency=%s",
        payment.id, order.id, cart.id, user_id, amount_cents, currency,
    )

    _record_event(payment, "provider_call_started", "Calling payment provider.")
    result = _payment_provider.charge(
        card_token=payment_method.provider_card_token,
        amount_cents=amount_cents,
        currency=currency,
        idempotency_key=payment.idempotency_key,
    )

    if result.success:
        payment.status = PaymentStatus.SUCCEEDED
        payment.provider_payment_id = result.provider_payment_id
        order.status = OrderStatus.PAID
        cart.status = CartStatus.PAID
        _record_event(
            payment, "payment_succeeded",
            f"Charge succeeded, provider_payment_id={result.provider_payment_id}.",
        )
        db.session.commit()
        logger.info("Payment succeeded: payment_id=%s order_id=%s", payment.id, order.id)
        return payment

    # --- Failure path --------------------------------------------------
    payment.status = PaymentStatus.FAILED
    payment.failure_reason = result.failure_reason
    order.status = OrderStatus.FAILED
    # NOTE: the cart is intentionally left OPEN (not PAID) on failure, so
    # the user can fix their payment method and try again.
    _record_event(payment, "payment_failed", f"Charge declined, reason={result.failure_reason}.")
    db.session.commit()
    logger.warning(
        "Payment declined: payment_id=%s order_id=%s reason=%s",
        payment.id, order.id, result.failure_reason,
    )
    raise PaymentDeclinedError(f"Payment was declined: {result.failure_reason}")


def _record_event(payment: Payment, event_type: str, message: str) -> None:
    """Append an audit-trail row for a payment. Does not commit."""
    db.session.add(PaymentEvent(payment_id=payment.id, event_type=event_type, message=message))
