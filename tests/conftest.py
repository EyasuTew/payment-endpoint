"""
Shared pytest fixtures.

Each test gets a fresh in-memory SQLite database (see TestingConfig) with
a small, consistent set of seed data: two users, a product, a cart with
one item, and a default payment method for the first user. Individual
tests then tweak this baseline (e.g. empty the cart, remove the payment
method) to exercise specific error paths.
"""

import pytest

from app import create_app
from app.extensions import db as _db
from app.models import Cart, CartItem, CartStatus, PaymentMethod, Product, User


@pytest.fixture()
def app():
    """Create a Flask app configured for testing, with tables created."""
    flask_app = create_app("testing")

    with flask_app.app_context():
        _db.create_all()
        yield flask_app
        _db.session.remove()
        _db.drop_all()


@pytest.fixture()
def client(app):
    """A test client bound to the app's request context helpers."""
    return app.test_client()


@pytest.fixture()
def db(app):
    """Expose the db session/instance inside the app context for tests."""
    return _db


@pytest.fixture()
def seed_data(db):
    """
    Baseline fixtures used by most tests:
      * user (id will be 1) - has a default payment method and a cart
        with one item worth 2 * 1000 cents = 2000 cents.
      * other_user (id will be 2) - has no cart/payment method, used to
        test that users can't pay for someone else's cart.
    """

    user = User(email="buyer@example.com", name="Buyer One")
    other_user = User(email="stranger@example.com", name="Stranger")
    db.session.add_all([user, other_user])
    db.session.flush()

    product = Product(name="Wireless Mouse", price_cents=1000, currency="USD")
    db.session.add(product)
    db.session.flush()

    cart = Cart(user_id=user.id, status=CartStatus.OPEN)
    db.session.add(cart)
    db.session.flush()

    cart_item = CartItem(cart_id=cart.id, product_id=product.id, quantity=2, unit_price_cents=product.price_cents)
    db.session.add(cart_item)

    payment_method = PaymentMethod(user_id=user.id, provider_card_token="tok_visa_ok", is_default=True)
    db.session.add(payment_method)

    db.session.commit()

    return {
        "user": user,
        "other_user": other_user,
        "product": product,
        "cart": cart,
        "payment_method": payment_method,
    }
