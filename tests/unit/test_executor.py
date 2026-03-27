"""Tests for ClaudeCodeExecutor — unit tests (no real Claude CLI)."""

from open_kknaks.config import ClaudeConfig
from open_kknaks.task import Task
from open_kknaks.worker.executor import ClaudeCodeExecutor


class TestBuildCommand:
    def test_minimal(self) -> None:
        executor = ClaudeCodeExecutor()
        task = Task(prompt="hello")
        config = ClaudeConfig()
        cmd = executor._build_command(task, config)
        assert cmd[0] == "claude"
        assert "-p" in cmd
        assert "--output-format" in cmd
        assert "stream-json" in cmd
        assert cmd[-1] == "hello"

    def test_with_model(self) -> None:
        executor = ClaudeCodeExecutor()
        task = Task(prompt="test")
        config = ClaudeConfig(model="opus")
        cmd = executor._build_command(task, config)
        idx = cmd.index("--model")
        assert cmd[idx + 1] == "opus"

    def test_with_system_prompt(self) -> None:
        executor = ClaudeCodeExecutor()
        task = Task(prompt="test")
        config = ClaudeConfig(system_prompt="be helpful")
        cmd = executor._build_command(task, config)
        idx = cmd.index("--system-prompt")
        assert cmd[idx + 1] == "be helpful"

    def test_with_max_turns(self) -> None:
        executor = ClaudeCodeExecutor()
        task = Task(prompt="test")
        config = ClaudeConfig(max_turns=5)
        cmd = executor._build_command(task, config)
        idx = cmd.index("--max-turns")
        assert cmd[idx + 1] == "5"

    def test_with_effort(self) -> None:
        executor = ClaudeCodeExecutor()
        task = Task(prompt="test")
        config = ClaudeConfig(effort="high")
        cmd = executor._build_command(task, config)
        idx = cmd.index("--effort")
        assert cmd[idx + 1] == "high"

    def test_with_allowed_tools(self) -> None:
        executor = ClaudeCodeExecutor()
        task = Task(prompt="test")
        config = ClaudeConfig(allowed_tools=["bash", "read"])
        cmd = executor._build_command(task, config)
        indices = [i for i, x in enumerate(cmd) if x == "--allowedTools"]
        assert len(indices) == 2
        assert cmd[indices[0] + 1] == "bash"
        assert cmd[indices[1] + 1] == "read"

    def test_with_session_id(self) -> None:
        executor = ClaudeCodeExecutor()
        task = Task(prompt="test", session_id="sess-123")
        config = ClaudeConfig()
        cmd = executor._build_command(task, config)
        idx = cmd.index("--resume")
        assert cmd[idx + 1] == "sess-123"

    def test_with_add_dirs(self) -> None:
        executor = ClaudeCodeExecutor()
        task = Task(prompt="test")
        config = ClaudeConfig(add_dirs=["/tmp", "/var"])
        cmd = executor._build_command(task, config)
        indices = [i for i, x in enumerate(cmd) if x == "--add-dir"]
        assert len(indices) == 2

    def test_custom_claude_bin(self) -> None:
        executor = ClaudeCodeExecutor(claude_bin="/usr/local/bin/claude")
        task = Task(prompt="test")
        config = ClaudeConfig()
        cmd = executor._build_command(task, config)
        assert cmd[0] == "/usr/local/bin/claude"

    def test_config_claude_bin_overrides(self) -> None:
        executor = ClaudeCodeExecutor(claude_bin="/usr/local/bin/claude")
        task = Task(prompt="test")
        config = ClaudeConfig(claude_bin="/opt/claude")
        cmd = executor._build_command(task, config)
        assert cmd[0] == "/opt/claude"

    def test_dangerously_skip_permissions(self) -> None:
        executor = ClaudeCodeExecutor()
        task = Task(prompt="test")
        config = ClaudeConfig(permission_mode="dangerously-skip-permissions")
        cmd = executor._build_command(task, config)
        assert "--dangerously-skip-permissions" in cmd

    def test_permission_mode_custom(self) -> None:
        executor = ClaudeCodeExecutor()
        task = Task(prompt="test")
        config = ClaudeConfig(permission_mode="plan")
        cmd = executor._build_command(task, config)
        idx = cmd.index("--permission-mode")
        assert cmd[idx + 1] == "plan"

    def test_permission_mode_default_not_added(self) -> None:
        executor = ClaudeCodeExecutor()
        task = Task(prompt="test")
        config = ClaudeConfig(permission_mode="default")
        cmd = executor._build_command(task, config)
        assert "--permission-mode" not in cmd
        assert "--dangerously-skip-permissions" not in cmd
