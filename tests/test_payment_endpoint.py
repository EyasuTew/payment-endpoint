"""
Tests for POST /api/v1/carts/<cart_id>/pay.

Each test seeds only what it needs via the `seed_data` fixture (see
conftest.py) plus small local tweaks, and asserts both the HTTP response
and the resulting database state where relevant.
"""

from app.models import Cart, CartStatus, Order, OrderStatus, Payment, PaymentStatus


def _pay(client, cart_id, user_id, idempotency_key=None):
    headers = {"X-User-Id": str(user_id)}
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    return client.post(f"/api/v1/carts/{cart_id}/pay", headers=headers)


class TestSuccessfulPayment:
    def test_pays_for_cart_successfully(self, client, db, seed_data):
        cart = seed_data["cart"]
        user = seed_data["user"]

        response = _pay(client, cart.id, user.id)

        assert response.status_code == 200
        body = response.get_json()
        assert body["status"] == "succeeded"
        assert body["amount_cents"] == 2000  # 2 x 1000 cents
        assert body["currency"] == "USD"
        assert body["provider_payment_id"] is not None
        assert body["failure_reason"] is None

        # Persisted state reflects the successful payment.
        refreshed_cart = db.session.get(Cart, cart.id)
        assert refreshed_cart.status == CartStatus.PAID

        order = db.session.get(Order, body["order_id"])
        assert order.status == OrderStatus.PAID

        payment = db.session.get(Payment, body["payment_id"])
        assert payment.status == PaymentStatus.SUCCEEDED

    def test_paying_twice_is_rejected(self, client, seed_data):
        cart = seed_data["cart"]
        user = seed_data["user"]

        first = _pay(client, cart.id, user.id)
        assert first.status_code == 200

        second = _pay(client, cart.id, user.id)
        assert second.status_code == 409
        assert "already" in second.get_json()["error"].lower()


class TestValidationErrors:
    def test_missing_user_id_header_returns_400(self, client, seed_data):
        response = client.post(f"/api/v1/carts/{seed_data['cart'].id}/pay")
        assert response.status_code == 400

    def test_cart_not_found_returns_404(self, client, seed_data):
        response = _pay(client, 999999, seed_data["user"].id)
        assert response.status_code == 404

    def test_paying_for_someone_elses_cart_returns_403(self, client, seed_data):
        response = _pay(client, seed_data["cart"].id, seed_data["other_user"].id)
        assert response.status_code == 403

    def test_empty_cart_returns_400(self, client, db, seed_data):
        cart = seed_data["cart"]
        # Remove the single seeded item to make the cart empty.
        for item in list(cart.items):
            db.session.delete(item)
        db.session.commit()

        response = _pay(client, cart.id, seed_data["user"].id)
        assert response.status_code == 400
        assert "no items" in response.get_json()["error"].lower() or "empty" in response.get_json()["error"].lower()

    def test_user_without_payment_method_returns_400(self, client, db, seed_data):
        # other_user has a cart of their own but no saved payment method.
        from app.models import Cart as CartModel
        from app.models import CartItem

        other_cart = CartModel(user_id=seed_data["other_user"].id, status=CartStatus.OPEN)
        db.session.add(other_cart)
        db.session.flush()
        db.session.add(
            CartItem(
                cart_id=other_cart.id,
                product_id=seed_data["product"].id,
                quantity=1,
                unit_price_cents=500,
            )
        )
        db.session.commit()

        response = _pay(client, other_cart.id, seed_data["other_user"].id)
        assert response.status_code == 400
        assert "payment method" in response.get_json()["error"].lower()


class TestProviderDeclines:
    def test_declined_card_returns_402_and_keeps_cart_open(self, client, db, seed_data):
        # tok_..._declined is treated by MockPaymentProvider as an
        # always-fail token (see app/payment_provider.py).
        seed_data["payment_method"].provider_card_token = "tok_visa_declined"
        db.session.commit()

        response = _pay(client, seed_data["cart"].id, seed_data["user"].id)

        assert response.status_code == 402
        assert "declined" in response.get_json()["error"].lower()

        refreshed_cart = db.session.get(Cart, seed_data["cart"].id)
        assert refreshed_cart.status == CartStatus.OPEN

    def test_retry_after_decline_can_succeed(self, client, db, seed_data):
        cart = seed_data["cart"]
        user = seed_data["user"]

        seed_data["payment_method"].provider_card_token = "tok_visa_declined"
        db.session.commit()
        first = _pay(client, cart.id, user.id)
        assert first.status_code == 402

        # User updates their card to a working one and retries.
        seed_data["payment_method"].provider_card_token = "tok_visa_ok"
        db.session.commit()
        second = _pay(client, cart.id, user.id)

        assert second.status_code == 200
        assert second.get_json()["status"] == "succeeded"


class TestIdempotency:
    def test_same_idempotency_key_does_not_double_charge(self, client, db, seed_data):
        cart = seed_data["cart"]
        user = seed_data["user"]

        first = _pay(client, cart.id, user.id, idempotency_key="retry-key-123")
        assert first.status_code == 200
        first_payment_id = first.get_json()["payment_id"]

        # Simulate the client retrying the same logical request (e.g.
        # after a network timeout) with the same idempotency key. Note:
        # a real client wouldn't normally retry after a 200, but this
        # verifies the replay-detection path returns the same payment
        # rather than creating/charging a new one.
        second = _pay(client, cart.id, user.id, idempotency_key="retry-key-123")
        assert second.status_code == 200
        assert second.get_json()["payment_id"] == first_payment_id

        payments = Payment.query.filter_by(order_id=first.get_json()["order_id"]).all()
        assert len(payments) == 1
