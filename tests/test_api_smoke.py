from __future__ import annotations

import os
import tempfile

_db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_db_file.name}")

from fastapi.testclient import TestClient  # noqa: E402

from runtime.api.main import app  # noqa: E402


def test_end_to_end_workflow_lifecycle():
    with TestClient(app) as client:
        resp = client.post(
            "/workflows", json={"objective": "Analyze NVIDIA, AMD, and Intel."}
        )
        assert resp.status_code == 201
        workflow_id = resp.json()["id"]

        got = client.get(f"/workflows/{workflow_id}")
        assert got.status_code == 200
        body = got.json()
        assert body["status"] == "COMPLETED"
        assert len(body["tasks"]) == 4
        assert all(t["status"] == "COMPLETED" for t in body["tasks"])


def test_tools_endpoint_lists_registered_tools_including_mcp_discovered_ones():
    with TestClient(app) as client:
        resp = client.get("/tools")
        assert resp.status_code == 200
        names = {t["name"] for t in resp.json()}
        assert {"echo", "uppercase", "delay", "lookup_fast", "lookup_careful"} <= names
        # Proves MCP discovery actually ran during this app's lifespan, not
        # just that the in-process tools are listed.
        assert "mcp.search_web" in names


def test_routing_decisions_endpoint_returns_404_for_unknown_workflow():
    with TestClient(app) as client:
        resp = client.get("/workflows/does_not_exist/routing-decisions")
        assert resp.status_code == 404


def test_replan_events_endpoint_returns_empty_list_for_a_workflow_with_no_replans():
    with TestClient(app) as client:
        resp = client.post(
            "/workflows", json={"objective": "Analyze NVIDIA, AMD, and Intel."}
        )
        workflow_id = resp.json()["id"]

        events = client.get(f"/workflows/{workflow_id}/replan-events")
        assert events.status_code == 200
        assert events.json() == []


def test_replan_events_endpoint_returns_404_for_unknown_workflow():
    with TestClient(app) as client:
        resp = client.get("/workflows/does_not_exist/replan-events")
        assert resp.status_code == 404
