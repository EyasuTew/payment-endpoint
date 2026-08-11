"""
Application configuration.

Three configurations are provided:

* ``DevelopmentConfig`` - used when running the app locally against a real
  PostgreSQL instance (the database URL is read from the environment).
* ``ProductionConfig``  - same as development but with debug/testing flags
  forced off. In a real deployment this would also pull secrets from a
  vault/secret-manager instead of a ``.env`` file.
* ``TestingConfig``     - used by the pytest suite. It defaults to an
  in-memory SQLite database so the tests can run anywhere without needing a
  real PostgreSQL server.

  Assumption: the task requires PostgreSQL for the running application,
  but requiring a live Postgres instance just to run `pytest` would make
  the test suite unnecessarily hard to run on a reviewer's machine. Since
  all database access goes through SQLAlchemy's ORM (no raw/PostgreSQL-only
  SQL is used in the code paths under test), SQLite is a safe stand-in for
  the test suite. The real app is still wired to PostgreSQL by default.
"""

import os


class BaseConfig:
    """Shared configuration values."""

    # Flask
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-key")
    JSON_SORT_KEYS = False

    # SQLAlchemy
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Logging
    LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")


class DevelopmentConfig(BaseConfig):
    DEBUG = True
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL",
        "postgresql+psycopg2://root:Alpha323@localhost:5432/payments_db",
    )


class ProductionConfig(BaseConfig):
    DEBUG = False
    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL")


class TestingConfig(BaseConfig):
    TESTING = True
    DEBUG = True
    # In-memory SQLite: fast, isolated, no external dependency for tests.
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"


CONFIG_BY_NAME = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "testing": TestingConfig,
}
