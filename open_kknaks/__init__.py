"""open_kknaks — provider-based headless agent task queue library."""

from open_kknaks._version import __version__, __version_tuple__  # noqa: F401
from open_kknaks.batch import BatchRunner, BatchStatus
from open_kknaks.broker.base import AbstractBroker
from open_kknaks.broker.redis import RedisBroker
from open_kknaks.client import AgentClient
from open_kknaks.config import ClaudeConfig
from open_kknaks.constants import DEFAULT_PROVIDER, PROVIDER_CLAUDE, PROVIDER_CODEX, SUPPORTED_PROVIDERS
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

__all__ = [
    "DEFAULT_PROVIDER",
    "PROVIDER_CLAUDE",
    "PROVIDER_CODEX",
    "SUPPORTED_PROVIDERS",
    "AbstractBroker",
    "AgentClient",
    "BatchRunner",
    "BatchStatus",
    "BillingError",
    "ClaudeAuthError",
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
