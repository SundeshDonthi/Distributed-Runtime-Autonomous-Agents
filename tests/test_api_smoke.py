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
