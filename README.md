# Distributed Runtime for Long-Horizon Autonomous Agents

A distributed execution runtime for autonomous AI agents: planning, DAG-based
scheduling, parallel execution, persistent state, failure recovery, and
observability -- built as systems infrastructure underneath an agent, not as
another LLM-calls-tools wrapper. Full design doc: see the project description
PDF this repo was scaffolded from.

## Status: Phase 1 -- Basic Runtime

Objective -> rule-based Planner -> DAG validator -> PostgreSQL -> sequential
Scheduler -> single Worker -> two deterministic test tools (`echo`,
`uppercase`) -> durable result.

No LLM, no parallel execution, no MCP, and no Redis-backed queue yet -- those
are Phase 2+ (see `runtime/planner/planner.py` docstring and the roadmap in
the project description, Section 15J / Section 17).

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

brew services start postgresql@16
createdb agent_runtime

cp .env.example .env   # adjust DATABASE_URL/REDIS_URL if needed
```

> **Redis**: not required until Phase 2 (it's only used for the task queue/worker
> heartbeats, Section 5.7). On this machine (macOS 12 / Monterey), Homebrew has
> no prebuilt bottle for Redis and building it from source fails on the
> system's Clang version. When Phase 2 starts, install it via
> `conda install -c conda-forge redis-server` instead, or upgrade macOS.

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

## Layout

```
runtime/
  api/         FastAPI app and routes
  models/      Pydantic domain models (Workflow, Task)
  planner/     Objective -> task graph, plus DAG validation
  scheduler/   Dependency-ready-task computation and the run loop
  workers/     Task execution
  tools/       Tool registry (Phase 1: echo, uppercase)
  state/       SQLAlchemy ORM + repository (Postgres-backed)
tests/
```
