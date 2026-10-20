from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

import asyncpg

from surake.app.models.domain import (
    DecisionAction,
    DecisionProposal,
    ExecutionStatus,
    InvokeRequest,
    InvokeResponse,
    RequestStatus,
    TodoStatus,
    WorkerResult,
)


class NotFoundError(Exception):
    pass


class AuthorizationError(Exception):
    pass


class VersionConflictError(Exception):
    pass


class Repository:
    def __init__(self, pool: asyncpg.Pool, lease_seconds: int = 300) -> None:
        self.pool = pool
        self.lease_seconds = lease_seconds

    async def get_request_response(self, request_id: UUID, owner_id: str) -> dict[str, Any] | None:
        row = await self.pool.fetchrow(
            "SELECT owner_id, response_json FROM requests WHERE request_id=$1", request_id
        )
        if row is None:
            return None
        if row["owner_id"] != owner_id:
            raise AuthorizationError("request_id belongs to another owner")
        return row["response_json"]

    async def create_request(self, payload: InvokeRequest, request_id: UUID) -> bool:
        result = await self.pool.execute(
            """INSERT INTO requests(request_id, owner_id, project_id, user_query, status)
               VALUES($1,$2,$3,$4,$5) ON CONFLICT (request_id) DO NOTHING""",
            request_id,
            payload.owner_id,
            payload.project_id,
            payload.user_query,
            RequestStatus.RECEIVED,
        )
        return result.endswith("1")

    async def resolve_project(self, payload: InvokeRequest) -> asyncpg.Record:
        if payload.project_id is not None:
            row = await self.pool.fetchrow(
                "SELECT * FROM projects WHERE project_id=$1", payload.project_id
            )
            if row is None:
                raise NotFoundError("project not found")
            if row["owner_id"] != payload.owner_id:
                raise AuthorizationError("project belongs to another owner")
            return row

        if payload.external_thread_id:
            row = await self.pool.fetchrow(
                "SELECT * FROM projects WHERE owner_id=$1 AND external_thread_id=$2",
                payload.owner_id,
                payload.external_thread_id,
            )
            if row is not None:
                return row
            if not payload.create_project:
                raise NotFoundError("external project key not found")

        if not payload.create_project:
            row = await self.pool.fetchrow(
                """SELECT * FROM projects WHERE owner_id=$1 AND status='active'
                   ORDER BY updated_at DESC LIMIT 1""",
                payload.owner_id,
            )
            if row is not None:
                return row

        project_id = uuid4()
        name = payload.project_name or payload.user_query[:120]
        try:
            return await self.pool.fetchrow(
                """INSERT INTO projects(project_id, owner_id, external_thread_id, name)
                   VALUES($1,$2,$3,$4) RETURNING *""",
                project_id,
                payload.owner_id,
                payload.external_thread_id,
                name,
            )
        except asyncpg.UniqueViolationError:
            return await self.pool.fetchrow(
                "SELECT * FROM projects WHERE owner_id=$1 AND external_thread_id=$2",
                payload.owner_id,
                payload.external_thread_id,
            )

    async def attach_request_to_project(self, request_id: UUID, project_id: UUID) -> None:
        await self.pool.execute(
            """UPDATE requests SET project_id=$2, status=$3, updated_at=now()
               WHERE request_id=$1""",
            request_id,
            project_id,
            RequestStatus.CLASSIFYING,
        )

    async def project_snapshot(self, project_id: UUID, owner_id: str) -> dict[str, Any]:
        project = await self.pool.fetchrow(
            "SELECT * FROM projects WHERE project_id=$1", project_id
        )
        if project is None:
            raise NotFoundError("project not found")
        if project["owner_id"] != owner_id:
            raise AuthorizationError("project belongs to another owner")
        tasks = await self.pool.fetch(
            """SELECT task_id, title, description, task_summary, status, priority,
                      version, active_execution_id
               FROM tasks WHERE project_id=$1 AND status != 'cancelled'
               ORDER BY priority DESC, updated_at DESC LIMIT 50""",
            project_id,
        )
        contexts = await self.pool.fetch(
            """SELECT context_key, context_value, category, importance, version
               FROM project_contexts WHERE project_id=$1
               ORDER BY importance DESC, context_key""",
            project_id,
        )
        artifacts = await self.pool.fetch(
            """SELECT artifact_id, name, artifact_kind, media_type, metadata, created_at
               FROM artifacts WHERE project_id=$1
               ORDER BY created_at DESC LIMIT 50""",
            project_id,
        )
        return {
            "project": dict(project),
            "tasks": [dict(row) for row in tasks],
            "contexts": [dict(row) for row in contexts],
            "artifacts": [dict(row) for row in artifacts],
        }

    async def apply_decision(
        self, request_id: UUID, project_id: UUID, proposal: DecisionProposal, user_query: str
    ) -> tuple[UUID | None, UUID | None]:
        async with self.pool.acquire() as connection, connection.transaction():
            project_version = await connection.fetchval(
                "SELECT version FROM projects WHERE project_id=$1 FOR UPDATE", project_id
            )
            if project_version != proposal.snapshot.project_version:
                raise VersionConflictError("project snapshot is stale")

            task_id = proposal.task_id
            execution_id: UUID | None = None
            if proposal.action is DecisionAction.CREATE_TASK:
                task_id = uuid4()
                await connection.execute(
                    """INSERT INTO tasks(task_id, project_id, title, description, status)
                       VALUES($1,$2,$3,$4,'ready')""",
                    task_id,
                    project_id,
                    proposal.task_title,
                    user_query,
                )
                todos = proposal.todos or [user_query]
                for position, description in enumerate(todos):
                    await connection.execute(
                        """INSERT INTO todos(todo_id, task_id, description, status, position)
                           VALUES($1,$2,$3,$4,$5)""",
                        uuid4(),
                        task_id,
                        description,
                        TodoStatus.READY if position == 0 else TodoStatus.PENDING,
                        position,
                    )
            elif task_id is not None:
                task = await connection.fetchrow(
                    "SELECT project_id, version, status FROM tasks WHERE task_id=$1 FOR UPDATE", task_id
                )
                if task is None or task["project_id"] != project_id:
                    raise NotFoundError("decision referenced an invalid task")
                if proposal.snapshot.task_version != task["version"]:
                    raise VersionConflictError("task snapshot is stale")
                await connection.execute(
                    """INSERT INTO query_task_impacts(request_id, task_id, impact_type, summary)
                       VALUES($1,$2,$3,$4)""",
                    request_id,
                    task_id,
                    proposal.impact_type,
                    proposal.reason,
                )
                if proposal.requires_steering or proposal.action is DecisionAction.REQUEST_STEERING:
                    await connection.execute(
                        """INSERT INTO steering_requests(steering_id, request_id, task_id, instruction)
                           VALUES($1,$2,$3,$4)""",
                        uuid4(),
                        request_id,
                        task_id,
                        user_query,
                    )

            await connection.execute(
                """UPDATE requests SET status=$2, classification_json=$3, updated_at=now()
                   WHERE request_id=$1""",
                request_id,
                RequestStatus.QUEUED,
                proposal.model_dump(mode="json"),
            )
            await connection.execute(
                "UPDATE projects SET version=version+1, updated_at=now() WHERE project_id=$1",
                project_id,
            )
            await connection.execute(
                """INSERT INTO task_events(event_id, project_id, task_id, request_id,
                                             event_type, payload, reason)
                   VALUES($1,$2,$3,$4,$5,$6,$7)""",
                uuid4(),
                project_id,
                task_id,
                request_id,
                "decision_applied",
                proposal.model_dump(mode="json"),
                proposal.reason,
            )
            if task_id is not None:
                await connection.execute(
                    """INSERT INTO outbox(event_type, payload) VALUES('task_ready', $1)""",
                    {"project_id": str(project_id), "task_id": str(task_id)},
                )
            return task_id, execution_id

    async def claim_next_todo(
        self, project_id: UUID, task_id: UUID, request_id: UUID, worker_id: str
    ) -> dict[str, Any] | None:
        execution_id = uuid4()
        graph_thread_id = f"task:{task_id}"
        lease_expires_at = datetime.now(timezone.utc) + timedelta(seconds=self.lease_seconds)
        async with self.pool.acquire() as connection, connection.transaction():
            todo = await connection.fetchrow(
                """SELECT t.* FROM todos t
                   WHERE t.task_id=$1 AND t.status='ready'
                     AND NOT EXISTS (
                       SELECT 1 FROM jsonb_array_elements_text(t.depends_on) d
                       JOIN todos dep ON dep.todo_id=d::uuid
                       WHERE dep.status != 'completed'
                     )
                   ORDER BY t.position
                   FOR UPDATE SKIP LOCKED LIMIT 1""",
                task_id,
            )
            if todo is None:
                return None
            task = await connection.fetchrow(
                """SELECT version, status, active_execution_id FROM tasks
                   WHERE task_id=$1 FOR UPDATE""",
                task_id,
            )
            if task is None or (task["active_execution_id"] is not None and task["status"] == "in_progress"):
                return None
            fencing_token = todo["version"] + 1
            await connection.execute(
                """INSERT INTO executions(execution_id, request_id, project_id, task_id, todo_id,
                     graph_thread_id, fencing_token, status, started_at, heartbeat_at)
                   VALUES($1,$2,$3,$4,$5,$6,$7,$8,now(),now())""",
                execution_id,
                request_id,
                project_id,
                task_id,
                todo["todo_id"],
                graph_thread_id,
                fencing_token,
                ExecutionStatus.RUNNING,
            )
            await connection.execute(
                """UPDATE todos SET status='in_progress', active_execution_id=$2,
                     version=$3, updated_at=now() WHERE todo_id=$1""",
                todo["todo_id"],
                execution_id,
                fencing_token,
            )
            await connection.execute(
                """UPDATE tasks SET status='in_progress', active_execution_id=$2,
                     lease_owner=$3, lease_expires_at=$4, version=version+1, updated_at=now()
                   WHERE task_id=$1""",
                task_id,
                execution_id,
                worker_id,
                lease_expires_at,
            )
            claimed = dict(todo)
            claimed.update(
                execution_id=execution_id,
                claimed_version=fencing_token,
                graph_thread_id=graph_thread_id,
            )
            return claimed

    async def commit_worker_result(self, result: WorkerResult) -> None:
        terminal = {TodoStatus.COMPLETED, TodoStatus.BLOCKED, TodoStatus.FAILED}
        if result.status not in terminal:
            raise ValueError("worker result must have a terminal bounded-step status")
        async with self.pool.acquire() as connection, connection.transaction():
            updated = await connection.fetchrow(
                """UPDATE todos SET status=$4, result_summary=$5, artifact_refs=$6,
                       active_execution_id=NULL, version=version+1, updated_at=now()
                   WHERE todo_id=$1 AND task_id=$2 AND active_execution_id=$3 AND version=$7
                   RETURNING todo_id""",
                result.todo_id,
                result.task_id,
                result.execution_id,
                result.status,
                result.result_summary,
                result.artifacts,
                result.claimed_version,
            )
            if updated is None:
                raise VersionConflictError("worker no longer owns this todo")
            execution_updated = await connection.execute(
                """UPDATE executions SET status=$2, finished_at=now()
                   WHERE execution_id=$1 AND fencing_token=$3 AND status='running'""",
                result.execution_id,
                ExecutionStatus.COMPLETED if result.status is TodoStatus.COMPLETED else ExecutionStatus.FAILED,
                result.claimed_version,
            )
            if not execution_updated.endswith("1"):
                raise VersionConflictError("execution fencing token is stale")
            await connection.execute(
                """UPDATE tasks SET active_execution_id=NULL, lease_owner=NULL,
                       lease_expires_at=NULL, status=CASE
                         WHEN EXISTS(SELECT 1 FROM todos WHERE task_id=$1 AND status IN ('ready','pending','in_progress'))
                         THEN 'ready'
                         WHEN EXISTS(SELECT 1 FROM todos WHERE task_id=$1 AND status IN ('blocked','failed'))
                         THEN 'blocked' ELSE 'completed' END,
                       version=version+1, updated_at=now()
                   WHERE task_id=$1 AND active_execution_id=$2""",
                result.task_id,
                result.execution_id,
            )
            await connection.execute(
                """UPDATE todos candidate SET status='ready', updated_at=now()
                   WHERE candidate.task_id=$1 AND candidate.status='pending'
                     AND NOT EXISTS (
                       SELECT 1 FROM jsonb_array_elements_text(candidate.depends_on) d
                       JOIN todos dep ON dep.todo_id=d::uuid WHERE dep.status != 'completed'
                     )""",
                result.task_id,
            )
            project_id = await connection.fetchval(
                "SELECT project_id FROM tasks WHERE task_id=$1", result.task_id
            )
            await connection.execute(
                """INSERT INTO task_events(event_id, project_id, task_id, todo_id, execution_id,
                                             event_type, payload, reason)
                   VALUES($1,$2,$3,$4,$5,'todo_finished',$6,$7)""",
                uuid4(),
                project_id,
                result.task_id,
                result.todo_id,
                result.execution_id,
                result.model_dump(mode="json"),
                result.result_summary[:2000] or "bounded worker completed",
            )

    async def save_response(self, response: InvokeResponse) -> None:
        await self.pool.execute(
            """UPDATE requests SET response_json=$2, status=$3, updated_at=now()
               WHERE request_id=$1""",
            response.request_id,
            response.model_dump(mode="json"),
            RequestStatus.COMPLETED,
        )

    async def save_initiation_context(
        self,
        project_id: UUID,
        owner_id: str,
        project_context: dict[str, Any],
        preferences: dict[str, Any],
        source_request_id: UUID,
    ) -> None:
        if not project_context and not preferences:
            return
        async with self.pool.acquire() as connection, connection.transaction():
            owner = await connection.fetchval(
                "SELECT owner_id FROM projects WHERE project_id=$1 FOR UPDATE", project_id
            )
            if owner != owner_id:
                raise AuthorizationError("project belongs to another owner")
            for category, values in (("fact", project_context), ("preference", preferences)):
                for key, value in values.items():
                    await self._upsert_context_row(
                        connection, project_id, key, value, category, 8, source_request_id
                    )
            await connection.execute(
                "UPDATE projects SET version=version+1, updated_at=now() WHERE project_id=$1",
                project_id,
            )
            await connection.execute(
                """INSERT INTO task_events(event_id, project_id, request_id, event_type, payload, reason)
                   VALUES($1,$2,$3,'context_saved',$4,$5)""",
                uuid4(),
                project_id,
                source_request_id,
                {"context_keys": list(project_context), "preference_keys": list(preferences)},
                "Saved durable context supplied when the project was initiated.",
            )

    @staticmethod
    async def _upsert_context_row(
        connection: Any,
        project_id: UUID,
        key: str,
        value: Any,
        category: str,
        importance: int,
        source_request_id: UUID | None,
    ) -> asyncpg.Record:
        row = await connection.fetchrow(
            """INSERT INTO project_contexts(
                   context_id, project_id, context_key, context_value, category,
                   importance, source_request_id)
               VALUES($1,$2,$3,$4,$5,$6,$7)
               ON CONFLICT(project_id, context_key) DO UPDATE SET
                   context_value=EXCLUDED.context_value,
                   category=EXCLUDED.category,
                   importance=EXCLUDED.importance,
                   source_request_id=EXCLUDED.source_request_id,
                   version=project_contexts.version+1,
                   updated_at=now()
               RETURNING *""",
            uuid4(),
            project_id,
            key,
            value,
            category,
            importance,
            source_request_id,
        )
        if row is None:
            raise RuntimeError("context upsert did not return a row")
        return row

    async def upsert_project_context(
        self,
        project_id: UUID,
        owner_id: str,
        key: str,
        value: Any,
        category: str,
        importance: int,
    ) -> dict[str, Any]:
        async with self.pool.acquire() as connection, connection.transaction():
            owner = await connection.fetchval(
                "SELECT owner_id FROM projects WHERE project_id=$1 FOR UPDATE", project_id
            )
            if owner is None:
                raise NotFoundError("project not found")
            if owner != owner_id:
                raise AuthorizationError("project belongs to another owner")
            row = await self._upsert_context_row(
                connection, project_id, key, value, category, importance, None
            )
            await connection.execute(
                "UPDATE projects SET version=version+1, updated_at=now() WHERE project_id=$1",
                project_id,
            )
            await connection.execute(
                """INSERT INTO task_events(event_id, project_id, event_type, payload, reason)
                   VALUES($1,$2,'context_saved',$3,$4)""",
                uuid4(),
                project_id,
                {"key": key, "category": category, "importance": importance},
                f"Saved durable project context: {key}",
            )
            return dict(row)

    async def list_projects(self, owner_id: str) -> list[dict[str, Any]]:
        rows = await self.pool.fetch(
            """SELECT p.*,
                      count(DISTINCT t.task_id)::int AS task_count,
                      count(DISTINCT a.artifact_id)::int AS artifact_count,
                      count(DISTINCT c.context_id)::int AS context_count
               FROM projects p
               LEFT JOIN tasks t ON t.project_id=p.project_id
               LEFT JOIN artifacts a ON a.project_id=p.project_id
               LEFT JOIN project_contexts c ON c.project_id=p.project_id
               WHERE p.owner_id=$1
               GROUP BY p.project_id
               ORDER BY p.updated_at DESC""",
            owner_id,
        )
        return [dict(row) for row in rows]

    async def observability_snapshot(self, project_id: UUID, owner_id: str) -> dict[str, Any]:
        snapshot = await self.project_snapshot(project_id, owner_id)
        todos = await self.pool.fetch(
            """SELECT td.* FROM todos td
               JOIN tasks t ON t.task_id=td.task_id
               WHERE t.project_id=$1 ORDER BY td.task_id, td.position""",
            project_id,
        )
        contexts = await self.pool.fetch(
            """SELECT context_id, context_key, context_value, category, importance,
                      version, created_at, updated_at
               FROM project_contexts WHERE project_id=$1
               ORDER BY importance DESC, context_key""",
            project_id,
        )
        artifacts = await self.pool.fetch(
            """SELECT a.*,
                      COALESCE(jsonb_agg(jsonb_build_object(
                          'target_artifact_id', ar.target_artifact_id,
                          'relation_type', ar.relation_type,
                          'target_name', target.name,
                          'target_kind', target.artifact_kind
                      )) FILTER (WHERE ar.target_artifact_id IS NOT NULL), '[]'::jsonb) AS relations
               FROM artifacts a
               LEFT JOIN artifact_relations ar ON ar.source_artifact_id=a.artifact_id
               LEFT JOIN artifacts target ON target.artifact_id=ar.target_artifact_id
               WHERE a.project_id=$1
               GROUP BY a.artifact_id
               ORDER BY a.created_at DESC""",
            project_id,
        )
        events = await self.pool.fetch(
            """SELECT event_id, task_id, todo_id, request_id, execution_id,
                      event_type, payload, reason, created_at
               FROM task_events WHERE project_id=$1
               ORDER BY created_at DESC LIMIT 100""",
            project_id,
        )
        executions = await self.pool.fetch(
            """SELECT execution_id, task_id, todo_id, status, attempt, started_at,
                      heartbeat_at, finished_at, error_code, error_summary
               FROM executions WHERE project_id=$1
               ORDER BY created_at DESC LIMIT 100""",
            project_id,
        )
        task_rows = snapshot["tasks"]
        todos_by_task: dict[UUID, list[dict[str, Any]]] = {}
        for todo in todos:
            todos_by_task.setdefault(todo["task_id"], []).append(dict(todo))
        for task in task_rows:
            task["todos"] = todos_by_task.get(task["task_id"], [])
        status_counts = await self.pool.fetchrow(
            """SELECT
                 (SELECT count(*)::int FROM tasks WHERE project_id=$1) AS tasks,
                 (SELECT count(*)::int FROM tasks WHERE project_id=$1 AND status='completed') AS completed_tasks,
                 (SELECT count(*)::int FROM executions WHERE project_id=$1 AND status='running') AS running_executions,
                 (SELECT count(*)::int FROM artifacts WHERE project_id=$1) AS artifacts""",
            project_id,
        )
        return {
            "project": snapshot["project"],
            "stats": dict(status_counts),
            "tasks": task_rows,
            "contexts": [dict(row) for row in contexts],
            "artifacts": [dict(row) for row in artifacts],
            "events": [dict(row) for row in events],
            "executions": [dict(row) for row in executions],
        }

    async def bounded_worker_context(
        self, project_id: UUID, task_id: UUID
    ) -> dict[str, Any]:
        task = await self.pool.fetchrow(
            """SELECT title, description, task_summary FROM tasks
               WHERE task_id=$1 AND project_id=$2""",
            task_id,
            project_id,
        )
        if task is None:
            raise NotFoundError("task is not part of this project")
        contexts = await self.pool.fetch(
            """SELECT context_key, context_value, category, importance
               FROM project_contexts WHERE project_id=$1
               ORDER BY importance DESC, updated_at DESC LIMIT 50""",
            project_id,
        )
        artifacts = await self.pool.fetch(
            """SELECT artifact_id, name, artifact_kind, metadata
               FROM artifacts WHERE project_id=$1
               ORDER BY created_at DESC LIMIT 50""",
            project_id,
        )
        return {
            "task": dict(task),
            "durable_context": [dict(row) for row in contexts],
            "available_artifacts": [dict(row) for row in artifacts],
        }

    async def register_artifact(
        self,
        project_id: UUID,
        owner_id: str,
        name: str,
        artifact_kind: str,
        storage_ref: str,
        media_type: str,
        size_bytes: int,
        sha256: str,
        metadata: dict[str, Any],
        task_id: UUID | None = None,
        parent_artifact_id: UUID | None = None,
        target_jd_artifact_id: UUID | None = None,
    ) -> dict[str, Any]:
        artifact_id = uuid4()
        async with self.pool.acquire() as connection, connection.transaction():
            owner = await connection.fetchval(
                "SELECT owner_id FROM projects WHERE project_id=$1 FOR UPDATE", project_id
            )
            if owner is None:
                raise NotFoundError("project not found")
            if owner != owner_id:
                raise AuthorizationError("project belongs to another owner")
            related_ids = [item for item in (parent_artifact_id, target_jd_artifact_id) if item]
            if related_ids:
                count = await connection.fetchval(
                    "SELECT count(*) FROM artifacts WHERE project_id=$1 AND artifact_id=ANY($2::uuid[])",
                    project_id,
                    related_ids,
                )
                if count != len(set(related_ids)):
                    raise NotFoundError("related artifact is not part of this project")
            if task_id is not None:
                exists = await connection.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM tasks WHERE task_id=$1 AND project_id=$2)",
                    task_id,
                    project_id,
                )
                if not exists:
                    raise NotFoundError("task is not part of this project")
            row = await connection.fetchrow(
                """INSERT INTO artifacts(artifact_id, project_id, task_id, name, artifact_kind,
                                          storage_ref, media_type, size_bytes, sha256, metadata)
                   VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10) RETURNING *""",
                artifact_id,
                project_id,
                task_id,
                name,
                artifact_kind,
                storage_ref,
                media_type,
                size_bytes,
                sha256,
                metadata,
            )
            if parent_artifact_id:
                await connection.execute(
                    """INSERT INTO artifact_relations(source_artifact_id, target_artifact_id, relation_type)
                       VALUES($1,$2,'derived_from')""",
                    artifact_id,
                    parent_artifact_id,
                )
            if target_jd_artifact_id:
                await connection.execute(
                    """INSERT INTO artifact_relations(source_artifact_id, target_artifact_id, relation_type)
                       VALUES($1,$2,'tailored_for')""",
                    artifact_id,
                    target_jd_artifact_id,
                )
            await connection.execute(
                "UPDATE projects SET version=version+1, updated_at=now() WHERE project_id=$1",
                project_id,
            )
            await connection.execute(
                """INSERT INTO task_events(event_id, project_id, task_id, event_type, payload, reason)
                   VALUES($1,$2,$3,'artifact_registered',$4,$5)""",
                uuid4(),
                project_id,
                task_id,
                {"artifact_id": str(artifact_id), "kind": artifact_kind, "name": name},
                f"Stored local artifact: {name}",
            )
            return dict(row)

    async def get_artifact(self, artifact_id: UUID, project_id: UUID, owner_id: str) -> dict[str, Any]:
        row = await self.pool.fetchrow(
            """SELECT a.* FROM artifacts a JOIN projects p ON p.project_id=a.project_id
               WHERE a.artifact_id=$1 AND a.project_id=$2 AND p.owner_id=$3""",
            artifact_id,
            project_id,
            owner_id,
        )
        if row is None:
            raise NotFoundError("artifact not found")
        return dict(row)

    async def recover_expired_leases(self) -> int:
        async with self.pool.acquire() as connection, connection.transaction():
            rows = await connection.fetch(
                """SELECT task_id, active_execution_id FROM tasks
                   WHERE status='in_progress' AND lease_expires_at < now()
                   FOR UPDATE SKIP LOCKED"""
            )
            for row in rows:
                await connection.execute(
                    """UPDATE executions SET status='failed', finished_at=now(),
                           error_code='lease_expired', error_summary='Worker lease expired'
                       WHERE execution_id=$1 AND status='running'""",
                    row["active_execution_id"],
                )
                await connection.execute(
                    """UPDATE todos SET status='ready', active_execution_id=NULL,
                           version=version+1, updated_at=now()
                       WHERE active_execution_id=$1""",
                    row["active_execution_id"],
                )
                await connection.execute(
                    """UPDATE tasks SET status='ready', active_execution_id=NULL,
                           lease_owner=NULL, lease_expires_at=NULL, version=version+1,
                           updated_at=now() WHERE task_id=$1""",
                    row["task_id"],
                )
            return len(rows)
