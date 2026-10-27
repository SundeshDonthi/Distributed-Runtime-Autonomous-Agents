from __future__ import annotations

from typing import Callable

ToolFn = Callable[[dict], dict]


class UnknownToolError(KeyError):
    pass


def _echo(args: dict) -> dict:
    return {"result": args.get("text", "")}


def _uppercase(args: dict) -> dict:
    return {"result": args.get("text", "").upper()}


# Phase 1 uses two deterministic, side-effect-free tools so the worker/scheduler
# path can be proven out before any real MCP server or external API is wired in
# (Section 15J, Phase 1 "two deterministic test tools").
_REGISTRY: dict[str, ToolFn] = {
    "echo": _echo,
    "uppercase": _uppercase,
}


def get_tool(name: str) -> ToolFn:
    try:
        return _REGISTRY[name]
    except KeyError as exc:
        raise UnknownToolError(name) from exc


def register_tool(name: str, fn: ToolFn) -> None:
    _REGISTRY[name] = fn


def list_tools() -> list[str]:
    return sorted(_REGISTRY)
