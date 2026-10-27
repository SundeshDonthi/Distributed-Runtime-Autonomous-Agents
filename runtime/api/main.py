from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from runtime.api.routes import router
from runtime.state.db import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Distributed Agent Runtime", version="0.1.0", lifespan=lifespan)
app.include_router(router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
