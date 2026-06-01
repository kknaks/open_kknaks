"""Integration tests for BatchRunner."""

import pytest
import pytest_asyncio
from fakeredis import aioredis as fake_aioredis

from open_kknaks.batch import BatchRunner, BatchStatus
from open_kknaks.broker.redis import RedisBroker
from open_kknaks.task import TaskStatus


@pytest_asyncio.fixture
async def broker():
    server = fake_aioredis.FakeServer()
    redis = fake_aioredis.FakeRedis(server=server)
    b = RedisBroker(redis=redis, namespace="test")
    await b.connect()
    yield b
    await b.close()


class TestBatchSubmit:
    @pytest.mark.asyncio
    async def test_submit_batch_copies_provider_fields(self, broker: RedisBroker) -> None:
        runner = BatchRunner(broker=broker)
        batch_id, task_ids = await runner.submit_batch(
            [
                {
                    "prompt": "task 1",
                    "context": "ctx",
                    "provider": "codex",
                    "model": "gpt-5.4",
                    "options": {"cwd": "/repo"},
                    "provider_options": {"sandbox": "workspace-write"},
                    "metadata": {"ticket": "OKK-1"},
                }
            ],
            queue="work",
        )

        task = await broker.get_task(task_ids[0])
        assert task is not None
        assert task.batch_id == batch_id
        assert task.queue == "work"
        assert task.context == "ctx"
        assert task.provider == "codex"
        assert task.model == "gpt-5.4"
        assert task.options == {"cwd": "/repo"}
        assert task.provider_options == {"sandbox": "workspace-write"}
        assert task.metadata == {"ticket": "OKK-1"}

    @pytest.mark.asyncio
    async def test_submit_batch_defaults_provider(self, broker: RedisBroker) -> None:
        runner = BatchRunner(broker=broker)
        _, task_ids = await runner.submit_batch([{"prompt": "task 1"}])

        task = await broker.get_task(task_ids[0])
        assert task is not None
        assert task.provider == "claude"


class TestBatchStatus:
    @pytest.mark.asyncio
    async def test_running_only_batch_is_running(self, broker: RedisBroker) -> None:
        runner = BatchRunner(broker=broker)
        batch_id, task_ids = await runner.submit_batch([{"prompt": "task 1"}])
        task = await broker.get_task(task_ids[0])
        assert task is not None
        task.status = TaskStatus.RUNNING
        await broker.update_task(task)

        assert await runner.get_batch_status(batch_id, task_ids) == BatchStatus.RUNNING

    @pytest.mark.asyncio
    async def test_partial_failure_when_all_terminal_mixed(self, broker: RedisBroker) -> None:
        runner = BatchRunner(broker=broker)
        batch_id, task_ids = await runner.submit_batch([{"prompt": "done"}, {"prompt": "failed"}])
        first = await broker.get_task(task_ids[0])
        second = await broker.get_task(task_ids[1])
        assert first is not None
        assert second is not None
        first.status = TaskStatus.DONE
        second.status = TaskStatus.FAILED
        await broker.update_task(first)
        await broker.update_task(second)

        assert await runner.get_batch_status(batch_id, task_ids) == BatchStatus.PARTIAL_FAILURE


class TestBatchWait:
    @pytest.mark.asyncio
    async def test_timeout_returns_terminal_tasks_only(self, broker: RedisBroker) -> None:
        runner = BatchRunner(broker=broker)
        _, task_ids = await runner.submit_batch([{"prompt": "done"}, {"prompt": "pending"}])
        done = await broker.get_task(task_ids[0])
        assert done is not None
        done.status = TaskStatus.DONE
        await broker.update_task(done)

        results = await runner.wait_batch(task_ids, timeout=0.01, poll_interval=0.01)

        assert [task.id for task in results] == [task_ids[0]]
