import logging
from types import SimpleNamespace

import httpx
import pytest
from app.communication import telegram_notify
from app.logging_privacy import PRIVATE_LOG_NAMESPACES, configure_private_logging
from app.main import create_app
from pydantic import SecretStr


@pytest.fixture(autouse=True)
def restore_transport_loggers():
    names = set(PRIVATE_LOG_NAMESPACES)
    names.update(
        name
        for name in list(logging.root.manager.loggerDict)
        if any(name.startswith(f"{prefix}.") for prefix in PRIVATE_LOG_NAMESPACES)
    )
    saved = {}
    for name in names:
        logger = logging.getLogger(name)
        saved[name] = (logger.level, logger.disabled, logger.propagate, logger.handlers[:])
        # Prove suppression even if prior tests/application startup disabled these logs.
        logger.disabled, logger.propagate = False, True
        logger.setLevel(logging.INFO)
    yield
    for name, (level, disabled, propagate, handlers) in saved.items():
        logger = logging.getLogger(name)
        logger.setLevel(level)
        logger.disabled, logger.propagate, logger.handlers = disabled, propagate, handlers


def test_app_suppresses_real_httpx_telegram_url_but_keeps_domain_error(
    settings, monkeypatch, caplog
):
    token = "123456:synthetic-secret-must-not-appear"
    requested = []

    def response(request):
        requested.append(str(request.url))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 42}})

    client_type = httpx.Client
    monkeypatch.setattr(
        "app.communication.httpx.Client",
        lambda **kwargs: client_type(transport=httpx.MockTransport(response), **kwargs),
    )
    caplog.set_level(logging.INFO)
    app = create_app(settings)
    try:
        result = telegram_notify(
            SimpleNamespace(
                mode="live",
                allow_external_sends=True,
                telegram_bot_token=SecretStr(token),
                telegram_chat_id="synthetic-chat",
                public_url="https://demo.invalid",
            ),
            "synthetic-lead-id",
        )
        logging.getLogger("lead_worker").error("worker_iteration_failed")
    finally:
        app.state.db.engine.dispose()
    assert result == {"provider": "telegram", "message_id": 42}
    assert requested == [f"https://api.telegram.org/bot{token}/sendMessage"]
    assert token not in caplog.text
    assert "api.telegram.org" not in caplog.text
    assert "worker_iteration_failed" in caplog.text


def test_access_and_transport_children_cannot_log_personal_query(caplog):
    caplog.set_level(logging.DEBUG)
    access = logging.getLogger("uvicorn.access")
    access.addHandler(caplog.handler)
    configure_private_logging()
    access.info("GET /api/v1/leads?search=synthetic.private@example.com HTTP/1.1 200")
    # A child created after configuration cannot propagate transport data to root.
    child = logging.getLogger("httpcore.synthetic_new_transport")
    try:
        child.setLevel(logging.DEBUG)
        child.warning("URL includes synthetic-private-token")
        assert "synthetic.private@example.com" not in caplog.text
        assert "synthetic-private-token" not in caplog.text
    finally:
        child.setLevel(logging.NOTSET)
