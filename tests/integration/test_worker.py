"""Integration tests for ClaudeWorker — config merge and middleware wiring."""

from open_kknaks.config import ClaudeConfig
from open_kknaks.task import Task
from open_kknaks.worker.worker import ClaudeWorker


class FakeBroker:
    """Minimal broker stub for unit-testing Worker._merge_config."""


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
            effort="high",
            max_turns=5,
            allowed_tools=["bash"],
        )
        merged = worker._merge_config(task)
        assert merged.model == "opus"
        assert merged.effort == "high"
        assert merged.max_turns == 5
        assert merged.allowed_tools == ["bash"]

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
