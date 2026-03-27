"""Tests for ClaudeConfig."""

from open_kknaks.config import OVERRIDABLE_FIELDS, ClaudeConfig


class TestClaudeConfig:
    def test_defaults(self) -> None:
        config = ClaudeConfig()
        assert config.work_dir == "."
        assert config.claude_bin is None
        assert config.model is None
        assert config.permission_mode == "default"

    def test_custom_values(self) -> None:
        config = ClaudeConfig(work_dir="/app", model="opus", max_turns=10)
        assert config.work_dir == "/app"
        assert config.model == "opus"
        assert config.max_turns == 10

    def test_json_roundtrip(self) -> None:
        config = ClaudeConfig(model="sonnet", effort="high", allowed_tools=["bash"])
        json_str = config.model_dump_json()
        restored = ClaudeConfig.model_validate_json(json_str)
        assert restored.model == "sonnet"
        assert restored.effort == "high"
        assert restored.allowed_tools == ["bash"]


class TestMergeTaskOverrides:
    def test_override_allowed_field(self) -> None:
        base = ClaudeConfig(model="sonnet", work_dir="/app")
        merged = base.merge_task_overrides({"model": "opus"})
        assert merged.model == "opus"
        assert merged.work_dir == "/app"  # unchanged

    def test_blocked_work_dir(self) -> None:
        base = ClaudeConfig(work_dir="/safe")
        merged = base.merge_task_overrides({"work_dir": "/evil"})
        assert merged.work_dir == "/safe"

    def test_blocked_claude_bin(self) -> None:
        base = ClaudeConfig(claude_bin="/usr/bin/claude")
        merged = base.merge_task_overrides({"claude_bin": "/tmp/evil"})
        assert merged.claude_bin == "/usr/bin/claude"

    def test_none_values_skipped(self) -> None:
        base = ClaudeConfig(model="sonnet")
        merged = base.merge_task_overrides({"model": None})
        assert merged.model == "sonnet"

    def test_multiple_overrides(self) -> None:
        base = ClaudeConfig()
        merged = base.merge_task_overrides(
            {
                "model": "opus",
                "effort": "high",
                "max_turns": 5,
                "allowed_tools": ["bash", "read"],
            }
        )
        assert merged.model == "opus"
        assert merged.effort == "high"
        assert merged.max_turns == 5
        assert merged.allowed_tools == ["bash", "read"]

    def test_unknown_fields_ignored(self) -> None:
        base = ClaudeConfig()
        merged = base.merge_task_overrides({"unknown_field": "value"})
        assert not hasattr(merged, "unknown_field")

    def test_original_unchanged(self) -> None:
        base = ClaudeConfig(model="sonnet")
        _ = base.merge_task_overrides({"model": "opus"})
        assert base.model == "sonnet"


class TestOverridableFields:
    def test_work_dir_not_overridable(self) -> None:
        assert "work_dir" not in OVERRIDABLE_FIELDS

    def test_claude_bin_not_overridable(self) -> None:
        assert "claude_bin" not in OVERRIDABLE_FIELDS

    def test_model_overridable(self) -> None:
        assert "model" in OVERRIDABLE_FIELDS
