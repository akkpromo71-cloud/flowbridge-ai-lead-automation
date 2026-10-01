import os

import pytest
from alembic import command
from alembic.config import Config
from app.db import Database
from app.main import create_app
from app.models import Base, Operator
from app.security import password_hasher
from app.settings import ROOT, Settings
from db_safety import configured_test_url, verify_test_connection, verify_test_engine
from fastapi.testclient import TestClient
from sqlalchemy import text


@pytest.fixture(scope="session")
def migrated_database():
    test_url = configured_test_url()
    database = Database(test_url)
    try:
        verify_test_engine(database.engine, test_url)
        old = {key: os.environ.get(key) for key in ("DATABASE_URL", "MODE")}
        os.environ.update(DATABASE_URL=test_url, MODE="test")
        try:
            with database.engine.begin() as connection:
                verify_test_connection(connection, test_url)
                migration_config = Config(str(ROOT / "alembic.ini"))
                migration_config.attributes["connection"] = connection
                command.upgrade(migration_config, "head")
        finally:
            for key, value in old.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
        yield database
    finally:
        database.engine.dispose()


@pytest.fixture
def database(migrated_database):
    test_url = configured_test_url()
    verify_test_engine(migrated_database.engine, test_url)
    with migrated_database.engine.begin() as connection:
        verify_test_connection(connection, test_url)
        tables = ", ".join(f'"{name}"' for name in Base.metadata.tables)
        connection.execute(text(f"TRUNCATE {tables} CASCADE"))
    return migrated_database


@pytest.fixture
def settings():
    return Settings(
        _env_file=None,
        mode="test",
        database_url=configured_test_url(),
        internal_token="test-service-token-" + "x" * 32,
        n8n_webhook_token="test-webhook-token-" + "y" * 32,
        request_limit_per_minute=1000,
    )


@pytest.fixture
def app(database, settings):
    return create_app(settings)


@pytest.fixture
def client(app):
    with TestClient(app) as client:
        yield client


@pytest.fixture
def authenticated(client, database):
    with database.session.begin() as db:
        db.add(
            Operator(
                email="operator@example.com",
                display_name="Тестовый оператор",
                password_hash=password_hasher.hash("a-long-synthetic-password"),
            )
        )
    response = client.post(
        "/api/v1/auth/login",
        json={"email": "operator@example.com", "password": "a-long-synthetic-password"},
    )
    assert response.status_code == 200
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    return client
