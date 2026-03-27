"""Integration tests for RedisBroker using fakeredis."""

import pytest
import pytest_asyncio
from fakeredis import aioredis as fake_aioredis

from open_kknaks.broker.redis import RedisBroker
from open_kknaks.task import Priority, StreamEvent, Task, TaskStatus


@pytest_asyncio.fixture
async def broker():
    """Create a RedisBroker backed by fakeredis."""
    server = fake_aioredis.FakeServer()
    redis = fake_aioredis.FakeRedis(server=server)
    b = RedisBroker(redis=redis, namespace="test")
    await b.connect()
    yield b
    await b.close()


class TestEnqueueDequeue:
    @pytest.mark.asyncio
    async def test_enqueue_and_dequeue(self, broker: RedisBroker) -> None:
        task = Task(prompt="hello", queue="default")
        await broker.enqueue(task)

        result = await broker.dequeue(["default"], timeout=0)
        assert result is not None
        assert result.prompt == "hello"
        assert result.id == task.id

    @pytest.mark.asyncio
    async def test_dequeue_empty_returns_none(self, broker: RedisBroker) -> None:
        result = await broker.dequeue(["default"], timeout=0)
        assert result is None

    @pytest.mark.asyncio
    async def test_priority_ordering(self, broker: RedisBroker) -> None:
        low = Task(prompt="low", queue="q", priority=Priority.LOW)
        normal = Task(prompt="normal", queue="q", priority=Priority.NORMAL)
        high = Task(prompt="high", queue="q", priority=Priority.HIGH)

        # Enqueue in wrong order
        await broker.enqueue(low)
        await broker.enqueue(normal)
        await broker.enqueue(high)

        r1 = await broker.dequeue(["q"], timeout=0)
        r2 = await broker.dequeue(["q"], timeout=0)
        r3 = await broker.dequeue(["q"], timeout=0)

        assert r1 is not None and r1.prompt == "high"
        assert r2 is not None and r2.prompt == "normal"
        assert r3 is not None and r3.prompt == "low"

    @pytest.mark.asyncio
    async def test_multiple_queues(self, broker: RedisBroker) -> None:
        t1 = Task(prompt="q1 task", queue="q1")
        t2 = Task(prompt="q2 task", queue="q2")
        await broker.enqueue(t1)
        await broker.enqueue(t2)

        # Dequeue from q1 first
        result = await broker.dequeue(["q1", "q2"], timeout=0)
        assert result is not None
        assert result.prompt == "q1 task"


class TestAckNack:
    @pytest.mark.asyncio
    async def test_ack_removes_from_active(self, broker: RedisBroker) -> None:
        task = Task(prompt="test", queue="default")
        await broker.enqueue(task)
        await broker.dequeue(["default"], timeout=0)

        # After dequeue, task should be in active set
        active_key = broker._key("queue", "default.active")
        members = await broker.redis.smembers(active_key)
        assert len(members) == 1

        # Ack should remove from active
        await broker.ack("default", task.id)
        members = await broker.redis.smembers(active_key)
        assert len(members) == 0

    @pytest.mark.asyncio
    async def test_nack_moves_to_dlq(self, broker: RedisBroker) -> None:
        task = Task(prompt="test", queue="default")
        await broker.enqueue(task)
        await broker.dequeue(["default"], timeout=0)
        await broker.nack("default", task.id)

        dlq = await broker.list_dlq("default")
        assert len(dlq) == 1
        assert dlq[0].id == task.id


class TestTaskState:
    @pytest.mark.asyncio
    async def test_get_task(self, broker: RedisBroker) -> None:
        task = Task(prompt="hello", queue="default")
        await broker.enqueue(task)

        fetched = await broker.get_task(task.id)
        assert fetched is not None
        assert fetched.prompt == "hello"

    @pytest.mark.asyncio
    async def test_get_task_not_found(self, broker: RedisBroker) -> None:
        result = await broker.get_task("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_update_task(self, broker: RedisBroker) -> None:
        task = Task(prompt="hello", queue="default")
        await broker.enqueue(task)

        task.status = TaskStatus.DONE
        task.result = "completed"
        await broker.update_task(task)

        fetched = await broker.get_task(task.id)
        assert fetched is not None
        assert fetched.status == "done"
        assert fetched.result == "completed"


class TestDLQ:
    @pytest.mark.asyncio
    async def test_list_dlq_empty(self, broker: RedisBroker) -> None:
        dlq = await broker.list_dlq("default")
        assert dlq == []

    @pytest.mark.asyncio
    async def test_retry_from_dlq(self, broker: RedisBroker) -> None:
        task = Task(prompt="retry me", queue="default")
        await broker.enqueue(task)
        await broker.dequeue(["default"], timeout=0)
        await broker.nack("default", task.id)

        await broker.retry_from_dlq("default", task.id)

        # Should be back in main queue
        result = await broker.dequeue(["default"], timeout=0)
        assert result is not None
        assert result.prompt == "retry me"
        assert result.retry_count == 1

    @pytest.mark.asyncio
    async def test_purge_dlq(self, broker: RedisBroker) -> None:
        task = Task(prompt="purge me", queue="default")
        await broker.enqueue(task)
        await broker.dequeue(["default"], timeout=0)
        await broker.nack("default", task.id)

        await broker.purge_dlq("default")
        dlq = await broker.list_dlq("default")
        assert dlq == []


class TestQueueSize:
    @pytest.mark.asyncio
    async def test_queue_size(self, broker: RedisBroker) -> None:
        assert await broker.queue_size("default") == 0

        await broker.enqueue(Task(prompt="a", queue="default"))
        await broker.enqueue(Task(prompt="b", queue="default"))
        assert await broker.queue_size("default") == 2

        await broker.dequeue(["default"], timeout=0)
        assert await broker.queue_size("default") == 1


class TestCosts:
    @pytest.mark.asyncio
    async def test_incr_and_get_total_cost(self, broker: RedisBroker) -> None:
        assert await broker.get_total_cost() == 0.0
        await broker.incr_cost(0.05)
        await broker.incr_cost(0.10)
        total = await broker.get_total_cost()
        assert abs(total - 0.15) < 0.001

    @pytest.mark.asyncio
    async def test_worker_cost(self, broker: RedisBroker) -> None:
        await broker.incr_cost(0.05, worker_id="w1")
        await broker.incr_cost(0.10, worker_id="w1")
        cost = await broker.get_worker_cost("w1")
        assert abs(cost - 0.15) < 0.001

    @pytest.mark.asyncio
    async def test_worker_cost_not_found(self, broker: RedisBroker) -> None:
        assert await broker.get_worker_cost("nonexistent") == 0.0


class TestWorkerManagement:
    @pytest.mark.asyncio
    async def test_register_and_heartbeat(self, broker: RedisBroker) -> None:
        await broker.register_worker("w1", ["default", "high"])
        await broker.heartbeat("w1")
        # No assertion needed — just verifying no errors

    @pytest.mark.asyncio
    async def test_deregister_worker(self, broker: RedisBroker) -> None:
        await broker.register_worker("w1", ["default"])
        await broker.deregister_worker("w1")

        raw = await broker.redis.hget(broker._key("workers"), "w1")
        assert raw is None

    @pytest.mark.asyncio
    async def test_deregister_nonexistent_worker(self, broker: RedisBroker) -> None:
        # Should not raise
        await broker.deregister_worker("nonexistent")

    @pytest.mark.asyncio
    async def test_reap_stale_workers_no_stale(self, broker: RedisBroker) -> None:
        await broker.register_worker("w1", ["default"])
        reaped = await broker.reap_stale_workers(timeout=60.0)
        assert reaped == []

    @pytest.mark.asyncio
    async def test_reap_stale_workers_requeues_active_tasks(self, broker: RedisBroker) -> None:
        import json
        import time

        # Register a worker with old heartbeat
        info = {
            "queues": ["default"],
            "last_heartbeat": time.time() - 120.0,  # 2 minutes ago
        }
        await broker.redis.hset(broker._key("workers"), "dead-worker", json.dumps(info))

        # Simulate tasks stuck in active set
        t1 = Task(prompt="stuck-1", queue="default")
        t2 = Task(prompt="stuck-2", queue="default")
        await broker.enqueue(t1)
        await broker.enqueue(t2)
        await broker.dequeue(["default"], timeout=0)
        await broker.dequeue(["default"], timeout=0)

        # Queue empty, 2 tasks in active
        assert await broker.queue_size("default") == 0
        active_key = broker._key("queue", "default.active")
        assert await broker.redis.scard(active_key) == 2

        # Reap stale workers
        reaped = await broker.reap_stale_workers(timeout=60.0)
        assert "dead-worker" in reaped

        # Worker removed from registry
        raw = await broker.redis.hget(broker._key("workers"), "dead-worker")
        assert raw is None

        # Tasks requeued back to main queue
        assert await broker.queue_size("default") == 2

    @pytest.mark.asyncio
    async def test_reap_stale_workers_multi_queue(self, broker: RedisBroker) -> None:
        import json
        import time

        # Worker subscribed to two queues
        info = {
            "queues": ["q1", "q2"],
            "last_heartbeat": time.time() - 120.0,
        }
        await broker.redis.hset(broker._key("workers"), "dead-worker", json.dumps(info))

        # Tasks stuck in each queue's active set
        t1 = Task(prompt="q1-stuck", queue="q1")
        t2 = Task(prompt="q2-stuck", queue="q2")
        await broker.enqueue(t1)
        await broker.enqueue(t2)
        await broker.dequeue(["q1"], timeout=0)
        await broker.dequeue(["q2"], timeout=0)

        reaped = await broker.reap_stale_workers(timeout=60.0)
        assert "dead-worker" in reaped

        # Both queues should have tasks back
        assert await broker.queue_size("q1") == 1
        assert await broker.queue_size("q2") == 1


class TestStreaming:
    @pytest.mark.asyncio
    async def test_publish_chunk(self, broker: RedisBroker) -> None:
        chunk = StreamEvent(type="text", text="hello")
        await broker.publish_chunk("task-1", chunk)

        # Verify stream entry exists
        stream_key = broker._key("stream", "task-1")
        entries = await broker.redis.xrange(stream_key)
        assert len(entries) == 1


class TestRequeue:
    @pytest.mark.asyncio
    async def test_requeue_returns_to_queue(self, broker: RedisBroker) -> None:
        task = Task(prompt="requeue me", queue="default")
        await broker.enqueue(task)
        await broker.dequeue(["default"], timeout=0)

        # Queue should be empty now
        assert await broker.queue_size("default") == 0

        await broker.requeue("default", [task.id])

        # Task should be back in queue
        assert await broker.queue_size("default") == 1
        result = await broker.dequeue(["default"], timeout=0)
        assert result is not None
        assert result.prompt == "requeue me"


class TestDelayedEnqueue:
    @pytest.mark.asyncio
    async def test_delayed_task_not_immediately_available(self, broker: RedisBroker) -> None:
        task = Task(prompt="delayed", queue="default")
        await broker.enqueue(task, delay=3600)  # 1 hour delay

        # Should not be in main queue
        result = await broker.dequeue(["default"], timeout=0)
        assert result is None

    @pytest.mark.asyncio
    async def test_promote_delayed(self, broker: RedisBroker) -> None:
        task = Task(prompt="promote me", queue="default")
        # delay=-1 means delay_until is in the past (already due)
        await broker.enqueue(task, delay=-1)

        promoted = await broker.promote_delayed("default")
        assert promoted == 1

        # Should now be dequeue-able from main queue
        result = await broker.dequeue(["default"], timeout=0)
        assert result is not None
        assert result.prompt == "promote me"
