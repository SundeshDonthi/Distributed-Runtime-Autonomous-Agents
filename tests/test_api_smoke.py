from __future__ import annotations

import tempfile

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import runtime.state.db as db_module
from runtime.api.main import app
from runtime.state import repository
from runtime.state.db import get_session


@pytest.fixture()
def client(monkeypatch):
    """Rebinds `runtime.state.db`'s `engine`/`SessionLocal` to an isolated,
    file-backed SQLite DB for this test, then opens the app.

    This used to rely on `os.environ.setdefault("DATABASE_URL", ...)` before
    importing the app, on the theory that `runtime.state.db` would read the
    env var fresh on its own first import. That doesn't actually happen:
    `tests/conftest.py` imports `runtime.state.db` at collection time --
    which pytest does unconditionally, even when running a single targeted
    test in this file -- binding its `engine`/`SessionLocal` to the real
    local Postgres default *before* this module's own code ever ran. Every
    test below was therefore actually hitting the real dev database all
    along, not an isolated one (confirmed by watching rows land in the real
    `agent_runtime` Postgres DB from a supposedly-isolated test run).
    Monkeypatching the module's attributes directly -- the same approach
    `tests/conftest.py`'s own `db_session` fixture already uses correctly --
    sidesteps the import-order problem entirely instead of racing it.
    """
    db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    engine = create_engine(f"sqlite:///{db_file.name}")
    monkeypatch.setattr(db_module, "engine", engine)
    monkeypatch.setattr(
        db_module, "SessionLocal", sessionmaker(bind=engine, expire_on_commit=False, future=True)
    )

    with TestClient(app) as test_client:
        yield test_client


def test_end_to_end_workflow_lifecycle(client):
    resp = client.post("/workflows", json={"objective": "Analyze NVIDIA, AMD, and Intel."})
    assert resp.status_code == 201
    workflow_id = resp.json()["id"]

    got = client.get(f"/workflows/{workflow_id}")
    assert got.status_code == 200
    body = got.json()
    assert body["status"] == "COMPLETED"
    assert len(body["tasks"]) == 4
    assert all(t["status"] == "COMPLETED" for t in body["tasks"])


def test_graph_endpoint_describes_the_workflow_as_nodes_and_edges(client):
    resp = client.post("/workflows", json={"objective": "Analyze NVIDIA, AMD, and Intel."})
    workflow_id = resp.json()["id"]

    graph = client.get(f"/workflows/{workflow_id}/graph")
    assert graph.status_code == 200
    body = graph.json()
    assert body["workflow_id"] == workflow_id
    assert len(body["nodes"]) == 4
    assert all(n["status"] == "COMPLETED" for n in body["nodes"])
    # At least one dependency edge must exist for a 4-task analysis plan --
    # if every task were independent this wouldn't be much of a DAG.
    assert len(body["edges"]) > 0
    node_ids = {n["id"] for n in body["nodes"]}
    assert all(e["from"] in node_ids and e["to"] in node_ids for e in body["edges"])


def test_graph_endpoint_returns_404_for_unknown_workflow(client):
    resp = client.get("/workflows/does_not_exist/graph")
    assert resp.status_code == 404


def test_trace_endpoint_returns_a_time_ordered_merge_of_every_event_source(client):
    resp = client.post("/workflows", json={"objective": "Analyze NVIDIA, AMD, and Intel."})
    workflow_id = resp.json()["id"]

    trace = client.get(f"/workflows/{workflow_id}/trace")
    assert trace.status_code == 200
    events = trace.json()
    assert len(events) > 0
    # Every task's completion must show up as an outbox event -- the trace's
    # whole point is to be a complete substitute for "tail the process log."
    completed_events = [e for e in events if e["source"] == "outbox" and e["type"] == "task.completed"]
    assert len(completed_events) == 4
    timestamps = [e["timestamp"] for e in events]
    assert timestamps == sorted(timestamps)


def test_trace_endpoint_returns_404_for_unknown_workflow(client):
    resp = client.get("/workflows/does_not_exist/trace")
    assert resp.status_code == 404


def test_tools_endpoint_lists_registered_tools_including_mcp_discovered_ones(client):
    resp = client.get("/tools")
    assert resp.status_code == 200
    names = {t["name"] for t in resp.json()}
    assert {"echo", "uppercase", "delay", "lookup_fast", "lookup_careful"} <= names
    # Proves MCP discovery actually ran during this app's lifespan, not
    # just that the in-process tools are listed.
    assert "mcp.search_web" in names


def test_routing_decisions_endpoint_returns_404_for_unknown_workflow(client):
    resp = client.get("/workflows/does_not_exist/routing-decisions")
    assert resp.status_code == 404


def test_replan_events_endpoint_returns_empty_list_for_a_workflow_with_no_replans(client):
    resp = client.post("/workflows", json={"objective": "Analyze NVIDIA, AMD, and Intel."})
    workflow_id = resp.json()["id"]

    events = client.get(f"/workflows/{workflow_id}/replan-events")
    assert events.status_code == 200
    assert events.json() == []


def test_replan_events_endpoint_returns_404_for_unknown_workflow(client):
    resp = client.get("/workflows/does_not_exist/replan-events")
    assert resp.status_code == 404


def test_router_models_lifecycle_through_the_api(client):
    assert client.get("/router-models").json() == []

    resp = client.post("/admin/router/models/does-not-exist/activate")
    assert resp.status_code == 404

    with get_session() as session:
        repository.save_router_model(
            session, "m1", "features-v1", "dataset-1", {"weights": [1.0], "bias": 0.0}
        )
        repository.save_router_model(
            session, "m2", "features-v1", "dataset-1", {"weights": [2.0], "bias": 0.0}
        )

    models = client.get("/router-models").json()
    assert {m["model_version"] for m in models} == {"m1", "m2"}
    assert all(m["status"] == "shadow" for m in models)

    activated = client.post("/admin/router/models/m1/activate")
    assert activated.status_code == 200
    assert activated.json()["status"] == "active"

    # Activating a second model retires the first -- single-active invariant.
    client.post("/admin/router/models/m2/activate")
    statuses = {m["model_version"]: m["status"] for m in client.get("/router-models").json()}
    assert statuses == {"m1": "retired", "m2": "active"}
