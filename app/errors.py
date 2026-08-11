"""
Domain-specific exceptions for the payment flow.

Keeping these as distinct exception classes (rather than e.g. returning
error strings/codes from the service layer) lets the HTTP layer translate
each one to the correct status code in a single place, while the service
layer stays focused on business rules and doesn't need to know anything
about HTTP.
"""


class PaymentDomainError(Exception):
    """Base class for all expected/handled payment errors."""

    #: Default HTTP status code for this error type. Subclasses override.
    http_status = 400

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class CartNotFoundError(PaymentDomainError):
    http_status = 404


class ForbiddenCartAccessError(PaymentDomainError):
    """Raised when a user tries to pay for a cart that isn't theirs."""

    http_status = 403


class EmptyCartError(PaymentDomainError):
    http_status = 400


class CartAlreadyPaidError(PaymentDomainError):
    http_status = 409


class NoPaymentMethodError(PaymentDomainError):
    http_status = 400


class PaymentDeclinedError(PaymentDomainError):
    """
    Raised when the order/payment records were created successfully but
    the payment provider declined the charge. Uses HTTP 402 Payment
    Required, which is the semantically correct status code for "the
    request was valid but the payment itself failed".
    """

    http_status = 402
