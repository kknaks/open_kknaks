"""Worker configuration models."""

from pydantic import BaseModel, ConfigDict

# Fields that Task IS allowed to override
OVERRIDABLE_FIELDS: frozenset[str] = frozenset(
    {
        "model",
        "system_prompt",
        "append_system_prompt",
        "max_turns",
        "effort",
        "json_schema",
        "allowed_tools",
        "disallowed_tools",
        "permission_mode",
        "mcp_config",
        "add_dirs",
    }
)


class ClaudeConfig(BaseModel):
    """Configuration for Claude CLI invocation."""

    model_config = ConfigDict(use_enum_values=True)

    # Environment
    work_dir: str = "."
    claude_bin: str | None = None

    # LLM / Prompt
    model: str | None = None
    system_prompt: str | None = None
    append_system_prompt: str | None = None
    max_turns: int | None = None
    effort: str | None = None
    json_schema: str | None = None

    # Tools / Permissions
    allowed_tools: list[str] | None = None
    disallowed_tools: list[str] | None = None
    permission_mode: str = "default"

    # Session / Environment
    mcp_config: str | None = None
    add_dirs: list[str] | None = None

    def merge_task_overrides(self, overrides: dict[str, object]) -> "ClaudeConfig":
        """Create a new config with task-level overrides applied.

        Only fields in OVERRIDABLE_FIELDS are accepted.
        work_dir and claude_bin are silently dropped for security.
        """
        safe = {k: v for k, v in overrides.items() if k in OVERRIDABLE_FIELDS and v is not None}
        return self.model_copy(update=safe)
