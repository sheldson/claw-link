"""ClawLink Relay Server — encrypted mail relay for agent collaboration."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from relay.config import settings
from relay.database import init_db
from relay.routes import friends, messages, registry


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize database on startup."""
    await init_db()
    yield


app = FastAPI(
    title="ClawLink Relay",
    description="Encrypted mail relay for agent-to-agent collaboration",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(registry.router)
app.include_router(friends.router)
app.include_router(messages.router)


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={"error": "internal_error", "detail": str(exc)},
    )


@app.get("/health")
async def health():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "relay.main:app",
        host=settings.host,
        port=settings.port,
        reload=True,
    )
