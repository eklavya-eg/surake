from uuid import uuid4

from fastapi.testclient import TestClient

from browser_agent.main import app


def test_health() -> None:
    response = TestClient(app).get("/health")
    assert response.status_code == 200
    assert response.json()["agent"] == "browser"


def test_task_is_blocked_without_provider_key(monkeypatch) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    response = TestClient(app).post(
        "/v1/tasks",
        json={
            "project_id": str(uuid4()),
            "task_id": str(uuid4()),
            "todo_id": str(uuid4()),
            "instruction": "Open https://example.com",
        },
    )
    assert response.status_code == 200
    assert response.json()["status"] == "blocked"
