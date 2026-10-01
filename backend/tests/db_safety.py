"""Fail-closed allowlist for destructive PostgreSQL test fixtures only."""

import ipaddress
import os
from collections.abc import Mapping

from sqlalchemy import text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError

EXPECTED_HOST = "127.0.0.1"
EXPECTED_PORT = 15432
EXPECTED_DATABASE = "ai_leads_test"


class DatabaseSafetyError(RuntimeError):
    """Messages deliberately omit supplied URLs, credentials and server responses."""


def _approved_url(value: str | URL | None) -> URL:
    if value is None or (isinstance(value, str) and not value.strip()):
        raise DatabaseSafetyError("TEST_DATABASE_URL must be explicitly configured")
    try:
        parsed = make_url(value)
    except (ArgumentError, TypeError, ValueError):
        # SQLAlchemy's ArgumentError includes the invalid URL; never echo it.
        raise DatabaseSafetyError("Invalid test database configuration") from None
    if (
        parsed.drivername != "postgresql+psycopg"
        or parsed.host != EXPECTED_HOST
        or parsed.port != EXPECTED_PORT
        or parsed.database != EXPECTED_DATABASE
        or not parsed.username
        or parsed.query
    ):
        raise DatabaseSafetyError("Test database configuration is outside the fixed allowlist")
    return parsed


def configured_test_url(environ: Mapping[str, str] | None = None) -> str:
    """There is no fallback, .env loading, suffix match or configurable allowlist."""
    value = (os.environ if environ is None else environ).get("TEST_DATABASE_URL")
    _approved_url(value)
    assert value is not None
    return value


def verify_test_engine(engine, configured_url: str) -> None:
    expected = _approved_url(configured_url)
    actual = _approved_url(engine.url)
    if actual != expected:
        raise DatabaseSafetyError("Test engine does not match explicit configuration")


def verify_test_connection(connection, configured_url: str) -> None:
    """Verify the actual server on the connection used for the next operation."""
    verify_test_engine(connection.engine, configured_url)
    try:
        database, address, port = connection.execute(
            text("SELECT current_database(), inet_server_addr()::text, inet_server_port()")
        ).one()
        correct = (
            database == EXPECTED_DATABASE
            and ipaddress.ip_interface(address).ip == ipaddress.ip_address(EXPECTED_HOST)
            and port == EXPECTED_PORT
        )
    except Exception:
        raise DatabaseSafetyError("Could not verify the actual test database identity") from None
    if not correct:
        raise DatabaseSafetyError("Actual test database identity does not match the allowlist")
