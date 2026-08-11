"""
Mock payment provider.

In production this module would be replaced by a real integration (e.g.
the Stripe/Adyen SDK) that actually charges the card behind
``provider_card_token``. For this task we simulate that call so the rest
of the system (order/payment bookkeeping, HTTP endpoint, error handling)
can be built and tested end-to-end without a real payment gateway.

Assumption on how success/failure is decided
----------------------------------------------
The mock is deterministic (not random) so that behaviour is easy to
reproduce in tests and demos:

* A card token ending in ``"_declined"`` always fails with reason
  ``"card_declined"``.
* A card token ending in ``"_insufficient_funds"`` always fails with
  reason ``"insufficient_funds"``.
* Any other token succeeds.

A real integration would instead inspect the provider's HTTP response
(status code / error payload) to decide the outcome.
"""

import logging
import uuid
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ChargeResult:
    """Outcome of a single charge attempt against the provider."""

    success: bool
    provider_payment_id: str | None = None
    failure_reason: str | None = None


class MockPaymentProvider:
    """A stand-in for a real payment gateway client."""

    #: Token suffixes that are treated as "always fail" for deterministic
    #: testing/demo purposes. Mapped to the failure reason returned.
    _FAILURE_TOKEN_SUFFIXES = {
        "_declined": "card_declined",
        "_insufficient_funds": "insufficient_funds",
    }

    def charge(self, card_token: str, amount_cents: int, currency: str, idempotency_key: str) -> ChargeResult:
        """
        Attempt to charge ``amount_cents`` (in ``currency``) to the card
        represented by ``card_token``.

        Note: we deliberately never log the raw ``card_token`` value -
        only whether the charge succeeded - to mirror how a real
        integration should avoid leaking sensitive payment identifiers
        into logs.
        """

        logger.info(
            "Calling payment provider: amount_cents=%s currency=%s idempotency_key=%s",
            amount_cents,
            currency,
            idempotency_key,
        )

        for suffix, reason in self._FAILURE_TOKEN_SUFFIXES.items():
            if card_token.endswith(suffix):
                logger.warning("Payment provider declined the charge: reason=%s", reason)
                return ChargeResult(success=False, failure_reason=reason)

        provider_payment_id = f"mock_ch_{uuid.uuid4().hex}"
        logger.info("Payment provider approved the charge: provider_payment_id=%s", provider_payment_id)
        return ChargeResult(success=True, provider_payment_id=provider_payment_id)
