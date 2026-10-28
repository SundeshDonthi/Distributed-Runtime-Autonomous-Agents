"""A tiny real MCP server (Section 5.6), run as a separate subprocess over
stdio -- proof that this runtime's tool registry can be populated by genuine
dynamic discovery, not just hardcoded Python. `search_web` is a stand-in for
a real search API (Section 15K: "MCP is an optional discovery transport;
native API adapters must also work" -- the point here is to exercise the
transport honestly, not to simulate a specific real provider).

Run standalone for manual testing:
    python -m mcp_servers.demo_tools_server
Normally launched as a subprocess by `runtime.tools.mcp_client`.
"""
from __future__ import annotations

import random
import time

from mcp.server.mcpserver import MCPServer

server = MCPServer("demo-tools")


@server.tool()
def search_web(query: str, force_fail: bool = False) -> dict:
    """Search the web for a query and return the top result. `force_fail` is
    a test-only escape hatch for deterministically exercising the failure
    path instead of relying on the ~10% simulated failure rate.
    """
    time.sleep(0.08)
    if force_fail or random.random() > 0.9:
        raise RuntimeError("search_web: simulated upstream failure")
    return {"result": f"top result for: {query}", "source": "search_web"}


if __name__ == "__main__":
    import asyncio

    asyncio.run(server.run_stdio_async())
