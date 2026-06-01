"""Integration tests for ClaudeWorker — config merge and middleware wiring."""

import pytest

from open_kknaks.config import ClaudeConfig
from open_kknaks.task import Task
from open_kknaks.worker.worker import ClaudeWorker


class FakeBroker:
    """Minimal broker stub for unit-testing Worker._merge_config."""

    def __init__(self) -> None:
        self.updated: list[Task] = []
        self.acked: list[tuple[str, str]] = []
        self.nacked: list[tuple[str, str]] = []

    async def update_task(self, task: Task) -> None:
        self.updated.append(task.model_copy(deep=True))

    async def ack(self, queue_name: str, task_id: str) -> None:
        self.acked.append((queue_name, task_id))

    async def nack(self, queue_name: str, task_id: str) -> None:
        self.nacked.append((queue_name, task_id))


class TestWorkerMergeConfig:
    def _make_worker(self, config: ClaudeConfig | None = None) -> ClaudeWorker:
        # We pass a stub broker; _merge_config doesn't use it
        return ClaudeWorker(broker=FakeBroker(), config=config)  # type: ignore[arg-type]

    def test_default_config(self) -> None:
        worker = self._make_worker()
        task = Task(prompt="test")
        merged = worker._merge_config(task)
        assert merged.work_dir == "."
        assert merged.model is None

    def test_task_overrides_model(self) -> None:
        worker = self._make_worker(ClaudeConfig(model="sonnet"))
        task = Task(prompt="test", model="opus")
        merged = worker._merge_config(task)
        assert merged.model == "opus"

    def test_task_none_keeps_worker_default(self) -> None:
        worker = self._make_worker(ClaudeConfig(model="sonnet"))
        task = Task(prompt="test")
        merged = worker._merge_config(task)
        assert merged.model == "sonnet"

    def test_work_dir_not_overridable(self) -> None:
        worker = self._make_worker(ClaudeConfig(work_dir="/safe"))
        task = Task(prompt="test")
        # Even if task somehow had work_dir, it can't override
        merged = worker._merge_config(task)
        assert merged.work_dir == "/safe"

    def test_multiple_overrides(self) -> None:
        worker = self._make_worker(ClaudeConfig())
        task = Task(
            prompt="test",
            model="opus",
            provider_options={
                "effort": "high",
                "max_turns": 5,
                "allowed_tools": ["bash"],
            },
        )
        merged = worker._merge_config(task)
        assert merged.model == "opus"
        assert merged.effort == "high"
        assert merged.max_turns == 5
        assert merged.allowed_tools == ["bash"]

    def test_provider_options_override_legacy_fields(self) -> None:
        worker = self._make_worker(ClaudeConfig())
        task = Task(
            prompt="test",
            model="opus",
            provider_options={
                "effort": "high",
                "max_turns": 7,
                "allowed_tools": ["Read"],
            },
        )
        merged = worker._merge_config(task)
        assert merged.model == "opus"
        assert merged.effort == "high"
        assert merged.max_turns == 7
        assert merged.allowed_tools == ["Read"]

    def test_common_options_map_to_runtime_config(self) -> None:
        worker = self._make_worker(ClaudeConfig(work_dir="/safe"))
        task = Task(
            prompt="test",
            options={
                "cwd": "/repo",
                "timeout_sec": 123,
                "resume": {"mode": "session", "session_id": "sess-123"},
            },
        )
        merged = worker._merge_config(task)
        assert merged.work_dir == "/repo"
        assert task.options["timeout_sec"] == 123
        assert task.options["resume"] == {"mode": "session", "session_id": "sess-123"}

    def test_config_unchanged_after_merge(self) -> None:
        config = ClaudeConfig(model="sonnet")
        worker = self._make_worker(config)
        task = Task(prompt="test", model="opus")
        worker._merge_config(task)
        assert config.model == "sonnet"  # Original unchanged


class TestWorkerInit:
    def test_default_values(self) -> None:
        worker = ClaudeWorker(broker=FakeBroker())  # type: ignore[arg-type]
        assert worker.concurrency == 4
        assert worker.queues == ["default"]
        assert worker.shutdown_timeout == 30.0
        assert worker.worker_id.startswith("worker-")

    def test_custom_values(self) -> None:
        worker = ClaudeWorker(
            broker=FakeBroker(),  # type: ignore[arg-type]
            queues=["high", "default"],
            concurrency=8,
            shutdown_timeout=60.0,
        )
        assert worker.concurrency == 8
        assert worker.queues == ["high", "default"]
        assert worker.shutdown_timeout == 60.0

    def test_registers_codex_adapter(self) -> None:
        worker = ClaudeWorker(broker=FakeBroker())  # type: ignore[arg-type]
        assert "codex" in worker._adapters

    def test_provider_status_merges_all_adapters(self) -> None:
        class FakeAdapter:
            def __init__(self, status: dict[str, str]) -> None:
                self.status = status

            def health_check(self) -> dict[str, str]:
                return self.status

        worker = ClaudeWorker(broker=FakeBroker())  # type: ignore[arg-type]
        worker._adapters = {  # type: ignore[assignment]
            "claude": FakeAdapter({"claude": "ok", "claude_version": "1.0"}),
            "codex": FakeAdapter({"codex": "ok", "codex_version": "2.0"}),
        }

        assert worker._check_provider_status() == {
            "claude": "ok",
            "claude_version": "1.0",
            "codex": "ok",
            "codex_version": "2.0",
        }


class TestWorkerProviderSelection:
    @pytest.mark.asyncio
    async def test_unknown_provider_fails_without_execution(self) -> None:
        broker = FakeBroker()
        worker = ClaudeWorker(broker=broker)  # type: ignore[arg-type]
        task = Task(prompt="test", provider="unknown")

        await worker._process_task(task)

        assert task.status == "failed"
        assert task.error == "Unsupported provider: unknown"
        assert broker.nacked == [("default", task.id)]
        assert broker.acked == []

    @pytest.mark.asyncio
    async def test_cancelled_task_is_acked_without_execution(self) -> None:
        broker = FakeBroker()
        worker = ClaudeWorker(broker=broker)  # type: ignore[arg-type]
        task = Task(prompt="test", status="cancelled")

        await worker._process_task(task)

        assert task.status == "cancelled"
        assert broker.acked == [("default", task.id)]
        assert broker.nacked == []
