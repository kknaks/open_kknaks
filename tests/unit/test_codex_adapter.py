"""Tests for CodexRunnerAdapter."""

import asyncio

import pytest

from open_kknaks.task import Task
from open_kknaks.worker.codex_adapter import CodexRunnerAdapter


class FakeProcess:
    def __init__(self, stdout: bytes, stderr: bytes = b"", returncode: int = 0) -> None:
        self.stdout = asyncio.StreamReader()
        self.stdout.feed_data(stdout)
        self.stdout.feed_eof()
        self.stderr = asyncio.StreamReader()
        self.stderr.feed_data(stderr)
        self.stderr.feed_eof()
        self.returncode = returncode
        self.terminated = False

    async def wait(self) -> int:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True


class TestCodexCommand:
    def test_new_session_command(self) -> None:
        adapter = CodexRunnerAdapter(codex_bin="codex")
        task = Task(prompt="hello", provider="codex", model="gpt-5.4")
        cmd = adapter._build_command(task)
        assert cmd == [
            "codex",
            "exec",
            "--json",
            "--model",
            "gpt-5.4",
            "--sandbox",
            "workspace-write",
            "hello",
        ]

    def test_resume_session_command(self) -> None:
        adapter = CodexRunnerAdapter()
        task = Task(
            prompt="continue",
            provider="codex",
            options={"resume": {"mode": "session", "session_id": "thread-1"}},
        )
        cmd = adapter._build_command(task)
        assert cmd[:5] == ["codex", "exec", "resume", "thread-1", "--json"]
        assert cmd[-1] == "continue"

    def test_resume_last_command(self) -> None:
        adapter = CodexRunnerAdapter()
        task = Task(prompt="continue", provider="codex", options={"resume": {"mode": "last"}})
        cmd = adapter._build_command(task)
        assert cmd[:5] == ["codex", "exec", "resume", "--last", "--json"]

    def test_provider_options_mapping(self) -> None:
        adapter = CodexRunnerAdapter()
        task = Task(
            prompt="run",
            provider="codex",
            provider_options={
                "sandbox": "read-only",
                "color": "never",
                "skip_git_repo_check": True,
                "add_dirs": ["/tmp/a", "/tmp/b"],
                "config": ["model_reasoning_effort=high"],
            },
        )
        cmd = adapter._build_command(task)
        assert "--sandbox" in cmd
        assert cmd[cmd.index("--sandbox") + 1] == "read-only"
        assert "--color" in cmd
        assert "--skip-git-repo-check" in cmd
        assert cmd.count("--add-dir") == 2
        assert cmd.count("--config") == 1

    @pytest.mark.parametrize(
        ("task", "message"),
        [
            (Task(prompt="x", provider="codex", provider_options={"bad": True}), "Unsupported codex provider_options"),
            (
                Task(prompt="x", provider="codex", options={"resume": {"mode": "session"}}),
                "session_id is required",
            ),
            (
                Task(prompt="x", provider="codex", provider_options={"json": False}),
                "json=false is not supported",
            ),
            (
                Task(
                    prompt="x",
                    provider="codex",
                    options={"resume": {"mode": "last"}},
                    provider_options={"ephemeral": True},
                ),
                "ephemeral=true cannot be used with resume",
            ),
        ],
    )
    def test_validation_errors(self, task: Task, message: str) -> None:
        adapter = CodexRunnerAdapter()
        assert message in (adapter._validate_options(task) or "")


class TestCodexEventParsing:
    def test_thread_started(self) -> None:
        adapter = CodexRunnerAdapter()
        events, result_text, usage, session_id = adapter._parse_json_event(
            {"type": "thread.started", "thread_id": "thread-1"}
        )
        assert events[0].type == "init"
        assert events[0].session_id == "thread-1"
        assert result_text is None
        assert usage is None
        assert session_id == "thread-1"

    def test_agent_message(self) -> None:
        adapter = CodexRunnerAdapter()
        events, result_text, usage, session_id = adapter._parse_json_event(
            {
                "type": "item.completed",
                "item": {"details": {"type": "agent_message", "text": "done"}},
            }
        )
        assert events[0].type == "text"
        assert events[0].text == "done"
        assert result_text == "done"
        assert usage is None
        assert session_id is None

    def test_agent_message_without_details(self) -> None:
        adapter = CodexRunnerAdapter()
        events, result_text, usage, session_id = adapter._parse_json_event(
            {
                "type": "item.completed",
                "item": {"id": "item_0", "type": "agent_message", "text": "OK"},
            }
        )
        assert events[0].type == "text"
        assert events[0].text == "OK"
        assert result_text == "OK"
        assert usage is None
        assert session_id is None

    def test_turn_completed_usage(self) -> None:
        adapter = CodexRunnerAdapter()
        events, result_text, usage, session_id = adapter._parse_json_event(
            {"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 5}}
        )
        assert events[0].type == "cost"
        assert result_text is None
        assert usage is not None
        assert usage.input_tokens == 10
        assert usage.output_tokens == 5
        assert session_id is None

    def test_command_execution_maps_to_tool_events(self) -> None:
        adapter = CodexRunnerAdapter()
        started, *_ = adapter._parse_json_event(
            {
                "type": "item.started",
                "item": {"details": {"type": "command_execution", "command": "ls"}},
            }
        )
        completed, *_ = adapter._parse_json_event(
            {
                "type": "item.completed",
                "item": {"details": {"type": "command_execution", "command": "ls", "output": "ok"}},
            }
        )
        assert started[0].type == "tool_use"
        assert started[0].tool_name == "ls"
        assert completed[0].type == "tool_result"
        assert completed[0].tool_result == "ok"


class TestCodexExecute:
    @pytest.mark.asyncio
    async def test_execute_parses_stdout_jsonl(self, monkeypatch: pytest.MonkeyPatch) -> None:
        payload = (
            b'{"type":"thread.started","thread_id":"thread-1"}\n'
            b'{"type":"item.completed","item":{"type":"agent_message","text":"done"}}\n'
            b'{"type":"turn.completed","usage":{"input_tokens":10,"output_tokens":5}}\n'
        )
        process = FakeProcess(payload)

        async def fake_create_subprocess_exec(*_args: str, **_kwargs: object) -> FakeProcess:
            return process

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
        adapter = CodexRunnerAdapter()
        events = []

        async def on_chunk(event):
            events.append(event)

        result = await adapter.execute(Task(prompt="run", provider="codex"), config=None, on_chunk=on_chunk)  # type: ignore[arg-type]

        assert result.exit_code == 0
        assert result.result == "done"
        assert result.stream == "done"
        assert result.session_id == "thread-1"
        assert result.usage is not None
        assert result.usage.input_tokens == 10
        assert [event.type for event in events] == ["init", "text", "cost"]

    @pytest.mark.asyncio
    async def test_execute_preserves_stderr_and_non_json_debug_context(self, monkeypatch: pytest.MonkeyPatch) -> None:
        process = FakeProcess(b"not-json\n", stderr=b"codex auth failed\n", returncode=1)

        async def fake_create_subprocess_exec(*_args: str, **_kwargs: object) -> FakeProcess:
            return process

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
        adapter = CodexRunnerAdapter()
        events = []

        async def on_chunk(event):
            events.append(event)

        result = await adapter.execute(Task(prompt="run", provider="codex"), config=None, on_chunk=on_chunk)  # type: ignore[arg-type]

        assert result.exit_code == 1
        assert result.result == ""
        assert result.debug_context is not None
        assert "not-json" in result.debug_context
        assert "codex auth failed" in result.debug_context
        assert events == []
