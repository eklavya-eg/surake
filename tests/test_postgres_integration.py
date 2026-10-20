import asyncio
import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from surake.app.app import app
from surake.app.core.config import get_settings
from surake.app.db.pool import Database
from surake.app.db.repository import Repository

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_TESTS") != "1",
    reason="set RUN_POSTGRES_TESTS=1 with PostgreSQL running",
)


def test_dashboard_is_available() -> None:
    with TestClient(app) as client:
        response = client.get("/ui")
        assert response.status_code == 200
        assert "Surake Control Room" in response.text


def test_invoke_is_persisted_and_idempotent() -> None:
    request_id = uuid4()
    owner_id = f"integration-{uuid4()}"
    body = {
        "request_id": str(request_id),
        "owner_id": owner_id,
        "user_query": "Prepare a concise implementation outline",
        "create_project": True,
        "project_name": "Integration test",
    }
    with TestClient(app) as client:
        response = client.post("/api/invoke", json=body)
        assert response.status_code == 202, response.text
        payload = response.json()
        assert payload["request_id"] == str(request_id)
        assert payload["status"] == "completed"

        duplicate = client.post("/api/invoke", json=body)
        assert duplicate.status_code == 202, duplicate.text
        assert duplicate.json()["duplicate"] is True
        assert duplicate.json()["task_id"] == payload["task_id"]

        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json()["database"] == "postgresql"


def test_project_memory_and_resume_lineage_are_persistent() -> None:
    owner_id = f"career-{uuid4()}"
    with TestClient(app) as client:
        created = client.post(
            "/api/invoke",
            json={
                "owner_id": owner_id,
                "user_query": "Switch into backend engineering",
                "create_project": True,
                "project_name": "Career switch",
                "project_context": {"career_goal": "Backend engineer"},
                "preferences": {"job_locations": ["remote", "Bengaluru"]},
            },
        )
        assert created.status_code == 202, created.text
        project_id = created.json()["project_id"]

        base = client.post(
            f"/api/projects/{project_id}/artifacts",
            data={"owner_id": owner_id, "artifact_kind": "resume_base", "label": "Master resume"},
            files={"file": ("resume.pdf", b"master resume", "application/pdf")},
        )
        assert base.status_code == 201, base.text
        jd = client.post(
            f"/api/projects/{project_id}/artifacts",
            data={"owner_id": owner_id, "artifact_kind": "job_description", "label": "Acme backend JD"},
            files={"file": ("acme.txt", b"backend python postgres", "text/plain")},
        )
        assert jd.status_code == 201, jd.text
        variant = client.post(
            f"/api/projects/{project_id}/artifacts",
            data={
                "owner_id": owner_id,
                "artifact_kind": "resume_variant",
                "label": "Acme tailored resume",
                "parent_artifact_id": base.json()["artifact_id"],
                "target_jd_artifact_id": jd.json()["artifact_id"],
            },
            files={"file": ("acme-resume.pdf", b"tailored resume", "application/pdf")},
        )
        assert variant.status_code == 201, variant.text

        snapshot = client.get(
            f"/api/projects/{project_id}/observability",
            params={"owner_id": owner_id},
        )
        assert snapshot.status_code == 200, snapshot.text
        payload = snapshot.json()
        context = {item["context_key"]: item["context_value"] for item in payload["contexts"]}
        assert context["career_goal"] == "Backend engineer"
        assert context["job_locations"] == ["remote", "Bengaluru"]
        tailored = next(item for item in payload["artifacts"] if item["artifact_kind"] == "resume_variant")
        assert {relation["relation_type"] for relation in tailored["relations"]} == {
            "derived_from",
            "tailored_for",
        }
        download = client.get(
            f"/api/projects/{project_id}/artifacts/{tailored['artifact_id']}/download",
            params={"owner_id": owner_id},
        )
        assert download.status_code == 200
        assert download.content == b"tailored resume"


@pytest.mark.asyncio
async def test_two_workers_cannot_claim_the_same_todo() -> None:
    settings = get_settings()
    database = Database(settings)
    await database.connect()
    await database.initialize_schema()
    pool = database.require_pool()
    repository = Repository(pool, settings.lease_seconds)
    project_id, task_id, todo_id, request_id = uuid4(), uuid4(), uuid4(), uuid4()
    try:
        await pool.execute(
            "INSERT INTO projects(project_id, owner_id, name) VALUES($1,'race-test','Race test')",
            project_id,
        )
        await pool.execute(
            """INSERT INTO requests(request_id, owner_id, project_id, user_query, status)
               VALUES($1,'race-test',$2,'Race','queued')""",
            request_id,
            project_id,
        )
        await pool.execute(
            """INSERT INTO tasks(task_id, project_id, title, description, status)
               VALUES($1,$2,'Race','Race','ready')""",
            task_id,
            project_id,
        )
        await pool.execute(
            """INSERT INTO todos(todo_id, task_id, description, status, position)
               VALUES($1,$2,'Only once','ready',0)""",
            todo_id,
            task_id,
        )
        claims = await asyncio.gather(
            repository.claim_next_todo(project_id, task_id, request_id, "worker-a"),
            repository.claim_next_todo(project_id, task_id, request_id, "worker-b"),
        )
        assert sum(claim is not None for claim in claims) == 1
    finally:
        await pool.execute("UPDATE tasks SET active_execution_id=NULL WHERE project_id=$1", project_id)
        await pool.execute(
            "UPDATE todos SET active_execution_id=NULL WHERE task_id IN (SELECT task_id FROM tasks WHERE project_id=$1)",
            project_id,
        )
        await pool.execute("DELETE FROM executions WHERE project_id=$1", project_id)
        await pool.execute("DELETE FROM requests WHERE project_id=$1", project_id)
        await pool.execute("DELETE FROM projects WHERE project_id=$1", project_id)
        await database.close()
