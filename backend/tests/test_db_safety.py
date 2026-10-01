"""Test-fixture safety checks; every engine/connection here is synthetic."""

from unittest.mock import MagicMock, Mock

import conftest
import pytest
from db_safety import DatabaseSafetyError, configured_test_url
from sqlalchemy.engine import make_url

ALLOWED_URL = "postgresql+psycopg://ai_leads_test@127.0.0.1:15432/ai_leads_test"


@pytest.fixture
def synthetic_database(monkeypatch):
    database = Mock()
    database.engine.url = make_url(ALLOWED_URL)
    connection = Mock()
    connection.engine = database.engine
    connection.execute.return_value.one.return_value = ("ai_leads_test", "127.0.0.1/32", 15432)
    context = MagicMock()
    context.__enter__.return_value = connection
    database.engine.connect.return_value = context
    database.engine.begin.return_value = context
    constructor = Mock(return_value=database)
    migration = Mock()
    monkeypatch.setattr(conftest, "Database", constructor)
    monkeypatch.setattr(conftest.command, "upgrade", migration)
    return database, connection, constructor, migration


def configure(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("TEST_DATABASE_URL", raising=False)
    else:
        monkeypatch.setenv("TEST_DATABASE_URL", value)
    # Exercise the previous module-level fallback too, without importing/reloading fixtures.
    monkeypatch.setattr(conftest, "TEST_URL", value or ALLOWED_URL, raising=False)


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        " ",
        "postgresql+psycopg://test@remote.invalid:5432/customer_test",
        "postgresql+psycopg://test@localhost:15432/ai_leads_test",
        "postgresql+psycopg://test@127.0.0.1:5432/ai_leads_test",
        "postgresql+psycopg://test@127.0.0.1/ai_leads_test",
        "postgresql+psycopg://test@127.0.0.1:15432/other_test",
        "postgresql+psycopg://test@127.0.0.1:15432/ai_leads_demo",
        "postgresql://test@127.0.0.1:15432/ai_leads_test",
        "postgresql+psycopg://127.0.0.1:15432/ai_leads_test",
        ALLOWED_URL + "?host=remote.invalid",
        ALLOWED_URL + "?dbname=ai_leads_demo",
        "not-a-database-url",
    ],
    ids=[
        "unset",
        "empty",
        "blank",
        "remote-suffix",
        "hostname-alias",
        "wrong-port",
        "implicit-port",
        "wrong-test-name",
        "demo",
        "wrong-driver",
        "missing-user",
        "redirect-host",
        "redirect-database",
        "invalid-url",
    ],
)
def test_unsafe_or_missing_target_never_constructs_database_or_migrates(
    monkeypatch, synthetic_database, value
):
    configure(monkeypatch, value)
    database, _connection, constructor, migration = synthetic_database
    fixture = conftest.migrated_database.__wrapped__()
    try:
        with pytest.raises(RuntimeError):
            next(fixture)
    finally:
        fixture.close()
    constructor.assert_not_called()
    migration.assert_not_called()
    with pytest.raises(DatabaseSafetyError):
        conftest.database.__wrapped__(database)
    database.engine.begin.assert_not_called()


@pytest.mark.parametrize(
    "identity",
    [
        ("ai_leads_demo", "127.0.0.1/32", 15432),
        ("ai_leads_test", "192.0.2.10", 15432),
        ("ai_leads_test", "127.0.0.1", 5432),
        ("ai_leads_test", None, 15432),
        ("ai_leads_test", "not-an-address", 15432),
    ],
    ids=["actual-database", "actual-host", "actual-port", "unix-socket", "invalid-address"],
)
def test_actual_database_mismatch_stops_before_migration(monkeypatch, synthetic_database, identity):
    configure(monkeypatch, ALLOWED_URL)
    _database, connection, _constructor, migration = synthetic_database
    connection.execute.return_value.one.return_value = identity
    fixture = conftest.migrated_database.__wrapped__()
    try:
        with pytest.raises(RuntimeError):
            next(fixture)
    finally:
        fixture.close()
    migration.assert_not_called()


def test_wrong_engine_never_connects_or_starts_cleanup(monkeypatch, synthetic_database):
    configure(monkeypatch, ALLOWED_URL)
    database, _connection, _constructor, migration = synthetic_database
    database.engine.url = make_url(ALLOWED_URL.replace("ai_leads_test", "ai_leads_demo"))
    fixture = conftest.migrated_database.__wrapped__()
    try:
        with pytest.raises(DatabaseSafetyError):
            next(fixture)
    finally:
        fixture.close()
    with pytest.raises(DatabaseSafetyError):
        conftest.database.__wrapped__(database)
    database.engine.connect.assert_not_called()
    database.engine.begin.assert_not_called()
    migration.assert_not_called()


def test_actual_database_mismatch_stops_before_every_truncate(monkeypatch, synthetic_database):
    configure(monkeypatch, ALLOWED_URL)
    database, connection, _constructor, _migration = synthetic_database
    connection.execute.return_value.one.return_value = ("ai_leads_demo", "127.0.0.1/32", 15432)
    with pytest.raises(RuntimeError):
        conftest.database.__wrapped__(database)
    assert all(
        "TRUNCATE" not in str(call.args[0]).upper() for call in connection.execute.call_args_list
    )


def test_allowed_target_rechecked_on_each_cleanup_and_environment_restored(
    monkeypatch, synthetic_database
):
    configure(monkeypatch, ALLOWED_URL)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("MODE", "demo")
    database, connection, _constructor, migration = synthetic_database

    def migrate(config, revision):
        assert conftest.os.environ["DATABASE_URL"] == ALLOWED_URL
        assert conftest.os.environ["MODE"] == "test"
        assert revision == "head"
        assert config.attributes["connection"] is connection
        assert "current_database()" in str(connection.execute.call_args.args[0])

    migration.side_effect = migrate
    fixture = conftest.migrated_database.__wrapped__()
    try:
        assert next(fixture) is database
        assert "DATABASE_URL" not in conftest.os.environ
        assert conftest.os.environ["MODE"] == "demo"
        assert conftest.database.__wrapped__(database) is database
        calls = [str(call.args[0]) for call in connection.execute.call_args_list]
        assert "current_database()" in calls[-2]
        assert calls[-1].startswith("TRUNCATE")
        connection.execute.return_value.one.return_value = ("ai_leads_demo", "127.0.0.1", 15432)
        with pytest.raises(DatabaseSafetyError):
            conftest.database.__wrapped__(database)
        assert (
            sum("TRUNCATE" in str(call.args[0]) for call in connection.execute.call_args_list) == 1
        )
    finally:
        fixture.close()
    database.engine.dispose.assert_called_once()


def test_identity_query_error_never_migrates_or_truncates(monkeypatch, synthetic_database):
    configure(monkeypatch, ALLOWED_URL)
    database, connection, _constructor, migration = synthetic_database
    connection.execute.side_effect = RuntimeError("synthetic sensitive server response")
    fixture = conftest.migrated_database.__wrapped__()
    try:
        with pytest.raises(DatabaseSafetyError) as error:
            next(fixture)
    finally:
        fixture.close()
    assert "sensitive" not in str(error.value)
    with pytest.raises(DatabaseSafetyError):
        conftest.database.__wrapped__(database)
    migration.assert_not_called()
    assert all("TRUNCATE" not in str(call.args[0]) for call in connection.execute.call_args_list)


def test_invalid_url_error_omits_synthetic_password():
    value = "postgresql+psycopg://test:synthetic-private-password@127.0.0.1:invalid/ai_leads_test"
    with pytest.raises(DatabaseSafetyError) as error:
        configured_test_url({"TEST_DATABASE_URL": value})
    assert "synthetic-private-password" not in str(error.value)
