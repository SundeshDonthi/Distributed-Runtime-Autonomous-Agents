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
    currently needs a manual drop/recreate (`python -m
    scripts.reset_dev_db`), fine for this dev database, not for anything
    with data worth keeping.
- **Phase 5 -- Dynamic replanning: done.**
  - **`MissingPrerequisiteError`** (`runtime/tools/registry.py`): a tool
    raises this when it discovers mid-call that it needs information it
    wasn't given -- Section 5.10's own example made concrete. `Worker` tags
    it `error_category="needs_replan"`, a third category alongside
    transient/permanent that routes to the replanner instead of either
    retry path.
  - **The replanner** (`runtime/recovery/replanner.py`): calls
    `Planner.generate_subplan` (a second method alongside `generate_plan`,
    same LLM-agnostic seam) to produce new task(s), validates the *resulting*
    graph before persisting anything (rejecting a colliding id, a subplan
    task with its own dependencies, or a cycle), then splices them in as a
    pure addition -- a new dependency edge onto the blocked task, nothing
    already completed is touched or re-validated. Bounded by each task's own
    `max_replans` so a prerequisite that never resolves can't mutate the
    graph forever.
  - **Versioned, audited mutation** (Section 15G.1): `Workflow.plan_version`
    bumps on every successful replan; a `replan_events` table records which
    task was blocked, why, and what got inserted, served at `GET
    /workflows/{id}/replan-events`.
  - Verified against the doc's own literal example: an A -> B -> C graph
    where B discovers it needs D becomes A -> B -> D -> C, with A's
    completion timestamp proving it was never re-touched
    (`test_replan_unblocks_a_task_and_reuses_completed_work`).
  - One more fencing-check interaction bug caught here, same root cause as
    Phase 4's: clearing `lease_owner` as part of returning a replanned task
    to PENDING made the fencing check compare the just-cleared value against
    the DB's still-active lease, silently rejecting the whole mutation.
    Fixed by leaving lease fields alone on that path (harmless -- the next
    `acquire_lease` overwrites them regardless).
- **Phase 6 -- Routing dataset, finished: done.**
  - **Feature snapshots** (`runtime/routing/heuristic.py`'s
    `build_feature_vector`, `FEATURE_VERSION`): the exact inputs
    `score_candidate` used for a candidate -- static priors and whatever
    observed stats were current *at that moment* -- are persisted alongside
    each `routing_candidates` row, not just the resulting score. Verified
    that recomputing the score purely from a persisted snapshot reproduces
    the logged score exactly
    (`test_persisted_features_reproduce_the_persisted_score`), so a training
    run can trust the snapshot instead of re-deriving features from
    observed-stats history that's since moved on (Section 15D.2: "avoid
    using post-execution fields as pre-execution features").
  - **Held-out splits by workflow** (`runtime/training/splits.py`):
    deterministic hash-based `assign_split(workflow_id, seed, ...)` -- same
    inputs always produce the same split, so a dataset export is
    reproducible with no persisted assignment table to keep in sync. Split
    by *workflow*, not by individual decision (Section 15D.2), so a
    multi-task workflow can't leak across train/val/test.
  - **The actual training export** (`runtime/training/datasets.py`'s
    `export_training_rows`, `benchmarks/export_training_dataset.py`): every
    labeled decision tagged with its split, written out as JSON Lines --
    what a Phase 7 training script reads, as opposed to the Phase 3 cold-
    start generator, which only ever reported live summary stats.
  - **A real cleanup bug caught here**: the FastAPI app's shutdown stopped
    the MCP subprocess connection but never unregistered the tools it had
    added, so `mcp.search_web` stayed in the global tool registry --
    backed by a now-dead connection -- after every app shutdown, silently
    contaminating whichever test ran next in the same process (and, in a
    real deployment, poisoning a restart). Fixed by unregistering each
    MCP-discovered tool on shutdown, not just disconnecting.
- **Phase 7 -- Train and deploy neural router: done.**
  - **The scorer** (`runtime/routing/neural.py`): logistic regression,
    implemented from scratch (no sklearn/pytorch, per the project's own
    "systems infrastructure, not a wrapper" framing) over the same five
    features `build_feature_vector` already logged in Phase 6 --
    `NeuralScorer` is a frozen dataclass of learned `weights`/`bias` plus
    `feature_means`/`feature_stds` for standardization, tagged with
    `model_version`/`feature_version`/`training_dataset_version` so a stored
    model can always be checked against the features it was actually trained
    on. This is the honest version of Section 15C.1's "text encoder + MLP":
    there's no real text signal in this project to encode, so a from-scratch
    linear scorer over structured features is what that section degrades to
    here rather than a decorative network with nothing to learn from.
  - **Training and evaluation are separate code paths**
    (`runtime/training/train.py`, `runtime/training/evaluate.py`):
    `train_router_model` reads *only* the `"train"` split; evaluation
    (`evaluate_against_heuristic`, calibration + agreement rate + accuracy)
    and `compare_policy_success_rates` read only `"test"`. Calibration
    (`runtime/training/calibrate.py`) reports Expected Calibration Error and
    Brier score, gated on a minimum sample count, so "calibrated" is a
    computed claim, not an assumption.
  - **`benchmarks/train_and_evaluate_router.py`** runs the full pipeline
    end to end and reports real numbers, not illustrative ones: 150 examples,
    ECE 0.067 (calibrated), 88.9% accuracy@0.5, and -- the actually meaningful
    result -- the neural model's default pick had a 100% empirical success
    rate on held-out data versus the heuristic's pick at 86.5%, despite a
    measured agreement rate of 0.0 between the two policies' top choices.
    That last number looked like a bug at first; it wasn't -- it's the
    dataset's cold-start exploration data genuinely supporting a different
    top pick than the heuristic's static priors would choose, which is the
    whole reason to train a model instead of trusting the heuristic forever.
    One caveat worth keeping honest: `observed_reliability`'s learned weight
    came out negative, most likely multicollinearity with
    `static_reliability` on a dataset this small, not a real inverse
    relationship -- a larger/more diverse training set is the real fix, not
    a feature removed to make the sign look right.
  - **Shadow and active routing modes, with guarded fallback**
    (`runtime/routing/dispatch.py`'s `resolve_task_tool`, threaded through
    `runtime/scheduler/scheduler.py`): heuristic ranking is always computed
    as the permanent fallback (Section 15A.1). In `"shadow"` mode the neural
    scorer's predictions are logged (`neural_score` on each
    `routing_candidates` row) but never change what actually runs. In
    `"active"` mode the neural top pick is used only if a model is loaded,
    its `feature_version` matches, and its confidence clears
    `confidence_threshold` (default 0.6); otherwise dispatch falls back to
    the heuristic pick and records why (`fallback_reason` on
    `routing_decisions`). Epsilon-greedy exploration, when it fires, still
    overrides both policies, exactly as it did before a neural model existed.
  - **Model lifecycle via the API**: a `router_models` table
    (`runtime/state/orm.py`) tracks each trained model's artifact and
    rollout `status` (`shadow` -> `active` -> `retired`, Section 15D.3).
    `GET /router-models` lists them; `POST
    /admin/router/models/{model_version}/activate` promotes one to active
    and retires whatever was active before it, enforcing the single-active
    invariant at the API layer (`runtime/api/routes.py`). Activating a model
    doesn't by itself change any workflow's `routing_mode` -- that's still
    an explicit argument to `run_workflow`, matching how `routing_mode`
    already worked before this table existed.
  - **A real, long-latent bug caught here**: `tests/test_api_smoke.py` had
    never actually run against an isolated database, since the file was
    first written. It tried to point itself at a throwaway SQLite file via
    `os.environ.setdefault("DATABASE_URL", ...)`, but `tests/conftest.py`
    imports `runtime.state.db` at collection time -- which pytest does
    unconditionally, even for a single targeted test -- so `engine`/
    `SessionLocal` were already bound to the real local Postgres default
    before this file's own setup code ever ran. Every test in it, across
    every phase, had silently been reading and writing the real dev
    `agent_runtime` Postgres database. Caught because
    `test_router_models_lifecycle_through_the_api` only failed when run as
    part of the full suite, and confirmed by inspecting Postgres directly
    with `psql` and watching rows land there from a supposedly-isolated
    test. Fixed by rewriting the file around a `client` fixture that
    monkeypatches `runtime.state.db`'s `engine`/`SessionLocal` directly --
    the same pattern `conftest.py`'s own `db_session` fixture already used
    correctly -- which sidesteps the import-order race instead of trying to
    win it.
- **Phase 8 -- Real multi-process distribution: done.**
  - **The API, scheduler, and workers are genuinely separate OS processes**,
    not `asyncio` coroutines sharing one process's memory. A new
    `worker_mode` on `run_workflow`/`run_workflow_async`
    (`runtime/scheduler/scheduler.py`) adds `"distributed"` alongside
    Phases 1-7's `"embedded"` default: instead of calling the in-process
    `Worker` directly, a round's tasks are pushed onto a global
    `DispatchQueue` (`runtime/scheduler/queue.py`) for any number of
    independent `runtime.workers.worker_process` processes to pick up, and
    their reported outcomes come back over a per-workflow `ResultQueue`.
    Everything downstream of that -- retry classification, replanning,
    idempotency, outbox events, fencing -- is unchanged, because it only ever
    sees the same in-memory `Task` object updated with a result, whether that
    result came from a coroutine in this process or a tool call that ran on
    a different machine entirely.
  - **A worker process never touches Postgres** (`runtime/workers/
    worker_process.py`): it only speaks Redis, reusing the exact same
    `Worker.execute_async` Phases 1-7 already call in-process, so "where the
    tool actually ran" is the only thing that changed. All task-state
    ownership (leases, fencing, retries) stays exactly where Phase 4 put it
    -- in the scheduler -- never duplicated across however many worker
    processes happen to be running.
  - **The scheduler is now optionally its own process, too**
    (`runtime/scheduler/scheduler_process.py`): it consumes workflow ids off
    a global `PendingWorkflowQueue`, bounding how many workflows it runs
    concurrently with a semaphore (each on its own DB session), and runs each
    one with `worker_mode="distributed"`. `POST /workflows`
    (`runtime/api/routes.py`) picks between handing a new workflow to this
    queue or running it inline via FastAPI's own `BackgroundTasks`, based on
    a `SCHEDULING_MODE` env var -- `"embedded"` (the default, Phases 1-7's
    behavior, and what the entire existing test suite still exercises
    unchanged) or `"distributed"`. This mirrors the project's existing
    "optional at runtime" pattern for Redis and MCP (Section 15A.1): nothing
    about the single-process path regressed to add the multi-process one.
  - **Docker Compose** (`docker-compose.yml`, `Dockerfile`): one image, three
    roles -- `api`, `scheduler`, and `worker` services built from the same
    Dockerfile with different `command:`s, plus `postgres` and `redis`,
    coordinating only through `DATABASE_URL`/`REDIS_URL` pointing at each
    other's service names. `docker compose up --scale worker=3` runs three
    independent worker containers against one scheduler and one API. Honest
    caveat: this machine has no Docker installed, so the compose file itself
    was validated as well-formed YAML but not run end-to-end in containers;
    what *was* run end-to-end, as real separate OS processes talking only
    through Postgres/Redis, is the exact same `scheduler_process`/
    `worker_process` entrypoints the compose file's `command:`s invoke --
    the part Docker adds on top is process isolation and packaging, not new
    application logic.
  - **Trace and graph endpoints** (`runtime/api/routes.py`): `GET
    /workflows/{id}/graph` returns the workflow as nodes (tasks, with status
    and resolved tool/capability) and dependency edges, for rendering an
    actual DAG. `GET /workflows/{id}/trace` merges outbox events, routing
    decisions, and replan events into one time-ordered timeline
    (`repository.get_outbox_events_for_workflow` joins outbox rows through
    `tasks` since they're keyed by task id, not workflow id). Both exist
    because Phase 8 itself removed the thing observability used to lean on
    for free -- "tail the one process's stdout" stopped being a coherent
    debugging strategy the moment execution could legitimately span an API
    process, a scheduler process, and an unbounded number of worker
    processes on different machines.
  - Verified for real, not just unit-tested in isolation:
    `tests/test_distributed_worker.py` pushes a request by hand onto the
    dispatch queue and confirms a real `worker_process` subprocess executes
    it and reports back correctly (including the permanent-failure path for
    an unknown tool, proving Phase 4's retry classification survives an
    out-of-process worker unchanged), and
    `tests/test_scheduler_process.py` runs a complete workflow through a
    real `scheduler_process` subprocess *and* a real `worker_process`
    subprocess at once, coordinating purely through a live Postgres and
    Redis -- this project's first test where no Python call stack connects
    "the workflow was created" to "the workflow completed" at all.
- **Phase 9 -- C++: done.**
  - **What's genuinely worth C++ here, and what isn't.** The neural router's
    *inference* call is a single 5-feature dot product + sigmoid -- Phase
    7's own benchmark already measured that at ~1us either way, i.e.
    nothing for compiled code to meaningfully win back once a Python<->
    native call's own crossing cost is counted. *Training* is different: it's
    O(epochs * n_examples * n_features) of repeated arithmetic, re-run every
    time a model is retrained, and the one place in this project where an
    interpreter's per-operation overhead genuinely compounds. So that's the
    one kernel this phase ports to C++ -- not "add C++ somewhere" for its own
    sake, the thing the project's earlier C++ discussion explicitly ruled
    out.
  - **`native/router_native.cpp`**: a pybind11 extension (`runtime.routing.
    _native`) that is a line-for-line port of `runtime/routing/neural.py`'s
    own training loop and batched-inference loop -- same standardization,
    same batch gradient descent on binary cross-entropy, same clipped
    sigmoid, same L2-regularized weight update. Not a different or "better"
    model; the same arithmetic, compiled instead of interpreted. Built with
    `python setup.py build_ext --inplace` (`setup.py`, using pybind11's
    `Pybind11Extension`).
  - **Optional at runtime, like every other external dependency in this
    project** (Section 15A.1 -- Redis, MCP, Phase 8's distributed workers):
    `runtime/routing/neural.py` tries to import `_native` once at module
    load and falls back to its pure-Python reference implementation if the
    extension was never built. `train_logistic_regression` and the new
    `predict_proba_batch` both take a `backend: Literal["python", "cpp"]`
    (threaded all the way from `run_workflow`/`resolve_task_tool` as
    `neural_backend`, the same pattern `routing_mode`/`worker_mode` already
    established) -- `"cpp"` with the extension unbuilt raises a clear
    `RuntimeError` naming the build command, never a silent fallback to a
    backend the caller didn't ask for and never a confusing crash three
    frames down.
  - **`predict_proba_batch`** is a new function, not just a backend switch:
    dispatch (`_neural_scores_for`) used to call `predict_proba` once per
    candidate; it now scores a task's whole candidate set in one call,
    amortizing a single Python<->native crossing over the batch instead of
    paying it per candidate. The pure-Python implementation of this is just
    a list comprehension over the existing `predict_proba` -- no behavior
    change there, only a cleaner integration point for the C++ path.
  - **Measured, not assumed, speedup** (`benchmarks/benchmark_cpp_router.py`,
    Section 16's own standard): training 4,000 examples for 300 epochs took
    0.951s in Python versus 0.018s in C++ -- **52.8x**. Batched inference
    over 20,000 candidates was 0.022s versus 0.007s -- **3.3x**, a real but
    far more modest win, honestly consistent with Phase 7's own finding that
    a single candidate's inference is too cheap for compiled code to matter;
    the 20k-candidate batch size in that benchmark exists to make the
    amortization effect visible at all, not because any real routing
    decision batches anywhere near that many candidates (a task's capability
    typically has 2). Both backends' outputs agreed to within 1e-15 (float
    noise from summation order, not a correctness gap) on every example.
  - **Correctness, not just speed** (`tests/test_neural_cpp.py`,
    `tests/test_neural_routing_modes.py`): the two backends' trained weights,
    biases, and inference outputs are checked to agree within floating-point
    tolerance on synthetic data, a full `resolve_task_tool` call in `"active"`
    mode is checked to pick the exact same tool under `neural_backend="cpp"`
    that the existing Python-backend test already proves it picks, and the
    "extension not built" fallback path is checked (via monkeypatching the
    module's own native-import slot, not relying on the environment not
    having built it) to raise a clear error rather than silently doing
    something else. All of these skip cleanly -- not fail -- on a machine
    that hasn't run the build step, verified by moving the compiled `.so`
    aside and re-running the full suite (153 passed, 7 skipped) before
    rebuilding it and confirming all 160 pass.
  - This was genuinely built and run on this machine: Apple clang 14,
    CMake 4.4, and pybind11 3.1 (installed into the project's own venv) --
    not stubbed out or left as a design-only placeholder.

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

## Run distributed (Docker Compose)

Phase 8: the API, scheduler, and workers as genuinely separate OS processes,
coordinating only through Postgres and Redis.

```bash
docker compose up --build --scale worker=3
```

```bash
curl -X POST localhost:8000/workflows \
  -H 'Content-Type: application/json' \
  -d '{"objective": "Analyze NVIDIA, AMD, and Intel."}'

curl localhost:8000/workflows/<workflow_id>/graph
curl localhost:8000/workflows/<workflow_id>/trace
```

`api` runs with `SCHEDULING_MODE=distributed`, so creating a workflow hands
it to the `scheduler` service over Redis instead of running it inline the
way the single-process `uvicorn` command above (and the entire test suite)
does by default.

## Build the C++ extension (optional)

Phase 9's compiled router kernel (`runtime/routing/_native`). Entirely
optional -- `runtime/routing/neural.py` falls back to its pure-Python
reference implementation if this was never built, so skipping this section
doesn't break anything else in the project.

```bash
pip install -r requirements.txt   # pulls in pybind11 + setuptools
python setup.py build_ext --inplace
python -m benchmarks.benchmark_cpp_router   # prints a real, measured speedup
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
python -m benchmarks.export_training_dataset   # writes training_dataset.jsonl
```

## Layout

```
runtime/
  api/         FastAPI app/routes (/workflows, /tools, /workflows/{id}/
               routing-decisions, /workflows/{id}/replan-events,
               /workflows/{id}/graph, /workflows/{id}/trace, /router-models)
  models/      Pydantic domain models (Workflow, Task)
  planner/     Objective -> task graph (generate_plan) and blocked-task ->
               subplan (generate_subplan), plus DAG validation
  scheduler/   dag.py (readiness), scheduler.py (the async run loop;
               `worker_mode` "embedded" via Redis/direct dispatch or
               "distributed" via queue.py's DispatchQueue/ResultQueue),
               scheduler_process.py (Phase 8's standalone scheduler OS
               process), leases.py (claim/fencing/reclaim),
               idempotency.py (cached-result short-circuit)
  recovery/    retry.py (transient/permanent classification + backoff),
               replanner.py (Section 5.10's graph mutation)
  workers/     worker.py (Task execution -- async, offloaded to a thread per
               tool call -- only ever runs an already-resolved tool, never
               routes itself; validates args against the tool's JSON schema),
               worker_process.py (Phase 8's standalone worker OS process,
               never touches Postgres)
  tools/       registry.py (tool metadata incl. input_schema), mcp_client.py
               (discovers tools from a real MCP server over stdio)
  routing/     heuristic.py (scoring + feature snapshots), history.py
               (observed stats from past tool calls), dispatch.py (the
               scheduler's routing step: resolve a capability to a tool,
               log the decision + feature snapshots, log the outcome),
               neural.py (logistic regression scorer; `backend="python"` or
               "cpp"), _native* (Phase 9's compiled extension, gitignored --
               see native/ and "Build the C++ extension" above)
  training/    datasets.py (generates a labeled dataset via controlled,
               exploratory benchmark runs, Section 15C.3; exports it with
               splits), splits.py (deterministic train/val/test assignment
               by workflow, Section 15D.2), train.py/evaluate.py/
               calibrate.py (Phase 7's training + evaluation pipeline)
  state/       SQLAlchemy ORM + repository (Postgres) and the Redis client
mcp_servers/   A real (demo) MCP server, launched as a subprocess by
               runtime/tools/mcp_client.py -- proves genuine dynamic tool
               discovery rather than a hardcoded registry
scripts/       chaos_run_workflow.py (the subprocess entry point the chaos
               recovery test kills mid-execution), reset_dev_db.py (drop +
               recreate the schema -- see the Phase 4 scope boundary above)
tests/
benchmarks/    Standalone scripts proving systems-level claims (Section 16)
native/        router_native.cpp -- Phase 9's one C++ source file, a
               line-for-line port of neural.py's training/inference math
setup.py       Builds native/router_native.cpp into runtime/routing/_native
               via pybind11 (`python setup.py build_ext --inplace`)
Dockerfile, docker-compose.yml
               Phase 8: one image (api/scheduler/worker share it, different
               `command:`s) plus postgres/redis -- see "Run distributed"
```

## Chaos test

```bash
pytest tests/test_chaos_recovery.py -v
```

Needs a live Postgres (skips otherwise -- separate OS processes can't share
an in-memory SQLite DB). Spawns a real subprocess, SIGKILLs it mid-workflow,
and verifies a fresh run recovers correctly.
