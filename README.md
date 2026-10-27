# Distributed Runtime for Long-Horizon Autonomous Agents

A distributed execution runtime for autonomous AI agents: planning, DAG-based
scheduling, parallel execution, persistent state, failure recovery, and
observability -- built as systems infrastructure underneath an agent, not as
another LLM-calls-tools wrapper. Full design doc: see the project description
PDF this repo was scaffolded from.

## Status: Phase 2 (in progress) -- Parallel Execution

Objective -> rule-based Planner -> DAG validator -> PostgreSQL -> async
Scheduler (each round's READY tasks pushed onto a Redis queue, pulled by up to
`max_concurrency` worker loops) -> Worker pool -> tools (`echo`, `uppercase`,
`delay`) -> durable result.

Phase 1 proved the sequential path. Phase 2 adds:
- concurrent dispatch of independent branches with a configurable concurrency
  limit (`runtime/scheduler/scheduler.py`, `benchmarks/parallelism.py`, which
  reproduces the project description's Section 6 worked example: 5s/6s/4s
  sequential -> ~6s parallel);
- a Redis-backed ready-task queue (`runtime/scheduler/queue.py`, Section 5.7)
  decoupling "a task became ready" from "a worker picked it up" -- the same
  pattern Phase 6's distributed workers will need, exercised in-process here.
  Falls back to direct dispatch if Redis is unreachable (`is_reachable` in
  `runtime/state/redis_client.py`), per Section 15A.1's "optional at runtime"
  philosophy.

Still no LLM, no MCP, and no real distribution across worker processes yet --
those are Phase 3/6 (see `runtime/planner/planner.py` docstring and the
roadmap in the project description, Section 15J / Section 17).

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
```

## Layout

```
runtime/
  api/         FastAPI app and routes
  models/      Pydantic domain models (Workflow, Task)
  planner/     Objective -> task graph, plus DAG validation
  scheduler/   Dependency-ready-task computation, the async run loop, and the
               Redis-backed ready-task queue
  workers/     Task execution (async, offloaded to a thread per tool call)
  tools/       Tool registry (echo, uppercase, delay)
  state/       SQLAlchemy ORM + repository (Postgres) and the Redis client
tests/
benchmarks/    Standalone scripts proving systems-level claims (Section 16)
```
