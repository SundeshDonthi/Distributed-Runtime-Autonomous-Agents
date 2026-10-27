from __future__ import annotations

import os

import redis
from redis.backoff import NoBackoff
from redis.retry import Retry

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")


def get_redis() -> redis.Redis:
    return redis.Redis.from_url(REDIS_URL, decode_responses=True)


def is_reachable(client: redis.Redis) -> bool:
    # Both Redis.execute_command() and Connection.connect() retry connection
    # errors with backoff by default, which would make an unreachable Redis
    # stall the scheduler for several seconds before it falls back. Build a
    # one-off connection with the same settings but retries disabled -- using
    # the pool's own connection_class/connection_kwargs, same as the pool
    # itself would -- for a single fast probe.
    kwargs = dict(client.connection_pool.connection_kwargs)
    kwargs["retry"] = Retry(NoBackoff(), 0)
    conn = client.connection_pool.connection_class(**kwargs)
    try:
        conn.connect()
        conn.send_command("PING")
        conn.read_response()
        return True
    except redis.exceptions.RedisError:
        return False
    finally:
        conn.disconnect()
