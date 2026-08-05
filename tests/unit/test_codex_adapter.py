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

    def test_resume_omits_flags_the_subcommand_rejects(self) -> None:
        """Regression: `codex exec resume` takes a strict subset of `codex exec` flags.

        Emitting any of these made every resume submission die with exit 2
        ("unexpected argument"). Measured against codex-cli 0.146.0.
        """
        adapter = CodexRunnerAdapter()
        task = Task(
            prompt="continue",
            provider="codex",
            model="gpt-5.4",
            options={"resume": {"mode": "session", "session_id": "thread-1"}, "cwd": "/srv/work"},
            provider_options={
                "sandbox": "read-only",
                "color": "never",
                "profile": "p",
                "profile_v2": "p2",
                "local_provider": "lp",
                "oss": True,
                "add_dirs": ["/tmp/a"],
                "skip_git_repo_check": True,
                "config": ["model_reasoning_effort=high"],
            },
        )
        cmd = adapter._build_command(task)

        rejected = ("--sandbox", "--cd", "--add-dir", "--color", "--profile", "--profile-v2", "--local-provider")
        for flag in (*rejected, "--oss"):
            assert flag not in cmd, f"{flag} is rejected by `codex exec resume`"

        # ...while everything resume *does* accept still gets through.
        assert cmd[:5] == ["codex", "exec", "resume", "thread-1", "--json"]
        assert "--skip-git-repo-check" in cmd
        assert "--config" in cmd
        assert cmd[cmd.index("--model") + 1] == "gpt-5.4"
        assert cmd[-1] == "continue"

    def test_resume_last_omits_flags_the_subcommand_rejects(self) -> None:
        adapter = CodexRunnerAdapter()
        task = Task(
            prompt="continue",
            provider="codex",
            options={"resume": {"mode": "last"}, "cwd": "/srv/work"},
        )
        cmd = adapter._build_command(task)
        # The workspace-write default must not leak into resume either.
        assert "--sandbox" not in cmd
        assert "--cd" not in cmd
        assert cmd == ["codex", "exec", "resume", "--last", "--json", "continue"]

    def test_new_session_still_emits_sandbox_cd_and_model(self) -> None:
        """The suppression must be scoped to resume — new sessions are unchanged."""
        adapter = CodexRunnerAdapter()
        task = Task(
            prompt="run",
            provider="codex",
            model="gpt-5.4",
            options={"cwd": "/srv/work"},
            provider_options={"color": "never", "add_dirs": ["/tmp/a"], "oss": True},
        )
        cmd = adapter._build_command(task)
        assert cmd[cmd.index("--sandbox") + 1] == "workspace-write"
        assert cmd[cmd.index("--cd") + 1] == "/srv/work"
        assert cmd[cmd.index("--model") + 1] == "gpt-5.4"
        assert "--color" in cmd
        assert "--add-dir" in cmd
        assert "--oss" in cmd

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


# Verbatim `codex exec --json` output from codex-cli 0.146.0.
REAL_COMMAND_STARTED = {
    "type": "item.started",
    "item": {
        "id": "item_1",
        "type": "command_execution",
        "command": "/bin/zsh -lc 'echo hello-from-codex'",
        "aggregated_output": "",
        "exit_code": None,
        "status": "in_progress",
    },
}
REAL_COMMAND_COMPLETED = {
    "type": "item.completed",
    "item": {
        "id": "item_1",
        "type": "command_execution",
        "command": "/bin/zsh -lc 'echo hello-from-codex'",
        "aggregated_output": "hello-from-codex\n",
        "exit_code": 0,
        "status": "completed",
    },
}


class TestCodexToolUseId:
    """Regression: codex tool events carried tool_use_id=None, so callers could not
    pair a tool call with its result."""

    def test_flat_command_execution_emits_paired_tool_events(self) -> None:
        adapter = CodexRunnerAdapter()
        started, *_ = adapter._parse_json_event(REAL_COMMAND_STARTED)
        completed, *_ = adapter._parse_json_event(REAL_COMMAND_COMPLETED)

        assert started[0].type == "tool_use"
        assert started[0].tool_name == "/bin/zsh -lc 'echo hello-from-codex'"
        assert completed[0].type == "tool_result"
        assert completed[0].tool_result == "hello-from-codex\n"
        # Same item id on both sides — this is what makes them pairable.
        assert started[0].tool_use_id == "item_1"
        assert completed[0].tool_use_id == "item_1"

    def test_exit_code_maps_to_tool_is_error(self) -> None:
        adapter = CodexRunnerAdapter()
        ok, *_ = adapter._parse_json_event(REAL_COMMAND_COMPLETED)
        assert ok[0].tool_is_error is False

        failed_event = {
            "type": "item.completed",
            "item": {
                "id": "item_2",
                "type": "command_execution",
                "command": "false",
                "aggregated_output": "boom",
                "exit_code": 1,
                "status": "failed",
            },
        }
        failed, *_ = adapter._parse_json_event(failed_event)
        assert failed[0].tool_is_error is True
        assert failed[0].tool_use_id == "item_2"

    def test_nested_details_shape_also_carries_tool_use_id(self) -> None:
        adapter = CodexRunnerAdapter()
        started, *_ = adapter._parse_json_event(
            {
                "type": "item.started",
                "item": {"id": "item_7", "details": {"type": "mcp_tool_call", "name": "search"}},
            }
        )
        completed, *_ = adapter._parse_json_event(
            {
                "type": "item.completed",
                "item": {
                    "id": "item_7",
                    "details": {"type": "mcp_tool_call", "name": "search", "output": "hits", "is_error": False},
                },
            }
        )

        assert started[0].type == "tool_use"
        assert started[0].tool_use_id == "item_7"
        assert completed[0].type == "tool_result"
        assert completed[0].tool_result == "hits"
        assert completed[0].tool_is_error is False
        assert completed[0].tool_use_id == "item_7"

    def test_item_updated_does_not_emit_duplicate_tool_result(self) -> None:
        adapter = CodexRunnerAdapter()
        updated, *_ = adapter._parse_json_event(
            {
                "type": "item.updated",
                "item": {
                    "id": "item_1",
                    "type": "command_execution",
                    "command": "sleep 1",
                    "aggregated_output": "partial",
                    "status": "in_progress",
                },
            }
        )
        assert updated[0].type == "progress"
        assert updated[0].tool_use_id == "item_1"

    def test_item_without_id_leaves_tool_use_id_none(self) -> None:
        adapter = CodexRunnerAdapter()
        started, *_ = adapter._parse_json_event(
            {"type": "item.started", "item": {"details": {"type": "command_execution", "command": "ls"}}}
        )
        assert started[0].type == "tool_use"
        assert started[0].tool_use_id is None


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
