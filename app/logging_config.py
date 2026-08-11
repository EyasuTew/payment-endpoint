"""
Centralized logging configuration.

The goal is to have professional, structured, and consistent logs across
the whole application:

* Every log line includes a timestamp, log level, logger name and a
  per-request correlation id (``request_id``), so that all log lines
  belonging to the same HTTP request can be grepped/traced together -
  this is especially useful for payments, where being able to
  reconstruct "everything that happened for this one charge" is
  important for debugging and audits.
* Payment related events (attempt started, provider called, succeeded,
  failed) are logged at INFO level so they show up in normal operation,
  while unexpected errors are logged at ERROR level with the stack trace.
* No sensitive data (full card numbers, CVV, etc.) is ever logged. Only
  the opaque provider card token is logged, which is safe by design
  because it is not itself a usable card number.
"""

import logging
import sys
import uuid

from flask import g, has_request_context


class RequestIdLogFilter(logging.Filter):
    """Injects the current request's correlation id into every log record.

    Falls back to "-" when logging happens outside of a request context
    (e.g. during app start-up).
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if has_request_context():
            record.request_id = getattr(g, "request_id", "-")
        else:
            record.request_id = "-"
        return True


def configure_logging(app) -> None:
    """Attach a structured stream handler + request-id filter to the app.

    Also registers a ``before_request`` hook that assigns a short unique
    id to every incoming request, so it can be correlated across log
    lines and (optionally) returned to the caller for support purposes.
    """

    log_level = getattr(logging, app.config.get("LOG_LEVEL", "INFO").upper(), logging.INFO)

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | request_id=%(request_id)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )

    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(formatter)
    handler.addFilter(RequestIdLogFilter())

    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    # Avoid duplicate handlers if configure_logging is called more than
    # once (e.g. app factory invoked repeatedly in tests).
    root_logger.handlers = [handler]

    # Flask's own logger propagates up to the root logger (configured
    # above), so it doesn't need its own handler - adding one too would
    # print every log line twice.
    app.logger.handlers = []
    app.logger.propagate = True
    app.logger.setLevel(log_level)

    @app.before_request
    def _assign_request_id():
        g.request_id = uuid.uuid4().hex[:12]
