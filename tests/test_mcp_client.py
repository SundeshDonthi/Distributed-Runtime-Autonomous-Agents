from __future__ import annotations

import pytest
from mcp import StdioServerParameters

from runtime.tools.mcp_client import DEMO_CAPABILITY_MAP, DEMO_SERVER_PARAMS, discover_tools
from runtime.tools.registry import (
    UnknownToolError,
    get_tool_metadata,
    register_tool,
    tools_for_capability,
    unregister_tool,
)


@pytest.fixture(scope="module")
def demo_mcp_source():
    """One real subprocess for the whole module -- the handshake (spawning a
    Python interpreter, stdio negotiation) is too slow to repeat per test.
    """
    source, metadata = discover_tools(DEMO_SERVER_PARAMS, DEMO_CAPABILITY_MAP)
    for m in metadata:
        register_tool(m)
    yield source, metadata
    for m in metadata:
        unregister_tool(m.name)
    source.stop()


def test_discovers_the_demo_servers_tool(demo_mcp_source):
    _source, metadata = demo_mcp_source
    assert {m.name for m in metadata} == {"mcp.search_web"}
    assert metadata[0].capability == "lookup"


def test_discovered_tool_carries_the_servers_own_schema(demo_mcp_source):
    _source, metadata = demo_mcp_source
    schema = metadata[0].input_schema
    assert schema["type"] == "object"
    assert "query" in schema["properties"]
    assert "query" in schema["required"]


def test_discovered_tool_is_registered_and_joins_the_lookup_capability(demo_mcp_source):
    tools = tools_for_capability("lookup")
    names = {t.name for t in tools}
    assert "mcp.search_web" in names
    # Phase 3's own definition of done: a newly registered tool shows up
    # without anyone having touched scheduler or routing code for it.
    assert names == {"lookup_fast", "lookup_careful", "mcp.search_web"}


def test_calling_the_discovered_tool_round_trips_through_the_subprocess(demo_mcp_source):
    # search_web has a genuine ~10% simulated failure rate (same pattern as
    # lookup_fast/lookup_careful) -- asserting unconditional success here
    # would itself be flaky. Checking both real outcomes also proves errors
    # actually propagate as exceptions through the MCP bridge, not just that
    # the happy path works. Note the MCP server deliberately replaces a
    # tool's real exception message with a generic "Error executing tool
    # <name>" before it crosses the process boundary (not leaking internal
    # server details to a client by default) -- so the *original* message
    # ("search_web: simulated upstream failure") never actually reaches us.
    metadata = get_tool_metadata("mcp.search_web")
    try:
        result = metadata.fn({"query": "gpu pricing"})
    except RuntimeError as exc:
        assert "search_web" in str(exc)
    else:
        assert result["source"] == "search_web"
        assert "gpu pricing" in result["result"]


def test_mcp_server_side_failure_propagates_as_an_exception(demo_mcp_source):
    metadata = get_tool_metadata("mcp.search_web")
    with pytest.raises(RuntimeError, match="search_web"):
        metadata.fn({"query": "x", "force_fail": True})


def test_unknown_tool_name_still_raises_after_mcp_tools_are_registered(demo_mcp_source):
    with pytest.raises(UnknownToolError):
        get_tool_metadata("mcp.does_not_exist")


def test_discover_tools_fails_cleanly_against_a_bad_command():
    bad_params = StdioServerParameters(command="definitely-not-a-real-executable", args=[])
    with pytest.raises(Exception):  # noqa: B017 - exact exception type is transport-dependent
        discover_tools(bad_params, {}, handshake_timeout=2.0)
