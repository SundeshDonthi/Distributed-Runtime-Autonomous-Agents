# Distributed Runtime for Long-Horizon Autonomous Agents

A distributed execution runtime for autonomous AI agents: planning, DAG-based
scheduling, parallel execution, persistent state, failure recovery, and
observability -- built as systems infrastructure underneath an agent, not as
another LLM-calls-tools wrapper. Full design doc: see the project description
PDF this repo was scaffolded from.

## Status

Tracking the project description's Section 15J roadmap (Phases 1-9).

- **Phase 1 -- Basic runtime: done.** Objective -> rule-based Planner -> DAG
  validator -> PostgreSQL -> Scheduler -> Worker -> tools -> durable result.
- **Phase 2 -- Parallel execution: done.** Concurrent dispatch of independent
  branches with a configurable concurrency limit
  (`runtime/scheduler/scheduler.py`); a Redis-backed ready-task queue
  (`runtime/scheduler/queue.py`, Section 5.7) decoupling "a task became
  ready" from "a worker picked it up" -- the pattern Phase 8's distributed
  workers rely on, exercised in-process for now. Falls back to direct
  dispatch if Redis is unreachable (`is_reachable` in
  `runtime/state/redis_client.py`), per Section 15A.1's "optional at
  runtime" philosophy. `benchmarks/parallelism.py` reproduces Section 6's
  worked example (5s/6s/4s sequential -> ~6s parallel).
- **Phase 3 -- Tool registry + heuristic router: done.**
  - A **heuristic router** (`runtime/routing/heuristic.py`) scoring competing
    tools for a capability (`capability_match + reliability - latency -
    cost`) -- the permanent deterministic fallback a future neural scorer
    degrades to. A task addresses work either by exact tool name
    (`{"tool": "echo"}`, bypasses routing) or by capability
    (`{"capability": "lookup"}`, routed).
  - Routing happens in the **scheduler's dispatch step**
    (`runtime/routing/dispatch.py`), not inside the worker (Section 15A.1) --
    picking a tool from historical outcomes needs the DB session a stateless
    worker (Section 5.5) deliberately doesn't have.
  - The router considers **observed outcomes**, not just static metadata
    (`runtime/routing/history.py`): every tool call and every routing
    decision's full candidate set and eventual outcome is persisted
    (`tool_calls`/`routing_decisions`/`routing_candidates`/
    `routing_outcomes`, Section 15D/15G).
  - `runtime/training/datasets.py` generates an initial labeled dataset
    (Section 15C.3's "cold start") via controlled benchmark tasks with
    epsilon-greedy exploration, so outcomes get logged for losing candidates
    too (Section 15D.2's selection-bias concern).
  - **Real MCP tool discovery** (`runtime/tools/mcp_client.py`,
    `mcp_servers/demo_tools_server.py`): a genuine MCP server, run as a
    subprocess over stdio, is queried for its tools at API startup and its
    tools registered into the same tool registry as the in-process ones,
    with zero scheduler/routing changes -- Phase 3's own definition of done.
  - **Input schema validation** (`jsonschema`, wired into `Worker`) runs for
    every tool call, in-process or MCP-discovered, before it's made.
- **Phase 4 -- Reliability: done.**
  - **Real checkpointing, fixed from a genuine bug**: the scheduler used to
    run an entire workflow inside one commit, so a real process crash would
    have rolled back *everything*, including tasks that had already finished
    -- directly contradicting Section 5.8. Commits now happen after every
    meaningful step (lease acquisition, each round's results, cancellations,
    reclaims), so a crash only costs whatever was genuinely in flight.
  - **Leases + fencing tokens** (`runtime/scheduler/leases.py`, Section
    12/15F.4): a task is claimed with an owner, an expiry, and a
    monotonically incrementing fencing token before it's dispatched; a
    result can only be committed if that token still matches the current row
    (`repository.commit_task_if_fencing_valid`) -- a worker whose claim has
    since been superseded can't overwrite a newer attempt.
  - **Lease reclaim + heartbeats** (Section 13): a lease that expired before
    its owner reported back is detected and reassigned at the start of every
    round; a background heartbeat renews the lease of a task that's
    genuinely still running, so a slow-but-healthy task isn't mistaken for
    an orphaned one.
  - **Retry classification + exponential backoff** (`runtime/scheduler/
    retry.py`, Section 5.9): a failure is tagged transient or permanent by
    `Worker` (schema/unknown-tool errors are permanent; a tool's own call
    failing is transient) and only transient ones are retried, with the
    doc's own 1s/2s/4s backoff, bounded by `max_retries`.
  - **Idempotency ledger** (`runtime/scheduler/idempotency.py`, Section
    15F.3): a task whose logical operation already completed on a prior
    attempt is served its cached result instead of being re-dispatched.
    Two demo tools show the honest limitation this can't paper over:
    `idempotent_charge` (simulates an external API with its own idempotency
    support -- a duplicate call is genuinely suppressed) versus
    `naive_charge` (no such support -- a duplicate call genuinely duplicates
    the side effect, Section 15K's "exactly-once is limited by external tool
    semantics" made concrete rather than just asserted).
  - **Outbox events** (Section 15F.4/15G.2): every task transition is
    recorded in the same commit as the state change itself.
  - **A real chaos test** (`tests/test_chaos_recovery.py`): spawns an actual
    subprocess, lets one task complete while a second is mid-flight,
    SIGKILLs it, and verifies a fresh run recovers correctly -- the completed
    task is untouched, the orphaned one gets reclaimed and finishes.
  - **Known scope boundary**: commits are per-*round*, not per-task within a
    round (concurrent tasks in one round only finish together via one
    `asyncio.gather`, and only one thing may safely touch the DB session
    while dispatch is in flight -- see `scheduler.py`'s docstring). A slow
    task sharing a round with fast ones delays their durability until the
    round finishes. There's also no real schema-migration tooling yet
    (`Base.metadata.create_all` only adds missing tables, not missing
    columns on existing ones) -- a schema change on a real deployment
    currently needs a manual drop/recreate, fine for this dev database, not
    for anything with data worth keeping.
- Phases 5, 6 (feature snapshots + held-out splits), 7 (train the neural
  router), 8 (real multi-process distribution), 9 (C++): not started yet.

No LLM in the planner yet (`runtime/planner/planner.py` is still rule-based
-- see its docstring); that's intentionally decoupled from the rest of this
work (Section 2's "swap models without changing the execution architecture")
and isn't blocking anything above.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

brew services start postgresql@16
createdb agent_runtime

cp .env.example .env   # adjust DATABASE_URL/REDIS_URL if needed
```

> **Redis**: this machine is on macOS 12 (Monterey), where Homebrew has no
> prebuilt bottle for Redis and building it from source fails on the system's
> Clang version. Installed instead via
> `conda install -c conda-forge redis-server` (a prebuilt binary, works fine).
> Start it with `redis-server --save "" --appendonly no &` (no `brew services`
> entry since it's a conda package, not a Homebrew one).

## Run

```bash
export $(cat .env | xargs)
uvicorn runtime.api.main:app --reload
```

```bash
curl -X POST localhost:8000/workflows \
  -H 'Content-Type: application/json' \
  -d '{"objective": "Analyze NVIDIA, AMD, and Intel."}'

curl localhost:8000/workflows/<workflow_id>
```

## Test

```bash
pytest
```

Tests run against an in-memory SQLite DB (`tests/conftest.py`) so they don't
require a live Postgres instance.

## Benchmark

```bash
python -m benchmarks.parallelism
python -m benchmarks.generate_routing_dataset
```

## Layout

```
runtime/
  api/         FastAPI app/routes (/workflows, /tools, /workflows/{id}/routing-decisions)
  models/      Pydantic domain models (Workflow, Task)
  planner/     Objective -> task graph, plus DAG validation
  scheduler/   dag.py (readiness), scheduler.py (the async run loop + Redis
               queue), leases.py (claim/fencing/reclaim), retry.py
               (transient/permanent classification + backoff),
               idempotency.py (cached-result short-circuit)
  workers/     Task execution (async, offloaded to a thread per tool call) --
               only ever runs an already-resolved tool, never routes itself;
               validates args against the tool's JSON schema before calling it
  tools/       registry.py (tool metadata incl. input_schema), mcp_client.py
               (discovers tools from a real MCP server over stdio)
  routing/     heuristic.py (scoring), history.py (observed stats from past
               tool calls), dispatch.py (the scheduler's routing step: resolve
               a capability to a tool, log the decision, log the outcome)
  training/    datasets.py -- generates a labeled routing dataset via
               controlled, exploratory benchmark runs (Section 15C.3)
  state/       SQLAlchemy ORM + repository (Postgres) and the Redis client
mcp_servers/   A real (demo) MCP server, launched as a subprocess by
               runtime/tools/mcp_client.py -- proves genuine dynamic tool
               discovery rather than a hardcoded registry
scripts/       chaos_run_workflow.py -- the subprocess entry point the chaos
               recovery test kills mid-execution
tests/
benchmarks/    Standalone scripts proving systems-level claims (Section 16)
```

## Chaos test

```bash
pytest tests/test_chaos_recovery.py -v
```

Needs a live Postgres (skips otherwise -- separate OS processes can't share
an in-memory SQLite DB). Spawns a real subprocess, SIGKILLs it mid-workflow,
and verifies a fresh run recovers correctly.
