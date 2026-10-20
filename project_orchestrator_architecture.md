# Project Orchestrator Architecture --- Revised Design and Risk Review

## 1. Executive summary

Use LangGraph for reasoning workflows, delegation, checkpointing, and
bounded agent execution. Keep project management, task scheduling,
concurrency control, permissions, and authoritative state in the
application/database.

**Core invariant:** only validated, current, authorized state
transitions may change canonical project/task data.

JEv is the decision model. It proposes classifications and next actions;
it does not own state and must not bypass application validation.

## 2. Responsibilities and boundaries

  -----------------------------------------------------------------------
  Component               Owns                    Must not own
  ----------------------- ----------------------- -----------------------
  API / Agent class       Request validation,     Long-term canonical
                          authentication,         task state
                          idempotency, response   
                          shape                   

  Project manager         Project resolution,     LLM reasoning internals
                          task lifecycle,         
                          task-to-query mapping,  
                          conflict policy         

  JEv decision layer      Classification, task    Database writes or
                          routing recommendation, unvalidated IDs
                          subagent selection      
                          recommendation          

  Planner / context       Build focused task      Unbounded access to all
  builder                 plans and subagent      history by default
                          inputs                  

  Scheduler               Task eligibility,       Deciding task meaning
                          claims, concurrency     from raw user text
                          limits, retries         alone

  LangGraph worker        Execute bounded work,   Replacing the project's
                          checkpoint progress,    entire task map
                          call tools/subagents    

  Database                Canonical projects,     Model-generated
                          tasks, todos, requests, unvalidated changes
                          executions, events      

  Checkpointer            Durable graph execution Acting as the only
                          state per graph thread  project/task database

  External tools          Browser, code, job      Untracked side effects
                          sites, APIs             
  -----------------------------------------------------------------------

## 3. Identifiers: do not overload `thread_id`

Use different identifiers for different concepts.

-   `project_id`: durable project identifier.
-   `task_id`: durable objective inside a project.
-   `todo_id`: one bounded unit of work.
-   `request_id`: one user message; also use it as an idempotency key
    when appropriate.
-   `execution_id`: one attempt to process a request or work on a task.
-   `graph_thread_id`: LangGraph checkpoint namespace for one durable
    workflow.
-   `agent_run_id`: optional identifier for an individual subagent run.

The optional incoming `thread_id` should be treated as a *project lookup
key only if that is the API contract*. Prefer an explicit `project_id`
in your own API. If no project is specified, resolve a default/recent
project or ask JEv to recommend among authorized candidates. If no
project fits, create one in application code.

Do not silently create a new project when a supplied ID is invalid or
mistyped. Return a clear error or ask whether the user intended to
create a project.

Suggested graph-thread strategy: - Project-level conversational graph,
if needed: `project:{project_id}`. - Task worker graph:
`task:{task_id}`. - One-off classification graph:
`request:{request_id}`.

A graph thread is not a lock. Two runs using the same checkpoint thread
can still race unless your application coordinates them.

## 4. Canonical data model

Start with these logical tables. SQLite is fine for a single-process
local prototype; concurrency semantics must be revisited before using
multiple server processes.

### `projects`

-   `project_id` primary key
-   `owner_id` / tenant identifier
-   `external_thread_id` optional unique lookup key
-   `name`, `project_map`
-   `version`
-   `status`
-   timestamps

### `tasks`

-   `task_id` primary key
-   `project_id` foreign key
-   `title`, `description`, `task_summary`
-   `status`:
    `pending | ready | in_progress | blocked | completed | cancelled`
-   `priority`
-   `version`
-   `active_execution_id` nullable
-   `lease_owner`, `lease_expires_at` nullable
-   timestamps

### `todos`

-   `todo_id` primary key
-   `task_id` foreign key
-   `description`, `requirements`, `acceptance_criteria`
-   `status`:
    `pending | ready | in_progress | blocked | completed | skipped | failed`
-   `position`, `depends_on`
-   `version`
-   `active_execution_id` nullable
-   `result_summary`, `artifact_refs`
-   timestamps

For a simple prototype, `depends_on` can be JSON. If dependency queries
become important, normalize them into a `todo_dependencies` table.

### `requests`

-   `request_id` primary key
-   `owner_id`, `project_id` nullable until resolved
-   `user_query`
-   `status`:
    `received | classifying | queued | processing | completed | rejected | failed`
-   `classification_json`
-   `created_at`, `updated_at`

### `executions`

-   `execution_id` primary key
-   `request_id`, `project_id`, `task_id` nullable as appropriate
-   `graph_thread_id`
-   `status`:
    `queued | claimed | running | waiting | completed | failed | cancelled`
-   `attempt`, `started_at`, `heartbeat_at`, `finished_at`
-   `error_code`, `error_summary`

### `task_events`

Append-only history of state transitions and meaningful decisions: -
`event_id` - `project_id`, `task_id`, `todo_id`, `request_id`,
`execution_id` - `event_type` - `old_value`, `new_value` or structured
payload - `reason`, `created_at`

### `query_task_impacts`

Track the relationship between user requests and tasks: - `request_id`,
`task_id` - `impact_type`:
`created | clarified | reprioritized | added_requirement | interrupted | cancelled | informational | unrelated` -
`summary`, `created_at`

### Optional: `outbox`

Use if database commits must reliably trigger background work. Store an
event such as `todo_ready` in the same transaction as the task update; a
dispatcher later sends it to the scheduler. This avoids "database
committed, but worker never got scheduled" failures.

## 5. High-level architecture

``` mermaid
flowchart TD
    U[User query + optional project key] --> API[API / Agent class]
    API --> IDEM[Validate identity and idempotency]
    IDEM --> RESOLVE[Resolve project]
    RESOLVE --> SNAP[Load committed snapshot + versions]
    SNAP --> ADMIT[Admission and conflict policy]
    ADMIT -->|Busy relevant task| WAIT[Queue, reject, or record steering request]
    ADMIT -->|Can classify| JEV[JEv: classify and propose action]
    JEV --> VALIDATE[Validate IDs, permissions, versions, and policy]
    VALIDATE -->|Stale snapshot| REFRESH[Reload relevant state]
    REFRESH --> JEV
    VALIDATE -->|Create task| CREATE[Transaction: create task/todos/events]
    VALIDATE -->|Existing task| UPDATE[Transaction: record impact or pending change]
    CREATE --> SCHED[Scheduler]
    UPDATE --> SCHED
    SCHED --> CLAIM[Atomic claim of eligible todo]
    CLAIM --> WORKER[LangGraph task worker]
    WORKER --> SUB[Choose subagent and build bounded input]
    SUB --> EXEC[Execute one bounded todo]
    EXEC --> RESULT[Validate result and side effects]
    RESULT --> COMMIT[Transaction: persist result + status + event]
    COMMIT --> SUMMARY[Refresh summary proposal from current committed state]
    SUMMARY --> VERSION[Version-checked summary commit]
    VERSION --> SCHED
```

## 6. Request intake and project resolution

### Flow

1.  Validate user identity and request payload.
2.  Generate or validate `request_id`; return the existing result if
    this is a duplicate request already processed.
3.  Resolve the project:
    -   If an explicit project key exists, look it up.
    -   If found and authorized, use it.
    -   If not found, return a not-found response unless the user
        explicitly requested project creation.
    -   If no key exists, use a default/recent project rule or ask JEv
        to rank a small set of authorized candidate projects.
    -   If no candidate fits, create a project in application code.
4.  Load a snapshot of the project and relevant tasks, including
    versions and active execution status.
5.  Apply admission rules before asking JEv to route to a task.
6.  Call JEv with compact context and a strict structured output schema.
7.  Validate JEv's proposal and commit the change in a short
    transaction.

### Important improvement

Avoid asking JEv to select among every task and include all task
histories. Give it a compact task index and only the most relevant
summaries. Retrieve detailed context after a candidate task is selected.
This reduces latency, token use, and confused routing.

## 7. JEv decision contract

JEv should return a proposal similar to:

``` json
{
  "action": "continue_task",
  "task_id": "task_123",
  "impact_type": "added_requirement",
  "requires_steering": true,
  "reason": "The user added a requirement to the ongoing job search.",
  "confidence": 0.91,
  "snapshot": {
    "project_version": 12,
    "task_version": 7
  }
}
```

Possible actions: - `continue_task` - `create_task` - `modify_task` -
`informational_answer` - `ask_user` - `queue_until_idle` -
`request_steering` - `new_project` only as a proposal, not as an ID
generator

Application code must: - Verify that IDs exist and belong to the
resolved project. - Check user access. - Check task status and snapshot
versions. - Enforce business rules. - Reject malformed/unsupported
actions. - Ask for clarification when confidence is low or the request
is ambiguous. - Never trust a model-generated status transition without
validation.

Do not rely only on a numerical confidence score unless it is calibrated
against real examples. Combine it with explicit ambiguity rules and
evaluation.

## 8. Concurrency: the critical part

### Don't lock the entire project during agent work

Example project: - Task A: search for jobs. - Task B: improve resume. -
Task C: apply to a selected job.

Task A running should not block independent work on Task B. However, if
Task C depends on Task A's results, the scheduler should wait until the
dependency is satisfied.

### Use separate kinds of coordination

1.  **Project-map update coordination:** short version-checked commits;
    avoid holding a lock during LLM calls.
2.  **Task ownership:** only one active writer for a task unless its
    data model explicitly supports concurrent writes.
3.  **Todo claim:** atomic claim so two workers cannot execute the same
    todo.
4.  **External resource coordination:** prevent duplicate job
    applications or duplicate submissions even if different tasks try to
    apply to the same listing.

### Optimistic concurrency control

Every state snapshot includes versions. An LLM may reason for several
seconds, so before applying its proposal:

-   Compare the current version to the version in the snapshot.
-   If equal, commit with a conditional update that increments the
    version.
-   If different, discard or re-evaluate the proposal against fresh
    state.

The database transaction---not a prior Python `if` statement---must
enforce the comparison.

### Atomic claim

A worker must claim a todo only if it is still eligible and unclaimed.
Implement using a conditional database update or transaction. If zero
rows were updated, another worker owns it or the state changed; do not
execute it.

### Leases and crashes

A worker may crash while marked `in_progress`. Store a lease owner and
expiry/heartbeat. After expiry, a recovery process checks whether it is
safe to retry. Do not assume the old worker stopped just because its
lease expired; use execution fencing/version tokens so a late worker
cannot commit over a newer owner.

### SQLite limitation

`asyncio.Lock` coordinates tasks in one Python process only. SQLite can
work for a local, single-process prototype, but it is not a distributed
lock service. Multiple processes require database-enforced claims and
careful transaction handling; for heavier concurrent workers, PostgreSQL
is usually a better fit.

## 9. Task worker graph: bounded and resumable

Prefer a worker invocation that performs one todo or another clearly
bounded unit of work, commits its result, and then lets the scheduler
choose the next step.

``` mermaid
flowchart TD
    START[Start or resume task worker] --> LOAD[Load current task and eligible todo]
    LOAD --> GUARD{Task and todo still eligible?}
    GUARD -->|No| EXIT[Exit without side effects]
    GUARD -->|Yes| STEER{Pending steering request?}
    STEER -->|Yes| PAUSE[Save progress and pause safely]
    STEER -->|No| ROUTE[JEv chooses subagent]
    ROUTE --> BUILD[Build focused subagent input]
    BUILD --> RUN[Execute one bounded todo]
    RUN --> CHECK[Validate output, evidence, and side effects]
    CHECK --> COMMIT[Atomically commit result and event]
    COMMIT --> SUMMARY[Propose task/project summary update]
    SUMMARY --> SAFE[Version-check and commit summary]
    SAFE --> END[Return control to scheduler]
```

Avoid having a graph run an endless loop that executes many todos
without publishing durable progress. Bounded steps improve recovery,
observability, interruption, and scheduling.

## 10. Building subagent inputs

Use an LLM to construct a focused input after the task and todo are
selected. Include:

-   Task objective.
-   Acceptance criteria.
-   Current todo description and requirements.
-   Relevant previous todo results.
-   Relevant project facts only.
-   Tool permissions and constraints.
-   Expected structured result schema.
-   Rules for reporting uncertainty, blockers, and evidence.

Do not send every project message or the entire task history by default.
Retrieve history based on relevance and required dependencies.

Example contract:

``` json
{
  "task_id": "task_123",
  "todo_id": "todo_42",
  "objective": "Find backend roles matching the user's constraints",
  "current_step": "Search approved job sources and collect matching listings",
  "acceptance_criteria": [
    "Include title, company, location, URL, and source",
    "Deduplicate listings",
    "Do not submit applications"
  ],
  "required_output": {
    "status": "completed | blocked | failed",
    "result_summary": "string",
    "evidence": [],
    "artifacts": [],
    "blockers": []
  }
}
```

The subagent result must include `execution_id`, `task_id`, `todo_id`,
and ideally the claimed version/fencing token. Reject a result that no
longer owns the task/todo.

## 11. Processing results and updating the project map

Do not make one giant `process()` call that holds a lock while
performing several LLM calls.

Split it into stages:

1.  `load_current_snapshot(...)`
2.  `validate_worker_result(...)`
3.  `commit_todo_result(...)`
4.  `propose_task_summary(...)`
5.  `commit_task_summary_if_version_matches(...)`
6.  `propose_project_map_refresh(...)`
7.  `commit_project_map_if_version_matches(...)`
8.  `schedule_next_eligible_todo(...)`

The project map is a **derived summary**, not canonical task state. It
can be regenerated from committed project/task records. If two tasks
finish at once, their updates should not replace each other's changes
based on old snapshots.

For better reliability, update the todo and append the task event in the
same transaction. Summary generation can happen after that; a stale
summary is recoverable because it can be regenerated.

## 12. Steering and interruption policy

Treat a new query affecting a running task as a *steering request* until
the worker reaches a safe point.

1.  Persist the steering request.
2.  Worker checks for steering requests between bounded operations.
3.  If safe, save current progress and pause.
4.  Reclassify the new query against the latest committed task state.
5.  Apply the requirement change transactionally.
6.  Resume or start a new execution according to policy.

Distinguish: - **Informational query:** answer from a safe snapshot; do
not mutate task state. - **Additive requirement:** queue for a
checkpoint or apply if it doesn't invalidate current work. - **Priority
change:** update scheduler priority. - **Cancellation:** cooperative
stop; handle external side effects carefully. - **Contradictory
instruction:** ask the user which requirement takes precedence. -
**Irreversible action underway:** do not assume it can be safely
interrupted.

A LangGraph interrupt/checkpoint can help pause and resume a graph, but
your application still needs to persist the steering request, control
who owns the task, and decide when resumption is safe.

## 13. Other important failure modes and mitigations

  ---------------------------------------------------------------------------------
  Problem                 Why it happens          Mitigation
  ----------------------- ----------------------- ---------------------------------
  Duplicate API retry     Client times out and    Idempotency key on `request_id`
  creates duplicate tasks resends                 

  Two workers claim the   Check-then-act race     Atomic DB claim
  same todo                                       

  Worker crashes after    Side effect and DB      Idempotent external operation
  external action but     update are not atomic   where possible; record intent
  before commit                                   before action; reconcile
                                                  uncertain outcomes

  Old worker returns      Lease expires but old   Fencing token/version check at
  after a replacement     process continues       commit
  starts                                          

  Project map overwrites  Whole summary           Version-checked summary commit or
  concurrent updates      regenerated from stale  regenerate from current records
                          snapshot                

  JEv picks an unrelated  Context too broad or    Candidate retrieval, compact task
  task                    summaries too similar   index, structured reasons,
                                                  clarification fallback

  Todo dependency is      Pending does not mean   Separate `pending` from `ready`;
  ignored                 runnable                scheduler checks dependencies

  Task marked complete    Agent says completed    Acceptance criteria and
  too early               without meeting goal    deterministic completion
                                                  validation

  Retry repeats           Failure happened after  Retry policy by action type;
  expensive/unsafe action action                  idempotency and reconciliation

  Subagent invents        Output trusted without  Validate URLs/artifacts, tool
  evidence or success     verification            traces, and result schema

  One task blocks all     Project-wide lock       Task-level ownership and short
  tasks                                           project-map commits

  Deadlocks or lock       Manual lock lifecycle   Database transactions, leases,
  leakage                                         timeouts, and cleanup

  Stuck `in_progress`     No recovery path        Heartbeat/lease expiry and
  task after restart                              recovery worker

  Too many agents         Unbounded parallelism   Global/per-provider/per-project
  overwhelm APIs                                  concurrency limits

  User asks to delete or  Authorization missing   Enforce access control in
  change project data     from model flow         application code

  Context grows without   Full history sent on    Summaries, selective retrieval,
  limit                   every run               bounded context, artifacts stored
                                                  externally

  Model output schema     Free-form responses     Typed structured output,
  drifts                                          validation, bounded retries

  A completed todo        No invalidation policy  Track requirement versions and
  becomes invalid after                           mark affected outputs stale
  new instructions                                

  New query races with    Admission check occurs  Recheck status/version at
  result commit           too early               transaction commit

  Scheduler never wakes   State change and queue  Transactional outbox or periodic
  after a task is queued  publish split           database polling

  Multiple tasks apply to Different task IDs but  Unique application key and
  the same job            same external target    external-action guard

  Project is created      Concurrent first        Unique project key and
  twice                   requests                create-or-get transaction

  Cross-project routing   Model returns task from Validate task-project
                          another project         relationship and authorization
  ---------------------------------------------------------------------------------

## 14. State machines

### Task state

-   `pending`: exists but not yet eligible or scheduled.
-   `ready`: dependencies and prerequisites satisfied.
-   `in_progress`: owned by an active execution.
-   `blocked`: needs input or prerequisite.
-   `completed`: acceptance criteria met.
-   `cancelled`: deliberately stopped.
-   Optionally `failed`: terminal failure after retry policy is
    exhausted.

Do not use one status to represent both the task's business state and
the worker execution's runtime state. Keep execution status separately.

### Todo state

`pending -> ready -> in_progress -> completed`

Possible side paths: `blocked`, `failed`, `skipped`. A retry should
generally create a new execution attempt while preserving the todo's
history.

### Request state

`received -> classifying -> queued/processing -> completed`

Possible terminal or side states: `rejected`, `failed`, `cancelled`,
`waiting_for_user`.

## 15. Scheduling and dependencies

The scheduler should choose work based on: - Todo readiness and
dependency completion. - Task priority and deadlines. - Task-level
ownership. - User steering requests. - Global and per-tool concurrency
limits. - Retry/backoff policy. - Whether an external resource is
already being modified.

Do not ask JEv to decide every low-level scheduling fact. Use
deterministic code for eligibility and constraints; use JEv for semantic
ambiguity, task matching, and choosing an agent when multiple agents
could reasonably do the work.

## 16. Observability and tests

Every log/trace should include `request_id`, `project_id`, `task_id`,
`todo_id`, `execution_id`, and `graph_thread_id` where available.

Record: - JEv input snapshot version and structured decision. - Why a
request was queued or rejected. - Task claims and lease renewals. -
Agent/tool start/end and result references. - State transitions and
version conflicts. - Retry count, token/cost/latency if available. -
Interrupt/steering lifecycle.

Test at least: 1. Two simultaneous requests for the same task. 2. Two
simultaneous requests for different tasks in one project. 3. Duplicate
request ID. 4. JEv returns a stale task version. 5. Two workers race to
claim one todo. 6. Worker crashes before and after an external side
effect. 7. Old worker returns after lease expiry. 8. New instruction
arrives during a todo. 9. Two tasks update project summaries at the same
time. 10. Dependency blocks a todo. 11. User requests an ambiguous or
contradictory change. 12. Application restarts with work in progress.

## 17. Implementation sequence

1.  Define IDs and data models.
2.  Implement project resolution, access control, and request
    idempotency.
3.  Implement versioned task/todo state and event history.
4.  Implement atomic claims, leases, and recovery.
5.  Build JEv classification with strict structured output.
6.  Build a task worker graph that performs one bounded todo.
7.  Validate and transactionally persist results.
8.  Add project-map summary refresh and stale-version handling.
9.  Add steering requests and cooperative interrupts.
10. Add scheduling limits, observability, and concurrency tests.

## 18. Recommended first version

For a local prototype: - FastAPI or a simple Python service. - SQLite
for canonical project/task records. - LangGraph checkpointer for graph
execution state. - JEv for semantic decisions. - One active writer per
task. - Independent tasks allowed to run concurrently. - Bounded worker
execution, ideally one todo per invocation. - No project-wide lock
around LLM calls. - Version checks and atomic database writes from day
one.

If you later need multiple processes or concurrent worker machines,
migrate coordination and canonical data to PostgreSQL and use
database-backed claims/leases. Do not rely on `asyncio.Lock` as a
distributed lock.

## 19. Final architecture principle

**JEv proposes. The scheduler decides what is eligible. LangGraph
executes bounded work. Application code validates. The database records
the truth.**

That division prevents stale routing, duplicate work, lost updates,
untracked side effects, and project-map overwrites while preserving the
flexibility of LangGraph.
