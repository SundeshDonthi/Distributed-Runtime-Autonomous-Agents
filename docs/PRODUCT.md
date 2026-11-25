# Relay: runtime explorer

This is the first product release of the runtime: a public workbench for a
bounded repository-comparison workload. It is an engineering demonstration,
not an open-ended autonomous research assistant.

## Try it locally

From the repository root, with dependencies installed:

```sh
DATABASE_URL=sqlite:////tmp/relay.db PUBLIC_DEMO=1 ENABLE_DEMO_MCP=0 \
  .venv/bin/python -m uvicorn runtime.api.main:app --port 8000
```

Open http://localhost:8000. Choose **Discover missing evidence** and run it.
The report task discovers that a README is absent, inserts a prerequisite,
consumes its persisted output, and completes. The original three collection
tasks are not repeated. Select graph nodes to inspect outputs and timings.
The URL contains the run identifier, so refresh restores a persisted run.

The default offline example executes through the runtime with labelled,
illustrative repository metadata. It contains no invented popularity figures.
**Live GitHub API** contacts api.github.com and reports real failures instead
of silently substituting example data. Optionally set GITHUB_TOKEN on the
server to increase the API allowance; it is never returned to the browser.

## What executes

- Three stateless tools collect public repository metadata concurrently.
- A report task waits for all three and receives their actual outputs.
- The recovery scenario deliberately omits a README prerequisite from the
  initial plan. A structured missing-evidence signal causes graph mutation.
- The new task fetches README metadata/content; the report resumes using its
  output. No process-local "already seen" state is involved in this workload.
- Workflow state, dependency edges, results, and replan events are persisted.

The public app uses fixed repository identifiers, two source modes, and two
scenarios. Arbitrary URLs, tools, objectives, and paid model calls cannot be
submitted through the public endpoint. PUBLIC_DEMO=1 disables mutation on
legacy/admin routes. Admission is limited to 12 submissions per minute and
four unfinished workflows per API process. These limits assume one API
instance. Multi-replica deployment needs a shared rate limiter.

The separate-process configuration is still docker-compose.yml. The free
hosted demo uses embedded execution plus SQLite and must be described as
such; it does not prove a distributed worker crash was recovered.

## Quantitative evidence

Reproduce the experiment with:

```sh
.venv/bin/python -m benchmarks.verify_product_claims --workflows 100 \
  --seed 731 --output /tmp/relay-benchmark.json
```

The checked-in artifact is benchmarks/results/controlled-demo.json, served
at GET /demo/benchmark. The page renders measurements from that artifact.
It includes every observation, the seed, policy settings, environment, and
limitations. On the recorded run:

- Fault-free median duration: 0.288 s sequential versus 0.108 s concurrent.
- Latency reduction: 62.3% on fixed 80 ms offline fixture reads.
- Completion: 86/100 baseline versus 100/100 with replanning, a 14-point gain.

The baseline disables replanning and limits concurrency to one; this is a
combined-policy comparison. Success gains cannot be attributed to concurrency.
Missing-prerequisite faults are injected, not measured production failures.
The original unqualified "40% faster / +15 points" questionnaire claim is
not established by this experiment. Do not silently replace these measured
numbers, change seeds to meet a target, or generalize them to live services.

## Free hosting

render.yaml declares exactly one **free** Docker web service, without a paid
database, disk, cache, or worker. The image compiles the C++ kernel, includes
browser assets and MCP server sources, and runs as a non-root user.

1. Push the reviewed changes to the connected GitHub repository.
2. In Render, create a Blueprint from this repository and review its plan:
   relay-runtime-explorer must say Free. No paid resources are required.
3. Deploy and use the resulting HTTPS URL in the questionnaire after the
   hosted smoke test passes.

Official limitations: https://render.com/docs/free. Free services spin down
on inactivity; cold starts may take about a minute. Local SQLite data is
lost on restart, redeploy, or spin-down. Shared run links are temporary;
checkpointing is durable within the running instance's filesystem, not
across host replacement. A full distributed deployment with persistent
Postgres and independent workers is a separate deployment choice.

Run a local container equivalent with:

```sh
docker compose -f compose.demo.yml up --build
```

Docker is unavailable on the development Mac. The new CI container job
builds the image, checks the compiled module, and executes an offline
replanning workflow over HTTP. That job must pass before publishing.

## Release verification

- tests/test_repository_workload.py exercises data flow, recovery, reuse of
  completed tasks, live HTTP transport, invalid identifiers, and explicit
  failure handling without depending on GitHub availability.
- API tests exercise the public boundary and recovery report.
- The interface was manually checked in a browser with both live and offline
  data, node inspection, recovery, restored runs, and narrow-screen layout.
- GitHub Actions runs the suite with disposable Postgres/Redis services and
  builds/smoke-tests the Docker image. A workflow file is not a successful
  CI run; inspect its first result after pushing.

## Before calling this production-ready

Finish distributed attempt identity/fencing at the queue boundary, durable
workflow handoff after API or scheduler crashes, per-task result commits,
schema migrations, crash recovery tests for multiple simultaneous workflows,
shared admission control, and managed retention. The existing mechanisms and
unit tests are a foundation; they are not evidence that every failure mode
has been solved. The public demo currently illustrates missing-evidence
replanning, not arbitrary failure recovery or neural routing quality.
