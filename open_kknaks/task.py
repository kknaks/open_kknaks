"""Task and related data models."""

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from open_kknaks.constants import DEFAULT_PROVIDER


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
    # Reasoning tokens, reported by codex as `reasoning_output_tokens`. A subset of
    # output_tokens, not an addition to it. Always 0 for providers that do not report it.
    reasoning_output_tokens: int = 0


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
    # tool_use / tool_result pairing
    tool_use_id: str | None = None
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
    debug_context: str | None = None


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
    provider: str = DEFAULT_PROVIDER

    # Status & Priority
    status: str = TaskStatus.PENDING
    priority: int = Priority.NORMAL
    delay_until: datetime | None = None

    # Provider Config (None = use Worker/adapter default)
    model: str | None = None
    options: dict[str, Any] = Field(default_factory=dict)
    provider_options: dict[str, Any] = Field(default_factory=dict)

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
