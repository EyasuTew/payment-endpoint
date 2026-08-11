"""
Shared Flask extension instances.

Kept in their own module (instead of inside `app/__init__.py`) so that
`models.py`, `services/*.py`, etc. can import `db` without triggering a
circular import with the application factory.
"""

from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()
