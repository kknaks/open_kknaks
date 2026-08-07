"""Integration tests for cancelling a task that is already running.

Regression: `AgentClient.cancel` only wrote status=CANCELLED to the broker. The worker
read that status once, right after dequeue, and never again while awaiting
`adapter.execute` — so a cancel issued mid-run did nothing and the CLI kept burning
tokens until it finished on its own.
"""

import asyncio

import pytest
import pytest_asyncio
from fakeredis import aioredis as fake_aioredis

from open_kknaks.broker.redis import RedisBroker
from open_kknaks.client import AgentClient
from open_kknaks.config import ClaudeConfig
from open_kknaks.constants import PROVIDER_CLAUDE
from open_kknaks.task import StreamEvent, Task, TaskResult, TaskStatus
from open_kknaks.worker.worker import ClaudeWorker


@pytest_asyncio.fixture
async def broker():
    server = fake_aioredis.FakeServer()
    redis = fake_aioredis.FakeRedis(server=server)
    b = RedisBroker(redis=redis, namespace="test")
    await b.connect()
    yield b
    await b.close()


class SlowAdapter:
    """Stands in for a long CLI run: blocks until cancelled or until `runtime` elapses.

    `cancel()` mimics the real process.terminate() path — it unblocks `execute`, which
    then returns the non-zero exit code a killed process produces.
    """

    provider = PROVIDER_CLAUDE

    def __init__(self, runtime: float = 30.0) -> None:
        self.runtime = runtime
        self.cancelled: list[str] = []
        self.started = asyncio.Event()
        self._terminate: dict[str, asyncio.Event] = {}

    def health_check(self) -> dict[str, str]:
        return {}

    async def execute(self, task, config, on_chunk=None) -> TaskResult:  # type: ignore[no-untyped-def]
        stop = asyncio.Event()
        self._terminate[task.id] = stop
        if on_chunk:
            await on_chunk(StreamEvent(type="text", text="working"))
        self.started.set()
        try:
            await asyncio.wait_for(stop.wait(), timeout=self.runtime)
        except (TimeoutError, asyncio.TimeoutError):
            return TaskResult(result="finished on its own", exit_code=0)
        # Killed: a terminated CLI exits non-zero.
        return TaskResult(result="", stream="partial", exit_code=-15)

    async def cancel(self, task_id: str) -> bool:
        self.cancelled.append(task_id)
        stop = self._terminate.get(task_id)
        if stop is None:
            return False
        stop.set()
        return True

    async def cleanup_all(self) -> int:
        return 0

    def active_task_ids(self) -> list[str]:
        return list(self._terminate)


def _make_worker(broker: RedisBroker, adapter: SlowAdapter) -> ClaudeWorker:
    worker = ClaudeWorker(broker=broker, config=ClaudeConfig(), queues=["default"])
    worker._adapters[PROVIDER_CLAUDE] = adapter  # type: ignore[assignment]
    worker.cancel_poll_interval = 0.05
    worker.cancel_grace_period = 2.0
    return worker


async def _dequeued_task(broker: RedisBroker, prompt: str = "long running") -> Task:
    task = Task(prompt=prompt, queue="default", provider=PROVIDER_CLAUDE)
    await broker.enqueue(task)
    await broker.dequeue(["default"], timeout=0)
    return task


class TestCancelWhileRunning:
    @pytest.mark.asyncio
    async def test_cancel_terminates_process_and_settles_as_cancelled(self, broker: RedisBroker) -> None:
        adapter = SlowAdapter()
        worker = _make_worker(broker, adapter)
        task = await _dequeued_task(broker)

        running = asyncio.create_task(worker._process_task(task))
        await asyncio.wait_for(adapter.started.wait(), timeout=2.0)

        # The real public cancellation path — writes status=CANCELLED to the broker only.
        assert await AgentClient(broker=broker).cancel(task.id) is True

        await asyncio.wait_for(running, timeout=5.0)

        # The process was actually terminated, not merely marked.
        assert adapter.cancelled == [task.id]

        stored = await broker.get_task(task.id)
        assert stored is not None
        assert stored.status == TaskStatus.CANCELLED
        assert stored.finished_at is not None

    @pytest.mark.asyncio
    async def test_cancelled_task_is_acked_not_sent_to_dlq(self, broker: RedisBroker) -> None:
        """A killed process exits non-zero; that must not be recorded as a failure."""
        adapter = SlowAdapter()
        worker = _make_worker(broker, adapter)
        task = await _dequeued_task(broker)

        running = asyncio.create_task(worker._process_task(task))
        await asyncio.wait_for(adapter.started.wait(), timeout=2.0)
        await AgentClient(broker=broker).cancel(task.id)
        await asyncio.wait_for(running, timeout=5.0)

        assert await broker.list_dlq("default") == []
        active = await broker.redis.smembers(broker._key("queue", "default.active"))
        assert active == set()

    @pytest.mark.asyncio
    async def test_stream_termination_contract_is_preserved(self, broker: RedisBroker) -> None:
        """Consumers stop on «no events for ~1s + terminal status».

        subscribe_chunks only treats done/failed/cancelled as terminal, so the cancelled
        task must be readable with that status and its already-published chunks drainable.
        """
        adapter = SlowAdapter()
        worker = _make_worker(broker, adapter)
        task = await _dequeued_task(broker)

        running = asyncio.create_task(worker._process_task(task))
        await asyncio.wait_for(adapter.started.wait(), timeout=2.0)
        await AgentClient(broker=broker).cancel(task.id)
        await asyncio.wait_for(running, timeout=5.0)

        stored = await broker.get_task(task.id)
        assert stored is not None
        assert stored.status in ("done", "failed", "cancelled")
        assert stored.status == TaskStatus.CANCELLED

        # The chunk published before the kill is still readable by a late subscriber.
        events = []
        async for event in broker.subscribe_chunks(task.id):
            events.append(event)
        assert [e.text for e in events] == ["working"]

    @pytest.mark.asyncio
    async def test_uncancelled_run_completes_normally(self, broker: RedisBroker) -> None:
        """The watcher must not disturb the happy path."""
        adapter = SlowAdapter(runtime=0.1)
        worker = _make_worker(broker, adapter)
        task = await _dequeued_task(broker)

        await asyncio.wait_for(worker._process_task(task), timeout=5.0)

        assert adapter.cancelled == []
        stored = await broker.get_task(task.id)
        assert stored is not None
        assert stored.status == TaskStatus.DONE
        assert stored.result == "finished on its own"
        assert await broker.list_dlq("default") == []

    @pytest.mark.asyncio
    async def test_cancel_after_completion_does_not_terminate_anything(self, broker: RedisBroker) -> None:
        """A cancel landing after the run finished leaves the real outcome intact."""
        adapter = SlowAdapter(runtime=0.05)
        worker = _make_worker(broker, adapter)
        task = await _dequeued_task(broker)

        await asyncio.wait_for(worker._process_task(task), timeout=5.0)
        await AgentClient(broker=broker).cancel(task.id)

        assert adapter.cancelled == []

    @pytest.mark.asyncio
    async def test_broker_error_during_poll_does_not_kill_the_run(self, broker: RedisBroker) -> None:
        """A transient get_task failure must not be read as a cancellation."""
        adapter = SlowAdapter(runtime=0.6)
        worker = _make_worker(broker, adapter)
        task = await _dequeued_task(broker)

        calls = {"n": 0}
        real_get_task = broker.get_task

        async def flaky_get_task(task_id: str):  # type: ignore[no-untyped-def]
            calls["n"] += 1
            if calls["n"] <= 3:
                raise ConnectionError("redis blip")
            return await real_get_task(task_id)

        broker.get_task = flaky_get_task  # type: ignore[method-assign]
        try:
            await asyncio.wait_for(worker._process_task(task), timeout=5.0)
        finally:
            broker.get_task = real_get_task  # type: ignore[method-assign]

        assert calls["n"] > 3  # the watcher kept polling through the errors
        assert adapter.cancelled == []
        stored = await real_get_task(task.id)
        assert stored is not None
        assert stored.status == TaskStatus.DONE
