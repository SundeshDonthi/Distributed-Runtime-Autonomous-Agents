from __future__ import annotations

import pytest

from runtime.scheduler.queue import DispatchQueue, PendingWorkflowQueue, ReadyQueue, ResultQueue
from runtime.state.redis_client import get_redis, is_reachable


@pytest.fixture()
def redis_client():
    client = get_redis()
    if not is_reachable(client):
        pytest.skip("redis is not reachable at REDIS_URL")
    yield client
    client.flushdb()


def test_push_and_pop_is_fifo(redis_client):
    queue = ReadyQueue(redis_client, "wf_queue_test")
    queue.clear()
    queue.push("a")
    queue.push("b")

    assert queue.pop() == "a"
    assert queue.pop() == "b"
    assert queue.pop(timeout=0.1) is None


def test_clear_empties_queue(redis_client):
    queue = ReadyQueue(redis_client, "wf_queue_test_2")
    queue.push("a")
    queue.clear()
    assert queue.pop(timeout=0.1) is None


def test_separate_workflows_do_not_share_a_queue(redis_client):
    q1 = ReadyQueue(redis_client, "wf_a")
    q2 = ReadyQueue(redis_client, "wf_b")
    q1.clear()
    q2.clear()

    q1.push("only-in-a")
    assert q2.pop(timeout=0.1) is None
    assert q1.pop(timeout=0.1) == "only-in-a"


def test_dispatch_queue_round_trips_a_task_execution_request(redis_client):
    queue = DispatchQueue(redis_client)
    queue.clear()
    queue.push("wf_1", "t1", "echo", {"text": "hi"})

    request = queue.pop(timeout=0.1)
    assert request == {"workflow_id": "wf_1", "task_id": "t1", "tool": "echo", "args": {"text": "hi"}}
    assert queue.pop(timeout=0.1) is None


def test_dispatch_queue_is_global_not_per_workflow(redis_client):
    """Unlike `ReadyQueue`, a `DispatchQueue` isn't keyed by workflow -- any
    worker process may pick up any workflow's request, which workflow it
    belongs to travels inside the payload instead.
    """
    queue = DispatchQueue(redis_client)
    queue.clear()
    queue.push("wf_a", "ta", "echo", {})
    queue.push("wf_b", "tb", "echo", {})

    first = queue.pop(timeout=0.1)
    second = queue.pop(timeout=0.1)
    assert {first["workflow_id"], second["workflow_id"]} == {"wf_a", "wf_b"}


def test_result_queue_is_scoped_per_workflow(redis_client):
    q1 = ResultQueue(redis_client, "wf_a")
    q2 = ResultQueue(redis_client, "wf_b")
    q1.clear()
    q2.clear()

    q1.push({"task_id": "ta", "status": "COMPLETED", "output": {}, "error": None, "error_category": None})
    assert q2.pop(timeout=0.1) is None
    result = q1.pop(timeout=0.1)
    assert result["task_id"] == "ta"
    assert result["status"] == "COMPLETED"


def test_pending_workflow_queue_is_fifo(redis_client):
    queue = PendingWorkflowQueue(redis_client)
    queue.clear()
    queue.push("wf_1")
    queue.push("wf_2")

    assert queue.pop() == "wf_1"
    assert queue.pop() == "wf_2"
    assert queue.pop(timeout=0.1) is None
