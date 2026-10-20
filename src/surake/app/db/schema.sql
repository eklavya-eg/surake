CREATE TABLE IF NOT EXISTS projects (
    project_id UUID PRIMARY KEY,
    owner_id TEXT NOT NULL,
    external_thread_id TEXT,
    name TEXT NOT NULL,
    project_map TEXT NOT NULL DEFAULT '',
    version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
    status TEXT NOT NULL DEFAULT 'active',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (owner_id, external_thread_id)
);

CREATE TABLE IF NOT EXISTS requests (
    request_id UUID PRIMARY KEY,
    owner_id TEXT NOT NULL,
    project_id UUID REFERENCES projects(project_id),
    user_query TEXT NOT NULL,
    status TEXT NOT NULL,
    classification_json JSONB,
    response_json JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS tasks (
    task_id UUID PRIMARY KEY,
    project_id UUID NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    task_summary TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 0,
    version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
    active_execution_id UUID,
    lease_owner TEXT,
    lease_expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_tasks_project_status ON tasks(project_id, status, priority DESC);

CREATE TABLE IF NOT EXISTS todos (
    todo_id UUID PRIMARY KEY,
    task_id UUID NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE,
    description TEXT NOT NULL,
    requirements JSONB NOT NULL DEFAULT '[]'::jsonb,
    acceptance_criteria JSONB NOT NULL DEFAULT '[]'::jsonb,
    status TEXT NOT NULL,
    position INTEGER NOT NULL,
    depends_on JSONB NOT NULL DEFAULT '[]'::jsonb,
    version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
    active_execution_id UUID,
    result_summary TEXT,
    artifact_refs JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(task_id, position)
);
CREATE INDEX IF NOT EXISTS ix_todos_task_status ON todos(task_id, status, position);

CREATE TABLE IF NOT EXISTS executions (
    execution_id UUID PRIMARY KEY,
    request_id UUID REFERENCES requests(request_id),
    project_id UUID NOT NULL REFERENCES projects(project_id),
    task_id UUID REFERENCES tasks(task_id),
    todo_id UUID REFERENCES todos(todo_id),
    graph_thread_id TEXT NOT NULL,
    fencing_token INTEGER NOT NULL,
    status TEXT NOT NULL,
    attempt INTEGER NOT NULL DEFAULT 1,
    started_at TIMESTAMPTZ,
    heartbeat_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    error_code TEXT,
    error_summary TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_executions_task_status ON executions(task_id, status);

CREATE TABLE IF NOT EXISTS task_events (
    event_id UUID PRIMARY KEY,
    project_id UUID NOT NULL REFERENCES projects(project_id),
    task_id UUID REFERENCES tasks(task_id),
    todo_id UUID REFERENCES todos(todo_id),
    request_id UUID REFERENCES requests(request_id),
    execution_id UUID REFERENCES executions(execution_id),
    event_type TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    reason TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_task_events_project_created ON task_events(project_id, created_at);

CREATE TABLE IF NOT EXISTS query_task_impacts (
    request_id UUID NOT NULL REFERENCES requests(request_id),
    task_id UUID NOT NULL REFERENCES tasks(task_id),
    impact_type TEXT NOT NULL,
    summary TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY(request_id, task_id)
);

CREATE TABLE IF NOT EXISTS steering_requests (
    steering_id UUID PRIMARY KEY,
    request_id UUID NOT NULL REFERENCES requests(request_id),
    task_id UUID NOT NULL REFERENCES tasks(task_id),
    instruction TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    applied_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS outbox (
    outbox_id BIGSERIAL PRIMARY KEY,
    event_type TEXT NOT NULL,
    payload JSONB NOT NULL,
    published_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS external_action_guards (
    action_key TEXT PRIMARY KEY,
    task_id UUID NOT NULL REFERENCES tasks(task_id),
    execution_id UUID REFERENCES executions(execution_id),
    status TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS project_contexts (
    context_id UUID PRIMARY KEY,
    project_id UUID NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    context_key TEXT NOT NULL,
    context_value JSONB NOT NULL,
    category TEXT NOT NULL,
    importance INTEGER NOT NULL DEFAULT 5 CHECK (importance BETWEEN 1 AND 10),
    source_request_id UUID REFERENCES requests(request_id) ON DELETE SET NULL,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(project_id, context_key)
);
CREATE INDEX IF NOT EXISTS ix_project_contexts_project_category
    ON project_contexts(project_id, category, importance DESC);

CREATE TABLE IF NOT EXISTS artifacts (
    artifact_id UUID PRIMARY KEY,
    project_id UUID NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    task_id UUID REFERENCES tasks(task_id) ON DELETE SET NULL,
    name TEXT NOT NULL,
    artifact_kind TEXT NOT NULL,
    storage_ref TEXT NOT NULL UNIQUE,
    media_type TEXT NOT NULL,
    size_bytes BIGINT NOT NULL CHECK (size_bytes >= 0),
    sha256 TEXT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_artifacts_project_kind
    ON artifacts(project_id, artifact_kind, created_at DESC);

CREATE TABLE IF NOT EXISTS artifact_relations (
    source_artifact_id UUID NOT NULL REFERENCES artifacts(artifact_id) ON DELETE CASCADE,
    target_artifact_id UUID NOT NULL REFERENCES artifacts(artifact_id) ON DELETE CASCADE,
    relation_type TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY(source_artifact_id, target_artifact_id, relation_type),
    CHECK(source_artifact_id != target_artifact_id)
);
