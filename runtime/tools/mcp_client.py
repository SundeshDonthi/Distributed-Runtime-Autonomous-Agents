from __future__ import annotations

import asyncio
import concurrent.futures
import json
import sys
import threading

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import CallToolResult, Tool

from runtime.tools.registry import ToolMetadata

DEMO_SERVER_PARAMS = StdioServerParameters(
    command=sys.executable, args=["-m", "mcp_servers.demo_tools_server"]
)
DEMO_CAPABILITY_MAP = {"search_web": "lookup"}


class MCPToolSource:
    """Keeps one MCP server subprocess connection alive on a dedicated
    background event loop for as long as the process needs it, and exposes
    its tools as ordinary synchronous `ToolFn`s -- so the rest of the runtime
    (registry, scheduler, worker) never has to know a given call crosses a
    process boundary over stdio (Section 5.6's "dynamically discover
    available tools and expose them to the planner/worker").

    A persistent connection is used rather than reconnecting per call: the
    stdio handshake (spawning the subprocess, `initialize()`) is too
    expensive to repeat for every tool invocation.
    """

    def __init__(self, server_params: StdioServerParameters) -> None:
        self._server_params = server_params
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._stop_event: asyncio.Event | None = None
        self._call_queue: asyncio.Queue | None = None
        self._connection_task: concurrent.futures.Future[None] | None = None

    def start(self, timeout: float = 15.0) -> list[Tool]:
        """Spawns the server subprocess and performs the MCP handshake.
        Returns the tools it advertises. Blocks the calling thread until the
        handshake completes, fails, or `timeout` elapses.
        """
        self._thread.start()
        ready = threading.Event()
        tools_holder: list[Tool] = []
        error_holder: list[BaseException] = []

        async def bootstrap() -> None:
            self._stop_event = asyncio.Event()
            self._call_queue = asyncio.Queue()
            try:
                async with stdio_client(self._server_params) as (read, write):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        result = await session.list_tools()
                        tools_holder.extend(result.tools)
                        ready.set()
                        await self._serve(session)
            except BaseException as exc:  # noqa: BLE001 - re-raised in start()
                error_holder.append(exc)
                ready.set()

        self._connection_task = asyncio.run_coroutine_threadsafe(bootstrap(), self._loop)
        if not ready.wait(timeout):
            raise TimeoutError("MCP server did not complete its handshake in time")
        if error_holder:
            raise error_holder[0]
        return tools_holder

    async def _serve(self, session: ClientSession) -> None:
        """Owns the live session for its whole lifetime, processing queued
        call requests one at a time until told to stop. A request is a
        (name, args, future) tuple; the future is resolved or failed with the
        raw `CallToolResult`/exception so `call()` can unwrap it on whatever
        thread asked for it.
        """
        assert self._stop_event is not None and self._call_queue is not None
        while not self._stop_event.is_set():
            try:
                name, args, result_future = await asyncio.wait_for(
                    self._call_queue.get(), timeout=0.5
                )
            except asyncio.TimeoutError:
                continue
            try:
                result = await session.call_tool(name, args)
                if not result_future.cancelled():
                    result_future.set_result(result)
            except Exception as exc:  # noqa: BLE001 - surfaced via the future
                if not result_future.cancelled():
                    result_future.set_exception(exc)

    def call(self, name: str, args: dict, timeout: float = 30.0) -> dict:
        """Synchronous call bridge -- safe to invoke from any thread,
        including the thread pool a `ToolFn` normally runs in via
        `asyncio.to_thread` (Section 5.5: workers offload tool calls to a
        thread so a blocking one doesn't stall the event loop).
        """
        if self._call_queue is None:
            raise RuntimeError("MCPToolSource.start() must be called before call()")

        async def _enqueue() -> CallToolResult:
            fut = asyncio.get_running_loop().create_future()
            await self._call_queue.put((name, args, fut))
            return await fut

        future = asyncio.run_coroutine_threadsafe(_enqueue(), self._loop)
        return _unwrap_call_result(future.result(timeout=timeout))

    def stop(self, timeout: float = 10.0) -> None:
        if self._stop_event is not None:
            self._loop.call_soon_threadsafe(self._stop_event.set)
        if self._connection_task is not None:
            self._connection_task.result(timeout=timeout)
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=timeout)


def _unwrap_call_result(result: CallToolResult) -> dict:
    if result.is_error:
        message = result.content[0].text if result.content else "MCP tool call failed"
        raise RuntimeError(message)
    if result.structured_content is not None:
        return result.structured_content
    if result.content and hasattr(result.content[0], "text"):
        try:
            return json.loads(result.content[0].text)
        except json.JSONDecodeError:
            return {"result": result.content[0].text}
    return {}


def discover_tools(
    server_params: StdioServerParameters,
    capability_map: dict[str, str],
    *,
    default_latency_seconds: float = 0.1,
    default_reliability: float = 0.9,
    default_cost_per_call: float = 0.0,
    handshake_timeout: float = 15.0,
) -> tuple[MCPToolSource, list[ToolMetadata]]:
    """Connects to an MCP server and converts the tools it advertises into
    `ToolMetadata`, ready for `runtime.tools.registry.register_tool`.
    `capability_map` names which of our internal capabilities (Section 5.3's
    `required_capabilities`) each MCP tool satisfies -- MCP tools carry no
    such tag natively, so the mapping is explicit rather than guessed; a
    discovered tool with no entry in the map is skipped. The initial
    latency/reliability/cost are starting priors only: once the tool has
    actually been called a few times, the router's observed-stats history
    (Section 15B.3) takes over.

    Returns the live `MCPToolSource` (the caller owns its lifetime and must
    call `.stop()` on shutdown) alongside the metadata.
    """
    source = MCPToolSource(server_params)
    tools = source.start(timeout=handshake_timeout)
    metadata = [
        ToolMetadata(
            name=f"mcp.{tool.name}",
            capability=capability_map[tool.name],
            fn=_bind_call(source, tool.name),
            base_latency_seconds=default_latency_seconds,
            reliability=default_reliability,
            cost_per_call=default_cost_per_call,
            input_schema=tool.input_schema or {"type": "object"},
        )
        for tool in tools
        if tool.name in capability_map
    ]
    return source, metadata


def _bind_call(source: MCPToolSource, tool_name: str):
    def _call(args: dict) -> dict:
        return source.call(tool_name, args)

    return _call
