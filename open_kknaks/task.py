"""Task and related data models."""

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class TaskStatus(str, Enum):
    """Task lifecycle states."""

    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"
    RETRYING = "retrying"


class Priority(int, Enum):
    """Task priority levels. Lower value = higher priority."""

    HIGH = 1
    NORMAL = 5
    LOW = 9


class TokenUsage(BaseModel):
    """Token usage and cost information from a Claude run."""

    model_config = ConfigDict(use_enum_values=True)

    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    duration_ms: int = 0


class StreamEvent(BaseModel):
    """A single event emitted during task streaming."""

    model_config = ConfigDict(use_enum_values=True)

    type: Literal[
        "text",
        "cost",
        "retry",
        "tool_use",
        "tool_result",
        "thinking",
        "init",
        "progress",
    ]
    # text / thinking
    text: str | None = None
    # cost
    cost_usd: float | None = None
    # retry
    retry_info: str | None = None
    # tool_use
    tool_name: str | None = None
    tool_input: dict[str, Any] | None = None
    # tool_result
    tool_result: str | None = None
    tool_is_error: bool | None = None
    # init
    model: str | None = None
    session_id: str | None = None
    # progress
    total_tokens: int | None = None
    tool_uses: int | None = None
    duration_ms: int | None = None
    description: str | None = None
    last_tool_name: str | None = None


class TaskResult(BaseModel):
    """Internal result returned by the PTY executor.

    `result` is the final assistant text from the result message — the value
    most callers want. `stream` is the full concatenation of every text event
    seen during execution (delta + assistant + result) and is intended for
    debugging or for callers that need the raw narration. The two are
    intentionally separate because partial deltas can split mid-grapheme.
    """

    model_config = ConfigDict(use_enum_values=True)

    result: str = ""
    stream: str = ""
    exit_code: int = 0
    session_id: str | None = None
    usage: TokenUsage | None = None


def _uuid4_str() -> str:
    return str(uuid.uuid4())


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


class Task(BaseModel):
    """Primary task model for the queue system."""

    model_config = ConfigDict(use_enum_values=True)

    # Identity & Routing
    id: str = Field(default_factory=_uuid4_str)
    prompt: str
    context: str | None = None
    queue: str = "default"

    # Status & Priority
    status: str = TaskStatus.PENDING
    priority: int = Priority.NORMAL
    delay_until: datetime | None = None

    # Claude Config (None = use Worker default)
    model: str | None = None
    system_prompt: str | None = None
    append_system_prompt: str | None = None
    max_turns: int | None = None
    effort: str | None = None
    json_schema: str | None = None
    allowed_tools: list[str] | None = None
    disallowed_tools: list[str] | None = None
    permission_mode: str | None = None
    session_id: str | None = None
    mcp_config: str | None = None
    add_dirs: list[str] | None = None
    timeout: int | None = None

    # Retries
    max_retries: int = 0
    retry_count: int = 0
    exception_type: str | None = None

    # Results
    result: str | None = None
    error: str | None = None
    exit_code: int | None = None
    result_session_id: str | None = None
    usage: TokenUsage | None = None

    # Metadata
    batch_id: str | None = None
    metadata: dict[str, str | int | float | bool | None] = Field(default_factory=dict)

    # Timestamps
    created_at: datetime = Field(default_factory=_now_utc)
    started_at: datetime | None = None
    finished_at: datetime | None = None
