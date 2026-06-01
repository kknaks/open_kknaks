"""Tests for Typer CLI surfaces."""

import re

from typer.testing import CliRunner

from open_kknaks.cli.main import app
from open_kknaks.cli.task_cmd import task_app
from open_kknaks.task import Task


class FakeBroker:
    def __init__(self, *args, **kwargs) -> None:
        self.task = Task(prompt="status", provider="codex", model="gpt-5.4")

    async def connect(self) -> None:
        pass

    async def close(self) -> None:
        pass

    async def get_task(self, task_id: str) -> Task | None:
        return self.task if task_id == "task-1" else None


def strip_ansi(value: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", value)


def test_worker_run_has_provider_option() -> None:
    result = CliRunner().invoke(app, ["worker", "run", "--help"])
    assert result.exit_code == 0
    assert "--provider" in strip_ansi(result.output)


def test_worker_run_rejects_unknown_provider() -> None:
    result = CliRunner().invoke(app, ["worker", "run", "--provider", "bad"])
    assert result.exit_code != 0
    assert "Unsupported provider" in result.output


def test_task_status_prints_provider_and_model(monkeypatch) -> None:
    import open_kknaks.broker.redis

    monkeypatch.setattr(open_kknaks.broker.redis, "RedisBroker", FakeBroker)
    result = CliRunner().invoke(task_app, ["status", "task-1"])

    assert result.exit_code == 0
    assert "Provider: codex" in result.output
    assert "Model: gpt-5.4" in result.output
