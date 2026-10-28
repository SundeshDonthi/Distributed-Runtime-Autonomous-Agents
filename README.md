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
- **Phase 4 -- Reliability: in progress.** See below.
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
  scheduler/   Dependency-ready-task computation, the async run loop, and the
               Redis-backed ready-task queue
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
tests/
benchmarks/    Standalone scripts proving systems-level claims (Section 16)
```
