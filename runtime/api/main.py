from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from runtime.api.demo import router as demo_router

from runtime.api.routes import router
from runtime.state.db import init_db
from runtime.tools.mcp_client import DEMO_CAPABILITY_MAP, DEMO_SERVER_PARAMS, discover_tools
from runtime.tools.registry import register_tool, unregister_tool

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()

    # MCP discovery is optional at startup (Section 15K: "MCP is an optional
    # discovery transport; native API adapters must also work") -- a demo
    # server failing to launch shouldn't take the whole API down with it.
    mcp_source = None
    discovered = []
    if os.environ.get("ENABLE_DEMO_MCP", "1") != "0":
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

    # Unregister, not just disconnect: leaving an MCP-discovered tool in the
    # registry after its connection is stopped would mean its ToolFn closure
    # tries to call a now-dead subprocess if anything invoked it later (and,
    # for tests specifically, silently leaks into whatever runs next in the
    # same process).
    for tool in discovered:
        unregister_tool(tool.name)
    if mcp_source is not None:
        await asyncio.to_thread(mcp_source.stop)


app = FastAPI(title="Distributed Agent Runtime", version="0.1.0", lifespan=lifespan)
app.include_router(router)
app.include_router(demo_router)
WEB_ROOT = Path(__file__).resolve().parents[1] / "web"
app.mount("/assets", StaticFiles(directory=WEB_ROOT), name="assets")


@app.middleware("http")
async def public_demo_boundary(request: Request, call_next):
    if os.environ.get("PUBLIC_DEMO") == "1" and request.method not in {"GET", "HEAD"}:
        if request.url.path != "/demo/runs":
            return JSONResponse({"detail": "This endpoint is disabled on the public demo."}, status_code=403)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'"
    return response


@app.get("/", include_in_schema=False)
def explorer():
    return FileResponse(WEB_ROOT / "index.html")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
