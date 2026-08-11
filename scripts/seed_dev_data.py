"""
Seed a small, deterministic set of demo data so the endpoint can be
exercised manually via curl / client.http without writing SQL by hand.

This is a dev convenience only - it is NOT part of the application and is
not imported by any app/service code.

Usage:
    python scripts/seed_dev_data.py

Creates (idempotently - safe to re-run):
    * User 1  "buyer@example.com"   with a working default card (tok_visa_ok)
    * User 2  "decliner@example.com" with a card that always gets declined
              (tok_visa_declined)
    * Product 1 "Wireless Mouse" ($10.00)
    * Cart 1 (belongs to user 1) with 2x Wireless Mouse  -> $20.00 total
    * Cart 2 (belongs to user 2) with 1x Wireless Mouse  -> $10.00 total
"""

import os
import sys

# Allow running as `python scripts/seed_dev_data.py` from the repo root
# without needing PYTHONPATH set manually.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models import Cart, CartItem, CartStatus, PaymentMethod, Product, User  # noqa: E402


def _get_or_create_user(email: str, name: str) -> User:
    user = User.query.filter_by(email=email).first()
    if user is None:
        user = User(email=email, name=name)
        db.session.add(user)
        db.session.flush()
    return user


def _get_or_create_product(name: str, price_cents: int) -> Product:
    product = Product.query.filter_by(name=name).first()
    if product is None:
        product = Product(name=name, price_cents=price_cents, currency="USD")
        db.session.add(product)
        db.session.flush()
    return product


def _get_or_create_open_cart(user: User, product: Product, quantity: int) -> Cart:
    cart = (
        Cart.query.filter_by(user_id=user.id, status=CartStatus.OPEN)
        .order_by(Cart.id.desc())
        .first()
    )
    if cart is None:
        cart = Cart(user_id=user.id, status=CartStatus.OPEN)
        db.session.add(cart)
        db.session.flush()
        db.session.add(
            CartItem(cart_id=cart.id, product_id=product.id, quantity=quantity, unit_price_cents=product.price_cents)
        )
    return cart


def _get_or_create_payment_method(user: User, token: str) -> PaymentMethod:
    payment_method = PaymentMethod.query.filter_by(user_id=user.id).first()
    if payment_method is None:
        payment_method = PaymentMethod(user_id=user.id, provider_card_token=token, is_default=True)
        db.session.add(payment_method)
    return payment_method


def main() -> None:
    app = create_app()
    with app.app_context():
        db.create_all()

        buyer = _get_or_create_user("buyer@example.com", "Buyer One")
        decliner = _get_or_create_user("decliner@example.com", "Decline Case")

        mouse = _get_or_create_product("Wireless Mouse", 1000)

        cart_1 = _get_or_create_open_cart(buyer, mouse, quantity=2)
        cart_2 = _get_or_create_open_cart(decliner, mouse, quantity=1)

        _get_or_create_payment_method(buyer, "tok_visa_ok")
        _get_or_create_payment_method(decliner, "tok_visa_declined")

        db.session.commit()

        print("Seed data ready:")
        print(f"  user_id={buyer.id}    (working card)   cart_id={cart_1.id}  -> POST /api/v1/carts/{cart_1.id}/pay")
        print(f"  user_id={decliner.id}    (declined card)  cart_id={cart_2.id}  -> POST /api/v1/carts/{cart_2.id}/pay")


if __name__ == "__main__":
    main()
