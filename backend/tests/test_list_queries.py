"""List pagination must not grow SQL query count with every analysed lead."""

from uuid import uuid4

import pytest
from app.models import Analysis, Lead
from sqlalchemy import event


@pytest.mark.postgres
def test_large_analysed_list_uses_bounded_queries_and_stable_pages(authenticated, database):
    with database.session.begin() as db:
        for number in range(350):
            lead = Lead(
                id=str(uuid4()),
                reference=f"performance-{number}",
                intake_key=str(uuid4()),
                payload_hash="synthetic",
                name=f"Performance synthetic {number}",
                email="performance@example.com",
                original_message="Synthetic list fixture, no external calls",
            )
            db.add(lead)
            db.flush()
            analysis = Analysis(
                lead_id=lead.id,
                operation_key=f"{lead.id}:list-fixture",
                facts={"service_id": "lead_automation"},
                result={},
                config_version="synthetic",
                config_snapshot={},
                prompt_version="synthetic",
                schema_version="synthetic",
                model="fake-v1",
            )
            db.add(analysis)
            db.flush()
            lead.analysis_id = analysis.id
    statements = []

    def capture(*args):
        statements.append(args[2])

    event.listen(database.engine, "before_cursor_execute", capture)
    try:
        small = authenticated.get("/api/v1/admin/leads?page_size=5").json()
        small_queries = len(statements)
        statements.clear()
        large = authenticated.get("/api/v1/admin/leads?page_size=100").json()
        large_queries = len(statements)
        last = authenticated.get("/api/v1/admin/leads?page=4&page_size=100").json()
    finally:
        event.remove(database.engine, "before_cursor_execute", capture)
    assert small["total"] == large["total"] == last["total"] == 350
    assert len(small["items"]) == 5 and len(large["items"]) == 100
    assert len(last["items"]) == 50
    assert not ({x["id"] for x in large["items"]} & {x["id"] for x in last["items"]})
    assert large_queries == small_queries and large_queries <= 8
