"""Redis-based broker implementation with Lua scripts."""

import asyncio
import json
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import redis.asyncio as aioredis
import structlog

from open_kknaks.broker.base import AbstractBroker
from open_kknaks.task import Priority, StreamEvent, Task

logger = structlog.get_logger()

_LUA_DIR = Path(__file__).parent / "lua"


def _load_lua(name: str) -> str:
    return (_LUA_DIR / f"{name}.lua").read_text()


class RedisBroker(AbstractBroker):
    """Redis-backed task queue broker using Lua scripts for atomicity."""

    def __init__(
        self,
        redis: Any = None,
        url: str = "redis://localhost:6379",
        namespace: str = "open_kknaks",
        result_ttl: int = 3600,
        stream_maxlen: int = 1000,
    ) -> None:
        self._redis: Any = redis
        self._url = url
        self._namespace = namespace
        self._result_ttl = result_ttl
        self._stream_maxlen = stream_maxlen

        # Lua script objects (loaded on connect)
        self._scripts: dict[str, Any] = {}

    def _key(self, *parts: str) -> str:
        for part in parts:
            if ":" in part or "\n" in part or "\r" in part:
                raise ValueError(f"Invalid key component: {part!r}")
        return ":".join([self._namespace, *parts])

    def _score(self, priority: int) -> float:
        """Compute sorted set score: lower = higher priority."""
        return priority * 1e12 + time.time() * 1000

    # ─── Lifecycle ───

    async def connect(self) -> None:
        if self._redis is None:
            self._redis = aioredis.from_url(self._url)

        # Register Lua scripts
        for name in ("enqueue", "dequeue", "ack", "nack", "requeue", "maintenance", "reap_stale"):
            script_src = _load_lua(name)
            self._scripts[name] = self._redis.register_script(script_src)

    async def close(self) -> None:
        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None

    @property
    def redis(self) -> Any:
        assert self._redis is not None, "Broker not connected. Call connect() first."
        return self._redis

    # ─── Queueing ───

    async def enqueue(self, task: Task, *, delay: int | None = None) -> None:
        score = self._score(task.priority)
        task_json = task.model_dump_json()

        delay_score = 0.0
        if delay is not None and delay != 0:
            delay_score = time.time() + delay

        await self._scripts["enqueue"](
            keys=[
                self._key("task", task.id),
                self._key("queue", task.queue),
                self._key("queue", f"{task.queue}.delayed"),
            ],
            args=[task.id, task_json, score, delay_score],
        )

    async def dequeue(self, queue_names: list[str], timeout: float = 1.0) -> Task | None:
        # Try each queue in order
        for queue_name in queue_names:
            result = await self._scripts["dequeue"](
                keys=[
                    self._key("queue", queue_name),
                    self._key("queue", f"{queue_name}.active"),
                ],
                args=[],
            )
            if result is not None:
                task_id = result.decode() if isinstance(result, bytes) else str(result)
                return await self.get_task(task_id)

        # No task found — wait briefly
        if timeout > 0:
            await _async_sleep_short(timeout)
        return None

    async def ack(self, queue_name: str, task_id: str) -> None:
        await self._scripts["ack"](
            keys=[
                self._key("queue", f"{queue_name}.active"),
                self._key("task", task_id),
            ],
            args=[task_id, self._result_ttl],
        )

    async def nack(self, queue_name: str, task_id: str) -> None:
        await self._scripts["nack"](
            keys=[
                self._key("queue", f"{queue_name}.active"),
                self._key("queue", f"{queue_name}.dlq"),
            ],
            args=[task_id],
        )

    async def requeue(self, queue_name: str, task_ids: list[str]) -> None:
        for task_id in task_ids:
            task = await self.get_task(task_id)
            if task:
                score = self._score(task.priority)
                await self._scripts["requeue"](
                    keys=[
                        self._key("queue", f"{queue_name}.active"),
                        self._key("queue", queue_name),
                    ],
                    args=[task_id, score],
                )

    # ─── State / Results ───

    async def get_task(self, task_id: str) -> Task | None:
        data = await self.redis.hget(self._key("task", task_id), "data")
        if data is None:
            return None
        json_str = data.decode() if isinstance(data, bytes) else str(data)
        return Task.model_validate_json(json_str)

    async def update_task(self, task: Task) -> None:
        await self.redis.hset(
            self._key("task", task.id),
            "data",
            task.model_dump_json(),
        )

    # ─── Streaming ───

    async def publish_chunk(self, task_id: str, chunk: StreamEvent) -> None:
        stream_key = self._key("stream", task_id)
        await self.redis.xadd(
            stream_key,
            {"data": chunk.model_dump_json()},
            maxlen=self._stream_maxlen,
        )

    async def subscribe_chunks(self, task_id: str) -> AsyncIterator[StreamEvent]:
        stream_key = self._key("stream", task_id)
        last_id = "0-0"

        while True:
            results = await self.redis.xread(
                {stream_key: last_id},
                block=1000,
                count=100,
            )
            if not results:
                # Check if task is done
                task = await self.get_task(task_id)
                if task and task.status in ("done", "failed", "cancelled"):
                    # Drain remaining
                    results = await self.redis.xread(
                        {stream_key: last_id},
                        count=100,
                    )
                    if results:
                        for _, messages in results:
                            for msg_id, fields in messages:
                                last_id = msg_id
                                data = fields.get(b"data", b"")
                                if data:
                                    json_str = data.decode() if isinstance(data, bytes) else str(data)
                                    yield StreamEvent.model_validate_json(json_str)
                    return
                continue

            for _, messages in results:
                for msg_id, fields in messages:
                    last_id = msg_id
                    data = fields.get(b"data", b"")
                    if data:
                        json_str = data.decode() if isinstance(data, bytes) else str(data)
                        yield StreamEvent.model_validate_json(json_str)

    # ─── DLQ ───

    async def list_dlq(self, queue_name: str, limit: int = 100) -> list[Task]:
        dlq_key = self._key("queue", f"{queue_name}.dlq")
        task_ids = await self.redis.lrange(dlq_key, 0, limit - 1)
        tasks: list[Task] = []
        for tid in task_ids:
            task_id = tid.decode() if isinstance(tid, bytes) else str(tid)
            task = await self.get_task(task_id)
            if task:
                tasks.append(task)
        return tasks

    async def retry_from_dlq(self, queue_name: str, task_id: str) -> None:
        dlq_key = self._key("queue", f"{queue_name}.dlq")
        await self.redis.lrem(dlq_key, 1, task_id)
        task = await self.get_task(task_id)
        if task:
            task.status = "pending"
            task.retry_count += 1
            await self.update_task(task)
            await self.enqueue(task)

    async def purge_dlq(self, queue_name: str) -> None:
        dlq_key = self._key("queue", f"{queue_name}.dlq")
        await self.redis.delete(dlq_key)

    # ─── Worker Management ───

    async def register_worker(
        self,
        worker_id: str,
        queues: list[str],
        extra: dict[str, str] | None = None,
    ) -> None:
        info: dict[str, object] = {
            "queues": queues,
            "last_heartbeat": time.time(),
        }
        if extra:
            info.update(extra)
        await self.redis.hset(self._key("workers"), worker_id, json.dumps(info))

    async def deregister_worker(self, worker_id: str) -> None:
        await self.redis.hdel(self._key("workers"), worker_id)

    async def heartbeat(self, worker_id: str) -> None:
        raw = await self.redis.hget(self._key("workers"), worker_id)
        if raw:
            info = json.loads(raw.decode() if isinstance(raw, bytes) else str(raw))
            info["last_heartbeat"] = time.time()
            await self.redis.hset(self._key("workers"), worker_id, json.dumps(info))

    async def reap_stale_workers(self, timeout: float = 60.0) -> list[str]:
        workers_key = self._key("workers")
        all_workers = await self.redis.hgetall(workers_key)
        now = time.time()
        reaped: list[str] = []

        for raw_id, raw_info in all_workers.items():
            worker_id = raw_id.decode() if isinstance(raw_id, bytes) else str(raw_id)
            info = json.loads(raw_info.decode() if isinstance(raw_info, bytes) else str(raw_info))
            last_hb = info.get("last_heartbeat", 0.0)

            if now - last_hb < timeout:
                continue

            # Stale worker — requeue its active tasks for each queue
            queues = info.get("queues", [])
            total_requeued = 0
            for queue_name in queues:
                count = await self._scripts["reap_stale"](
                    keys=[
                        self._key("queue", f"{queue_name}.active"),
                        self._key("queue", queue_name),
                    ],
                    args=[self._score(Priority.NORMAL)],
                )
                total_requeued += int(count)

            # Remove worker from registry
            await self.redis.hdel(workers_key, worker_id)
            reaped.append(worker_id)
            logger.warning(
                "worker.reaped",
                worker_id=worker_id,
                stale_seconds=round(now - last_hb, 1),
                requeued=total_requeued,
            )

        return reaped

    async def queue_size(self, queue_name: str) -> int:
        result: int = await self.redis.zcard(self._key("queue", queue_name))
        return result

    # ─── Costs ───

    async def incr_cost(self, amount: float, worker_id: str | None = None) -> None:
        await self.redis.incrbyfloat(self._key("cost", "total"), amount)
        if worker_id:
            await self.redis.incrbyfloat(self._key("cost", "worker", worker_id), amount)

    async def get_total_cost(self) -> float:
        val = await self.redis.get(self._key("cost", "total"))
        if val is None:
            return 0.0
        return float(val)

    async def get_worker_cost(self, worker_id: str) -> float:
        val = await self.redis.get(self._key("cost", "worker", worker_id))
        if val is None:
            return 0.0
        return float(val)

    # ─── Maintenance ───

    async def promote_delayed(self, queue_name: str) -> int:
        """Move delayed tasks that are due to the main queue."""
        result = await self._scripts["maintenance"](
            keys=[
                self._key("queue", f"{queue_name}.delayed"),
                self._key("queue", queue_name),
            ],
            args=[time.time(), self._score(Priority.NORMAL)],
        )
        count: int = int(result)
        return count


async def _async_sleep_short(seconds: float) -> None:
    """Sleep for a short duration, used for dequeue polling."""
    await asyncio.sleep(min(seconds, 1.0))
