from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from app import cli, worker
from app.deployment import ensure_database_mode
from app.main import create_app
from app.models import IntegrationState, Job, Lead, Operator
from fastapi.testclient import TestClient
from sqlalchemy import func, select


@pytest.mark.parametrize(
    "entrypoint", ["guard", "api", "seed-demo", "init-demo-operator", "create-operator", "worker"]
)
def test_mode_mismatch_blocks_startup_without_mutations(
    database, settings, monkeypatch, tmp_path, entrypoint
):
    with database.session.begin() as db:
        db.add(IntegrationState(name="deployment", state="ready", detail={"mode": "live"}))
        job = Job(kind="notification", dedup_key="existing-live-job", status="pending")
        db.add(job)
        db.flush()
        job_id = job.id
    demo_settings = settings.model_copy(update={"mode": "demo"})

    def forbidden(*args, **kwargs):
        pytest.fail("Mode guard must run before prompts, network or dispatch")

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(cli, "Settings", lambda: demo_settings)
    monkeypatch.setattr(cli, "Database", lambda _: database)
    monkeypatch.setattr(cli.getpass, "getpass", forbidden)
    monkeypatch.setattr(worker, "get_settings", lambda: demo_settings)
    monkeypatch.setattr(worker, "Database", lambda _: database)
    monkeypatch.setattr(worker, "run_once", forbidden)
    monkeypatch.setattr(worker.httpx, "Client", forbidden)
    with pytest.raises(RuntimeError, match="Database mode mismatch"):
        if entrypoint == "guard":
            ensure_database_mode(database, "demo")
        elif entrypoint == "api":
            with TestClient(create_app(demo_settings)):
                pytest.fail("Mismatched API must not start")
        elif entrypoint == "worker":
            worker.main()
        else:
            argv = ["app.cli", entrypoint]
            if entrypoint == "create-operator":
                argv.extend(["--email", "operator@example.com"])
            monkeypatch.setattr("sys.argv", argv)
            cli.main()
    with database.session() as db:
        assert db.get(IntegrationState, "deployment").detail == {"mode": "live"}
        job = db.get(Job, job_id)
        assert (job.status, job.generation, job.attempts) == ("pending", 0, 0)
        assert db.scalar(select(func.count()).select_from(Job)) == 1
        assert db.scalar(select(func.count()).select_from(Lead)) == 0
        assert db.scalar(select(func.count()).select_from(Operator)) == 0
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("modes", [("demo", "demo"), ("demo", "live")])
def test_concurrent_empty_database_bootstrap_has_one_immutable_mode(database, modes):
    barrier = Barrier(2)

    def bootstrap(mode):
        barrier.wait(timeout=5)
        try:
            ensure_database_mode(database, mode)
            return mode, True
        except RuntimeError as error:
            assert "Database mode mismatch" in str(error)
            return mode, False

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(bootstrap, modes))
    with database.session() as db:
        marker = db.get(IntegrationState, "deployment")
        assert marker.state == "ready"
        assert db.scalar(select(func.count()).select_from(IntegrationState)) == 1
        expected_winner = marker.detail["mode"]
    assert all(success == (mode == expected_winner) for mode, success in results)
    assert sum(success for _, success in results) == (2 if modes[0] == modes[1] else 1)
