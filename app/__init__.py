"""
Application factory.

Using the "create_app" factory pattern (rather than a module-level Flask
instance) keeps configuration flexible and, importantly, lets the test
suite spin up a fresh app + in-memory database per test session without
any global state leaking between tests.
"""

import logging
import os

from flask import Flask, jsonify
from werkzeug.exceptions import HTTPException

from app.config import CONFIG_BY_NAME
from app.extensions import db
from app.logging_config import configure_logging

logger = logging.getLogger(__name__)


def create_app(config_name: str | None = None) -> Flask:
    """Build and configure the Flask application."""

    config_name = config_name or os.environ.get("FLASK_CONFIG", "development")
    app = Flask(__name__)
    app.config.from_object(CONFIG_BY_NAME[config_name])

    configure_logging(app)

    db.init_app(app)

    # Import models so they are registered on `db.metadata` before any
    # `db.create_all()` call (used by tests / local bootstrapping).
    from app import models  # noqa: F401

    from app.routes.payment_routes import payment_bp
    app.register_blueprint(payment_bp)

    _register_error_handlers(app)

    logger.info("Application created with config=%s", config_name)
    return app


def _register_error_handlers(app: Flask) -> None:
    """Catch-all handler for anything not already handled by a route.

    Domain errors (PaymentDomainError subclasses) are handled locally in
    the route itself so they can be logged with request-specific context;
    this handler is the last line of defense against truly unexpected
    errors, ensuring the API never leaks a raw stack trace to the client.
    """

    @app.errorhandler(404)
    def handle_404(_error):
        return jsonify(error="Resource not found."), 404

    @app.errorhandler(Exception)
    def handle_unexpected_error(error):
        # Werkzeug HTTP exceptions (405, 400, etc.) already carry the
        # correct status code and a safe message - pass them through
        # unchanged instead of masking them as a generic 500.
        if isinstance(error, HTTPException):
            return jsonify(error=error.description), error.code

        logger.exception("Unhandled exception while processing request: %s", error)
        return jsonify(error="Internal server error."), 500
