"""Integration tests for ClaudeClient."""

import pytest
import pytest_asyncio
from fakeredis import aioredis as fake_aioredis

from open_kknaks.broker.redis import RedisBroker
from open_kknaks.client import ClaudeClient
from open_kknaks.task import Priority, TaskStatus


@pytest_asyncio.fixture
async def broker():
    server = fake_aioredis.FakeServer()
    redis = fake_aioredis.FakeRedis(server=server)
    b = RedisBroker(redis=redis, namespace="test")
    await b.connect()
    yield b
    await b.close()


@pytest.fixture
def client(broker: RedisBroker) -> ClaudeClient:
    return ClaudeClient(broker=broker)


class TestSubmit:
    @pytest.mark.asyncio
    async def test_submit_returns_task_id(self, client: ClaudeClient) -> None:
        task_id = await client.submit("hello world")
        assert isinstance(task_id, str)
        assert len(task_id) > 0

    @pytest.mark.asyncio
    async def test_submit_creates_task_in_broker(
        self, client: ClaudeClient, broker: RedisBroker,
    ) -> None:
        task_id = await client.submit("test prompt", queue="myqueue")
        task = await broker.get_task(task_id)
        assert task is not None
        assert task.prompt == "test prompt"
        assert task.queue == "myqueue"

    @pytest.mark.asyncio
    async def test_submit_with_priority(
        self, client: ClaudeClient, broker: RedisBroker,
    ) -> None:
        task_id = await client.submit("high priority", priority=Priority.HIGH)
        task = await broker.get_task(task_id)
        assert task is not None
        assert task.priority == 1

    @pytest.mark.asyncio
    async def test_submit_with_overrides(
        self, client: ClaudeClient, broker: RedisBroker,
    ) -> None:
        task_id = await client.submit(
            "test",
            model="opus",
            effort="high",
            max_turns=5,
            metadata={"key": "value"},
        )
        task = await broker.get_task(task_id)
        assert task is not None
        assert task.model == "opus"
        assert task.effort == "high"
        assert task.max_turns == 5
        assert task.metadata == {"key": "value"}

    @pytest.mark.asyncio
    async def test_submit_with_delay(
        self, client: ClaudeClient, broker: RedisBroker,
    ) -> None:
        await client.submit("delayed", delay_seconds=60)
        # Task should be in delayed queue, not main
        size = await broker.queue_size("default")
        assert size == 0  # Not in main queue yet


class TestStatus:
    @pytest.mark.asyncio
    async def test_status_pending(self, client: ClaudeClient) -> None:
        task_id = await client.submit("test")
        status = await client.status(task_id)
        assert status == "pending"

    @pytest.mark.asyncio
    async def test_status_not_found(self, client: ClaudeClient) -> None:
        status = await client.status("nonexistent")
        assert status is None


class TestResult:
    @pytest.mark.asyncio
    async def test_result_already_done(
        self, client: ClaudeClient, broker: RedisBroker,
    ) -> None:
        task_id = await client.submit("test")
        # Manually mark as done
        task = await broker.get_task(task_id)
        assert task is not None
        task.status = TaskStatus.DONE
        task.result = "completed output"
        await broker.update_task(task)

        result = await client.result(task_id, timeout=1.0)
        assert result is not None
        assert result.result == "completed output"
        assert result.status == "done"

    @pytest.mark.asyncio
    async def test_result_not_found(self, client: ClaudeClient) -> None:
        result = await client.result("nonexistent", timeout=0.1)
        assert result is None


class TestCancel:
    @pytest.mark.asyncio
    async def test_cancel_existing(self, client: ClaudeClient, broker: RedisBroker) -> None:
        task_id = await client.submit("cancel me")
        success = await client.cancel(task_id)
        assert success

        task = await broker.get_task(task_id)
        assert task is not None
        assert task.status == "cancelled"

    @pytest.mark.asyncio
    async def test_cancel_not_found(self, client: ClaudeClient) -> None:
        success = await client.cancel("nonexistent")
        assert not success


class TestBatchIntegration:
    @pytest.mark.asyncio
    async def test_batch_submit_and_status(self, broker: RedisBroker) -> None:
        from open_kknaks.batch import BatchRunner

        runner = BatchRunner(broker=broker)
        batch_id, task_ids = await runner.submit_batch(
            [{"prompt": "task 1"}, {"prompt": "task 2"}, {"prompt": "task 3"}],
            queue="default",
        )
        assert isinstance(batch_id, str)
        assert len(task_ids) == 3

        # All tasks should be in queue
        size = await broker.queue_size("default")
        assert size == 3
