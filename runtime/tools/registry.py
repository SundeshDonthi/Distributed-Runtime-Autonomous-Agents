from __future__ import annotations

import random
import time
from dataclasses import dataclass
from typing import Callable

ToolFn = Callable[[dict], dict]


class UnknownToolError(KeyError):
    pass


class UnknownCapabilityError(KeyError):
    pass


@dataclass(frozen=True)
class ToolMetadata:
    """Section 15B.2's "candidate tool metadata" -- enough for a router
    (heuristic today, neural later) to compare multiple tools that offer the
    same capability.
    """

    name: str
    capability: str
    fn: ToolFn
    base_latency_seconds: float
    reliability: float  # assumed success probability, 0..1
    cost_per_call: float


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


# Phase 1/2 tools (echo, uppercase, delay) are deterministic and single-option
# -- there's nothing to route between. `lookup_fast`/`lookup_careful` share
# the "lookup" capability with different latency/reliability/cost tradeoffs
# specifically so a router (Section 15) has a real decision to make.
_REGISTRY: dict[str, ToolMetadata] = {
    "echo": ToolMetadata("echo", "echo", _echo, 0.0, 1.0, 0.0),
    "uppercase": ToolMetadata("uppercase", "uppercase", _uppercase, 0.0, 1.0, 0.0),
    "delay": ToolMetadata("delay", "delay", _delay, 0.0, 1.0, 0.0),
    "lookup_fast": ToolMetadata(
        name="lookup_fast",
        capability="lookup",
        fn=_make_lookup("lookup_fast", reliability=0.85, latency_seconds=0.05),
        base_latency_seconds=0.05,
        reliability=0.85,
        cost_per_call=0.001,
    ),
    "lookup_careful": ToolMetadata(
        name="lookup_careful",
        capability="lookup",
        fn=_make_lookup("lookup_careful", reliability=0.99, latency_seconds=0.3),
        base_latency_seconds=0.3,
        reliability=0.99,
        cost_per_call=0.01,
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


def list_tools() -> list[str]:
    return sorted(_REGISTRY)
