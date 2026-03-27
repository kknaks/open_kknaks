"""Example FastAPI app — web UI + REST API for task queue."""

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from open_kknaks.broker.redis import RedisBroker
from open_kknaks.client import ClaudeClient


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    broker = RedisBroker(
        url=os.environ.get("REDIS_URL", "redis://localhost:6379"),
        namespace=os.environ.get("NAMESPACE", "example"),
    )
    await broker.connect()
    app.state.client = ClaudeClient(broker=broker)
    app.state.broker = broker
    yield
    await broker.close()


app = FastAPI(title="open_kknaks Example", lifespan=lifespan)
_THIS_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(_THIS_DIR / "templates"))


class SubmitRequest(BaseModel):
    prompt: str
    context: str | None = None
    queue: str = "default"
    priority: str = "normal"


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "index.html")


@app.get("/health")
async def health(request: Request) -> dict[str, object]:
    """Health check: Redis connection + worker info + Claude status."""
    import json as _json

    broker: RedisBroker = request.app.state.broker
    try:
        await broker.redis.ping()
        redis_ok = True
    except Exception:
        redis_ok = False

    # Check registered workers and their claude status
    workers_raw = await broker.redis.hgetall(broker._key("workers"))
    workers: list[dict[str, object]] = []
    for wid, wdata in workers_raw.items():
        worker_id = wid.decode() if isinstance(wid, bytes) else str(wid)
        info = _json.loads(wdata.decode() if isinstance(wdata, bytes) else str(wdata))
        workers.append({
            "id": worker_id,
            "claude": info.get("claude", "unknown"),
            "claude_version": info.get("claude_version", ""),
            "queues": info.get("queues", []),
        })

    # Queue sizes
    queues_info: dict[str, int] = {}
    for q in ("default", "analysis", "review"):
        queues_info[q] = await broker.queue_size(q)

    # Overall claude status
    claude_statuses = [w["claude"] for w in workers]
    if not claude_statuses:
        claude_status = "no_workers"
    elif all(s == "ok" for s in claude_statuses):
        claude_status = "connected"
    elif any(s == "ok" for s in claude_statuses):
        claude_status = "partial"
    else:
        claude_status = "disconnected"

    return {
        "redis": "connected" if redis_ok else "disconnected",
        "claude": claude_status,
        "workers": workers,
        "worker_count": len(workers),
        "queues": queues_info,
        "namespace": broker._namespace,
    }


@app.post("/submit")
async def submit_task(req: SubmitRequest, request: Request) -> dict[str, str]:
    client: ClaudeClient = request.app.state.client
    task_id = await client.submit(
        prompt=req.prompt,
        context=req.context,
        queue=req.queue,
    )
    return {"task_id": task_id}


@app.get("/status/{task_id}")
async def get_status(task_id: str, request: Request) -> dict[str, str | None]:
    client: ClaudeClient = request.app.state.client
    status = await client.status(task_id)
    return {"task_id": task_id, "status": status}


@app.get("/result/{task_id}")
async def get_result(task_id: str, request: Request) -> dict[str, object]:
    client: ClaudeClient = request.app.state.client
    task = await client.result(task_id, timeout=600)
    if task is None:
        return {"task_id": task_id, "status": "not_found", "result": None}
    return {
        "task_id": task.id,
        "status": task.status,
        "result": task.result,
        "usage": task.usage.model_dump() if task.usage else None,
    }


@app.get("/stream/{task_id}")
async def stream_task(task_id: str, request: Request) -> EventSourceResponse:
    client: ClaudeClient = request.app.state.client

    async def generate() -> AsyncIterator[dict[str, str]]:
        async for event in client.stream(task_id):
            if event.text:
                yield {"event": "text", "data": event.text}
            elif event.type == "retry":
                yield {"event": "retry", "data": str(event.retry_info)}
        yield {"event": "done", "data": ""}

    return EventSourceResponse(generate())
