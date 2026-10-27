from __future__ import annotations

import pytest

from runtime.scheduler.queue import ReadyQueue
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
