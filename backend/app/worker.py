"""Durable dispatch loop. n8n owns step sequencing; the worker only delivers IDs."""

import logging
import signal
import threading

import httpx

from app.db import Database
from app.deployment import ensure_database_mode
from app.jobs import acknowledge_dispatch, dispatch_claim, recover
from app.logging_privacy import configure_private_logging
from app.settings import Settings, get_settings

logger = logging.getLogger("lead_worker")
WEBHOOKS = {
    "lead_processing": "ai-lead-processing",
    "notification": "ai-lead-communication",
    "email_send": "ai-lead-communication",
    "followup_prepare": "ai-lead-communication",
}


def run_once(database: Database, settings: Settings, *, client=None) -> bool:
    recover(database, settings)
    job = dispatch_claim(database, settings)
    if job is None:
        return False
    path = WEBHOOKS.get(job.kind)
    if path is None:
        acknowledge_dispatch(
            database, job.id, job.generation, False, settings, error_code="unknown_job_kind"
        )
        return True
    if not settings.n8n_webhook_token.get_secret_value():
        acknowledge_dispatch(
            database, job.id, job.generation, False, settings, error_code="n8n_not_configured"
        )
        return True
    owned_client = client is None
    client = client or httpx.Client(timeout=httpx.Timeout(10), follow_redirects=False)
    try:
        response = client.post(
            f"{settings.n8n_base_url.rstrip('/')}/webhook/{path}",
            headers={"X-Webhook-Token": settings.n8n_webhook_token.get_secret_value()},
            json={
                "contract_version": "1",
                "job_id": job.id,
                "generation": job.generation,
                "kind": job.kind,
            },
        )
        accepted = 200 <= response.status_code < 300
        acknowledge_dispatch(
            database,
            job.id,
            job.generation,
            accepted,
            settings,
            error_code=None if accepted else "n8n_http_error",
        )
    except httpx.HTTPError:
        acknowledge_dispatch(
            database, job.id, job.generation, False, settings, error_code="n8n_unavailable"
        )
    finally:
        if owned_client:
            client.close()
    return True


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    configure_private_logging()
    settings = get_settings()
    database = Database(settings.database_url)
    try:
        ensure_database_mode(database, settings.mode)
        stop = threading.Event()
        signal.signal(signal.SIGINT, lambda *_: stop.set())
        signal.signal(signal.SIGTERM, lambda *_: stop.set())
        with httpx.Client(timeout=httpx.Timeout(10), follow_redirects=False) as client:
            while not stop.is_set():
                try:
                    processed = run_once(database, settings, client=client)
                except Exception:
                    # No exception text: providers/SQL may embed secrets or PII.
                    logger.error("worker_iteration_failed")
                    processed = False
                if not processed:
                    stop.wait(2)
    finally:
        database.engine.dispose()


if __name__ == "__main__":
    main()
