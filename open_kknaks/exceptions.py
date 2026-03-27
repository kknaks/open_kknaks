"""open_kknaks exception hierarchy."""


class OpenKknaksError(Exception):
    """Base exception for all open_kknaks errors."""


class ClaudeAuthError(OpenKknaksError):
    """Authentication failure — no retry."""


class ClaudeNotFoundError(OpenKknaksError):
    """Claude CLI binary not found — no retry."""


class BillingError(OpenKknaksError):
    """HTTP 402 billing error — no retry, alert worker."""


class TaskCancelledError(OpenKknaksError):
    """Task was cancelled by user or system."""


class TaskTimeoutError(OpenKknaksError):
    """Task exceeded its timeout."""


class IdleTimeoutError(OpenKknaksError):
    """No output received for idle timeout period."""


class RateLimitError(OpenKknaksError):
    """HTTP 429 rate limit — CLI auto-retries, library logs."""


class ExecutionError(OpenKknaksError):
    """General execution error during task run."""
