"""Example FastAPI app — web UI + REST API for task queue."""

import json
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from open_kknaks.broker.redis import RedisBroker
from open_kknaks.client import AgentClient
from open_kknaks.constants import DEFAULT_PROVIDER, PROVIDER_CODEX


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    broker = RedisBroker(
        url=os.environ.get("REDIS_URL", "redis://localhost:6379"),
        namespace=os.environ.get("NAMESPACE", "example"),
    )
    await broker.connect()
    app.state.client = AgentClient(broker=broker)
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
    provider: str = DEFAULT_PROVIDER
    model: str | None = None
    options: dict[str, object] | None = None
    provider_options: dict[str, object] | None = None


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "index.html")


@app.get("/health")
async def health(request: Request) -> dict[str, object]:
    """Health check: Redis connection + worker info + provider status."""
    import json as _json

    broker: RedisBroker = request.app.state.broker
    try:
        await broker.redis.ping()
        redis_ok = True
    except Exception:
        redis_ok = False

    # Check registered workers and their provider status
    workers_raw = await broker.redis.hgetall(broker._key("workers"))
    workers: list[dict[str, object]] = []
    for wid, wdata in workers_raw.items():
        worker_id = wid.decode() if isinstance(wid, bytes) else str(wid)
        info = _json.loads(wdata.decode() if isinstance(wdata, bytes) else str(wdata))
        workers.append({
            "id": worker_id,
            "claude": info.get("claude", "unknown"),
            "claude_version": info.get("claude_version", ""),
            "codex": info.get("codex", "unknown"),
            "codex_version": info.get("codex_version", ""),
            "queues": info.get("queues", []),
        })

    # Queue sizes
    queues_info: dict[str, int] = {}
    for q in ("default", "analysis", "review"):
        queues_info[q] = await broker.queue_size(q)

    def provider_status(provider: str) -> str:
        statuses = [w[provider] for w in workers]
        if not statuses:
            return "no_workers"
        if all(s == "ok" for s in statuses):
            return "connected"
        if any(s == "ok" for s in statuses):
            return "partial"
        return "disconnected"

    return {
        "redis": "connected" if redis_ok else "disconnected",
        "claude": provider_status("claude"),
        "codex": provider_status(PROVIDER_CODEX),
        "workers": workers,
        "worker_count": len(workers),
        "queues": queues_info,
        "namespace": broker._namespace,
    }


@app.post("/submit")
async def submit_task(req: SubmitRequest, request: Request) -> dict[str, str]:
    client: AgentClient = request.app.state.client
    task_id = await client.submit(
        prompt=req.prompt,
        context=req.context,
        queue=req.queue,
        provider=req.provider,
        model=req.model,
        options=req.options,
        provider_options=req.provider_options,
    )
    return {"task_id": task_id}


@app.get("/status/{task_id}")
async def get_status(task_id: str, request: Request) -> dict[str, str | None]:
    client: AgentClient = request.app.state.client
    status = await client.status(task_id)
    return {"task_id": task_id, "status": status}


@app.get("/result/{task_id}")
async def get_result(task_id: str, request: Request) -> dict[str, object]:
    client: AgentClient = request.app.state.client
    task = await client.result(task_id, timeout=600)
    if task is None:
        return {"task_id": task_id, "status": "not_found", "result": None}
    return {
        "task_id": task.id,
        "status": task.status,
        "result": task.result,
        "error": task.error,
        "exit_code": task.exit_code,
        "usage": task.usage.model_dump() if task.usage else None,
    }


@app.get("/stream/{task_id}")
async def stream_task(task_id: str, request: Request) -> EventSourceResponse:
    client: AgentClient = request.app.state.client

    async def generate() -> AsyncIterator[dict[str, str]]:
        async for event in client.stream(task_id):
            if event.text:
                yield {"event": "text", "data": event.text}
            elif event.type == "retry":
                yield {"event": "retry", "data": str(event.retry_info)}
        task = await client.result(task_id, timeout=1)
        payload = {
            "status": task.status if task else "not_found",
            "result": task.result if task else None,
            "error": task.error if task else None,
            "exit_code": task.exit_code if task else None,
        }
        yield {"event": "done", "data": json.dumps(payload, ensure_ascii=False)}

    return EventSourceResponse(generate())
