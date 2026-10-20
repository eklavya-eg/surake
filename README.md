# Surake

FastAPI project orchestrator based on the [target architecture](./project_orchestrator_architecture.md).

See [Current architecture](./ARCHITECTURE.md) for service diagrams, the request flow, durable memory, resume/JD artifact mapping, and the distinction between implemented features and remaining work.

## Services

- Orchestrator (`:8000`): PostgreSQL-backed canonical projects, tasks, todos, requests, executions, events, leases, fencing, and idempotency.
- Browser agent (`:8101`): bounded `browser-use` execution.
- PDF editor agent (`:8102`): local PDF rotation and merging with local artifact storage.

## Run

```powershell
docker compose up -d postgres
Copy-Item .env.example .env
uv sync
uv run surake
```

Start the agent services in separate terminals:

```powershell
uv run --project services\browser_agent browser-agent
uv run --project services\pdf_editor_agent pdf-editor-agent
```

Open the observability UI at `http://127.0.0.1:8000/ui`; OpenAPI is at `http://127.0.0.1:8000/docs`. The main endpoint is `POST /api/invoke`.

## Durable career-project context

Project initiation accepts `project_context` and `preferences`. Both are stored in PostgreSQL and included in future decision snapshots for every task in that project.

```json
{
  "owner_id": "local-user",
  "user_query": "Switch into backend engineering",
  "create_project": true,
  "project_name": "Career switch",
  "project_context": {"career_goal": "Backend engineer"},
  "preferences": {"locations": ["remote", "Bengaluru"], "role": "backend"}
}
```

The UI can update durable goals, facts, constraints, and job-sorting preferences. Local artifacts are registered in PostgreSQL with file metadata and lineage. Upload a base resume and job description, then upload each tailored resume with `derived_from` and `tailored_for` mappings. Files remain beneath `ARTIFACT_ROOT`; the database stores their searchable relationships.

## Tests

```powershell
uv run pytest -q
$env:RUN_POSTGRES_TESTS='1'; uv run pytest -q tests\test_postgres_integration.py
uv run --project services\browser_agent pytest -q services\browser_agent\tests
uv run --project services\pdf_editor_agent pytest -q services\pdf_editor_agent\tests
```

Artifacts are written beneath `ARTIFACT_ROOT`; PostgreSQL is the only canonical state store.
