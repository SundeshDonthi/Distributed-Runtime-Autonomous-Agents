from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from runtime.api.routes import router
from runtime.state.db import init_db
from runtime.tools.mcp_client import DEMO_CAPABILITY_MAP, DEMO_SERVER_PARAMS, discover_tools
from runtime.tools.registry import register_tool

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()

    # MCP discovery is optional at startup (Section 15K: "MCP is an optional
    # discovery transport; native API adapters must also work") -- a demo
    # server failing to launch shouldn't take the whole API down with it.
    mcp_source = None
    try:
        mcp_source, discovered = await asyncio.to_thread(
            discover_tools, DEMO_SERVER_PARAMS, DEMO_CAPABILITY_MAP
        )
        for tool in discovered:
            register_tool(tool)
        logger.info("Registered %d tool(s) via MCP: %s", len(discovered), [t.name for t in discovered])
    except Exception:
        logger.warning("MCP tool discovery failed; continuing with built-in tools only", exc_info=True)

    yield

    if mcp_source is not None:
        await asyncio.to_thread(mcp_source.stop)


app = FastAPI(title="Distributed Agent Runtime", version="0.1.0", lifespan=lifespan)
app.include_router(router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
