from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from runtime.state.repository import get_recent_tool_calls
from runtime.tools.registry import ToolMetadata

DEFAULT_WINDOW = 50


@dataclass(frozen=True)
class ObservedStats:
    """A tool's recent real-world performance (Section 15B.3), as opposed to
    the fixed `ToolMetadata` priors -- this is the "prior outcomes" half of
    "select tools... based on task context and prior outcomes."
    """

    success_rate: float
    avg_latency_seconds: float
    sample_size: int


def get_observed_stats(
    session: Session, tool_name: str, window: int = DEFAULT_WINDOW
) -> ObservedStats | None:
    """None means cold start -- no history yet, caller should fall back to
    the tool's static `ToolMetadata` prior (Section 15C.3). A simple
    most-recent-N window stands in for Section 15B.3's "time-windowed or
    exponentially decayed aggregates"; decay is a reasonable later
    refinement once there's enough volume for it to matter.
    """
    calls = get_recent_tool_calls(session, tool_name, window)
    if not calls:
        return None

    successes = sum(1 for c in calls if c.status == "COMPLETED")
    avg_latency_ms = sum(c.latency_ms for c in calls) / len(calls)
    return ObservedStats(
        success_rate=successes / len(calls),
        avg_latency_seconds=avg_latency_ms / 1000.0,
        sample_size=len(calls),
    )


def get_observed_stats_for_capability(
    session: Session, candidates: list[ToolMetadata], window: int = DEFAULT_WINDOW
) -> dict[str, ObservedStats]:
    stats = {}
    for tool in candidates:
        observed = get_observed_stats(session, tool.name, window)
        if observed is not None:
            stats[tool.name] = observed
    return stats
