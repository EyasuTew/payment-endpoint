"""
HTTP endpoint(s) for the payment flow.

Only translates HTTP <-> the payment service; all business logic lives in
``app.services.payment_service``.

Authentication assumption
--------------------------
The task does not describe an authentication system, so a full one is out
of scope here. To keep the endpoint realistic (i.e. "pay for *my* cart",
not an open endpoint anyone can call for anyone's cart) it requires an
``X-User-Id`` header identifying the caller, and the service layer
verifies that the target cart actually belongs to that user. In a real
system this would instead come from a decoded auth token/session.
"""

import logging

from flask import Blueprint, jsonify, request

from app.errors import PaymentDomainError
from app.models import Payment
from app.services.payment_service import pay_for_cart

logger = logging.getLogger(__name__)

payment_bp = Blueprint("payments", __name__, url_prefix="/api/v1")


def _serialize_payment(payment: Payment) -> dict:
    """Build the JSON representation returned to API clients for a Payment."""
    return {
        "payment_id": payment.id,
        "order_id": payment.order_id,
        "status": payment.status.value,
        "amount_cents": payment.amount_cents,
        "currency": payment.currency,
        "provider_payment_id": payment.provider_payment_id,
        "failure_reason": payment.failure_reason,
    }


@payment_bp.route("/carts/<int:cart_id>/pay", methods=["POST"])
def pay_cart(cart_id: int):
    """
    Start (or idempotently retry) payment for the given cart.

    Headers:
        X-User-Id (required)     - id of the user paying for the cart.
        Idempotency-Key (optional) - client-generated key; replaying the
            same request with the same key will not double-charge the
            card, see ``payment_service.pay_for_cart``.

    Responses:
        200 - payment succeeded, body is the Payment representation.
        400 - malformed request / empty cart / no saved payment method.
        402 - the payment provider declined the charge.
        403 - the cart does not belong to the requesting user.
        404 - the cart does not exist.
        409 - the cart has already been paid for.
    """

    user_id_header = request.headers.get("X-User-Id")
    if not user_id_header or not user_id_header.isdigit():
        logger.info("Rejected payment request for cart_id=%s: missing/invalid X-User-Id header.", cart_id)
        return jsonify(error="Missing or invalid X-User-Id header."), 400

    user_id = int(user_id_header)
    idempotency_key = request.headers.get("Idempotency-Key")

    logger.info("Received payment request: cart_id=%s user_id=%s", cart_id, user_id)

    try:
        payment = pay_for_cart(user_id=user_id, cart_id=cart_id, idempotency_key=idempotency_key)
    except PaymentDomainError as exc:
        # Expected/handled failure - log at the appropriate level and
        # return the status code the exception carries (see app.errors).
        log_fn = logger.warning if exc.http_status >= 500 else logger.info
        log_fn("Payment request failed: cart_id=%s user_id=%s error=%s", cart_id, user_id, exc.message)
        return jsonify(error=exc.message), exc.http_status

    return jsonify(_serialize_payment(payment)), 200
