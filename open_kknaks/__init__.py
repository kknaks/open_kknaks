"""open_kknaks — PTY-based Claude Code task queue library."""

from __future__ import annotations

from typing import TYPE_CHECKING

from open_kknaks._version import __version__, __version_tuple__  # noqa: F401
from open_kknaks.batch import BatchRunner, BatchStatus
from open_kknaks.broker.base import AbstractBroker
from open_kknaks.client import ClaudeClient
from open_kknaks.config import ClaudeConfig
from open_kknaks.exceptions import (
    BillingError,
    ClaudeAuthError,
    ClaudeNotFoundError,
    ExecutionError,
    IdleTimeoutError,
    OpenKknaksError,
    RateLimitError,
    TaskCancelledError,
    TaskTimeoutError,
)
from open_kknaks.task import Priority, StreamEvent, Task, TaskResult, TaskStatus, TokenUsage

if TYPE_CHECKING:
    from open_kknaks.broker.redis import RedisBroker

__all__ = [
    "AbstractBroker",
    "BatchRunner",
    "BatchStatus",
    "BillingError",
    "ClaudeAuthError",
    "ClaudeClient",
    "ClaudeConfig",
    "ClaudeNotFoundError",
    "ExecutionError",
    "IdleTimeoutError",
    "OpenKknaksError",
    "Priority",
    "RateLimitError",
    "RedisBroker",
    "StreamEvent",
    "Task",
    "TaskCancelledError",
    "TaskResult",
    "TaskStatus",
    "TaskTimeoutError",
    "TokenUsage",
]


def __getattr__(name: str) -> type:
    if name == "RedisBroker":
        from open_kknaks.broker.redis import RedisBroker

        return RedisBroker
    msg = f"module {__name__!r} has no attribute {name!r}"
    raise AttributeError(msg)
