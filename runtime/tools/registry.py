from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import Callable

ToolFn = Callable[[dict], dict]


class UnknownToolError(KeyError):
    pass


class UnknownCapabilityError(KeyError):
    pass


_OBJECT_SCHEMA = {"type": "object"}


@dataclass(frozen=True)
class ToolMetadata:
    """Section 15B.2's "candidate tool metadata" -- enough for a router
    (heuristic today, neural later) to compare multiple tools that offer the
    same capability, and for `Worker` to validate a call's arguments before
    making it (Section 15A.1: routing/execution "never bypasses... schema
    validation").
    """

    name: str
    capability: str
    fn: ToolFn
    base_latency_seconds: float
    reliability: float  # assumed success probability, 0..1
    cost_per_call: float
    input_schema: dict = field(default_factory=lambda: dict(_OBJECT_SCHEMA))


def _echo(args: dict) -> dict:
    return {"result": args.get("text", "")}


def _uppercase(args: dict) -> dict:
    return {"result": args.get("text", "").upper()}


def _delay(args: dict) -> dict:
    """Blocks for `seconds` before returning. Simulates a slow I/O-bound tool
    call (Section 6's worked example) so parallel dispatch has something
    measurable to speed up; has no real side effects.
    """
    seconds = float(args.get("seconds", 0))
    time.sleep(seconds)
    return {"result": args.get("text", ""), "slept_seconds": seconds}


def _make_lookup(name: str, reliability: float, latency_seconds: float) -> ToolFn:
    """A stand-in for a real tool (e.g. web.search) with a tunable latency and
    failure rate, so a router has a genuine reliability/latency tradeoff to
    reason about -- Section 15's own Tool A (200ms, 92%) vs Tool B (450ms,
    99%) example -- instead of a deterministic always-succeeds function.
    """

    def _lookup(args: dict) -> dict:
        time.sleep(latency_seconds)
        if random.random() > reliability:
            raise RuntimeError(f"{name}: simulated failure")
        return {"result": args.get("query", ""), "source": name}

    return _lookup


def _idempotent_charge(args: dict) -> dict:
    """Simulates a real payment-style external API that supports idempotency
    keys (Section 15F.3): the *external system itself* refuses to double-
    charge a given key, which is the only thing that can make a retried
    side effect genuinely safe. Uses its own session (not the caller's) --
    a real external API wouldn't share a DB transaction with us either.
    """
    from runtime.state import repository
    from runtime.state.db import get_session

    idempotency_key = args["idempotency_key"]
    amount = args["amount"]
    with get_session() as session:
        existing = repository.get_external_charge(session, idempotency_key)
        if existing is not None:
            return {"charged": existing.amount, "duplicate_suppressed": True}
        repository.record_external_charge(session, idempotency_key, amount)
        return {"charged": amount, "duplicate_suppressed": False}


def _naive_charge(args: dict) -> dict:
    """The honest counter-example: a tool with no idempotency support at all.
    Calling it twice for "the same" logical operation charges twice -- Section
    15K's "exactly-once behavior is limited by external tool semantics" made
    concrete, not just asserted.
    """
    from runtime.state import repository
    from runtime.state.db import get_session

    amount = args["amount"]
    with get_session() as session:
        import uuid

        repository.record_external_charge(session, uuid.uuid4().hex, amount)
    return {"charged": amount, "duplicate_suppressed": False}


_TEXT_ARG_SCHEMA = {"type": "object", "properties": {"text": {"type": "string"}}}
_DELAY_ARG_SCHEMA = {
    "type": "object",
    "properties": {"seconds": {"type": "number"}, "text": {"type": "string"}},
}
_LOOKUP_ARG_SCHEMA = {"type": "object", "properties": {"query": {"type": "string"}}}
_CHARGE_ARG_SCHEMA = {
    "type": "object",
    "properties": {"idempotency_key": {"type": "string"}, "amount": {"type": "number"}},
    "required": ["amount"],
}

# Phase 1/2 tools (echo, uppercase, delay) are deterministic and single-option
# -- there's nothing to route between. `lookup_fast`/`lookup_careful` share
# the "lookup" capability with different latency/reliability/cost tradeoffs
# specifically so a router (Section 15) has a real decision to make. None of
# these schemas mark their fields "required": the underlying functions are
# already tolerant of missing args (`.get(..., default)`), so the schema
# reflects what the tool actually accepts rather than inventing a stricter
# contract than the implementation honors.
_REGISTRY: dict[str, ToolMetadata] = {
    "echo": ToolMetadata("echo", "echo", _echo, 0.0, 1.0, 0.0, _TEXT_ARG_SCHEMA),
    "uppercase": ToolMetadata("uppercase", "uppercase", _uppercase, 0.0, 1.0, 0.0, _TEXT_ARG_SCHEMA),
    "delay": ToolMetadata("delay", "delay", _delay, 0.0, 1.0, 0.0, _DELAY_ARG_SCHEMA),
    "lookup_fast": ToolMetadata(
        name="lookup_fast",
        capability="lookup",
        fn=_make_lookup("lookup_fast", reliability=0.85, latency_seconds=0.05),
        base_latency_seconds=0.05,
        reliability=0.85,
        cost_per_call=0.001,
        input_schema=_LOOKUP_ARG_SCHEMA,
    ),
    "lookup_careful": ToolMetadata(
        name="lookup_careful",
        capability="lookup",
        fn=_make_lookup("lookup_careful", reliability=0.99, latency_seconds=0.3),
        base_latency_seconds=0.3,
        reliability=0.99,
        cost_per_call=0.01,
        input_schema=_LOOKUP_ARG_SCHEMA,
    ),
    "idempotent_charge": ToolMetadata(
        name="idempotent_charge",
        capability="charge",
        fn=_idempotent_charge,
        base_latency_seconds=0.0,
        reliability=1.0,
        cost_per_call=0.0,
        input_schema=_CHARGE_ARG_SCHEMA,
    ),
    "naive_charge": ToolMetadata(
        name="naive_charge",
        capability="charge",
        fn=_naive_charge,
        base_latency_seconds=0.0,
        reliability=1.0,
        cost_per_call=0.0,
        input_schema=_CHARGE_ARG_SCHEMA,
    ),
}


def get_tool(name: str) -> ToolFn:
    try:
        return _REGISTRY[name].fn
    except KeyError as exc:
        raise UnknownToolError(name) from exc


def get_tool_metadata(name: str) -> ToolMetadata:
    try:
        return _REGISTRY[name]
    except KeyError as exc:
        raise UnknownToolError(name) from exc


def tools_for_capability(capability: str) -> list[ToolMetadata]:
    return [t for t in _REGISTRY.values() if t.capability == capability]


def register_tool(metadata: ToolMetadata) -> None:
    _REGISTRY[metadata.name] = metadata


def unregister_tool(name: str) -> None:
    """Mainly for tests: removes a dynamically-registered tool (e.g. one
    discovered over MCP) so one test's registrations can't leak into another.
    """
    _REGISTRY.pop(name, None)


def list_tools() -> list[str]:
    return sorted(_REGISTRY)
