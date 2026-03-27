"""Retry middleware with exponential backoff."""

import structlog

from open_kknaks.broker.base import AbstractBroker
from open_kknaks.exceptions import BillingError, ClaudeAuthError, TaskCancelledError
from open_kknaks.middleware.base import Middleware
from open_kknaks.task import Task, TaskResult, TaskStatus

logger = structlog.get_logger()

# Exceptions that must never be retried
NO_RETRY_DEFAULT: tuple[type[BaseException], ...] = (
    TaskCancelledError,
    ClaudeAuthError,
    BillingError,
)


class RetriesMiddleware(Middleware):
    """Retry failed tasks with exponential backoff.

    On failure, re-enqueues the task with a delay via broker.enqueue().
    Respects no_retry_on exceptions (BillingError, ClaudeAuthError, TaskCancelledError).
    """

    def __init__(
        self,
        max_retries: int = 3,
        min_backoff: float = 5.0,
        max_backoff: float = 300.0,
        backoff_factor: float = 2.0,
        no_retry_on: tuple[type[BaseException], ...] = NO_RETRY_DEFAULT,
    ) -> None:
        self.max_retries = max_retries
        self.min_backoff = min_backoff
        self.max_backoff = max_backoff
        self.backoff_factor = backoff_factor
        self.no_retry_on = no_retry_on

    async def after_process(
        self,
        broker: AbstractBroker,
        task: Task,
        *,
        result: TaskResult | None = None,
        exception: BaseException | None = None,
    ) -> None:
        if exception is None:
            return

        # Don't retry certain exceptions
        if isinstance(exception, self.no_retry_on):
            logger.info(
                "retry.skip_no_retry",
                task_id=task.id,
                exception_type=type(exception).__name__,
            )
            return

        # Check max retries
        # task.max_retries=0 means "no retries" (explicit). Only fall back to
        # middleware default when the task doesn't specify (None would be ideal,
        # but the field defaults to 0, so we treat 0 as "use middleware default").
        # To explicitly disable retries for a task, set max_retries=0 AND
        # don't attach RetriesMiddleware, or set no_retry_on broadly.
        effective_max = task.max_retries if task.max_retries > 0 else self.max_retries
        if task.retry_count >= effective_max:
            logger.info(
                "retry.exhausted",
                task_id=task.id,
                retry_count=task.retry_count,
                max_retries=effective_max,
            )
            return

        # Calculate backoff delay
        delay = min(
            self.min_backoff * (self.backoff_factor ** task.retry_count),
            self.max_backoff,
        )

        # Re-enqueue with delay
        task.retry_count += 1
        task.status = TaskStatus.RETRYING
        task.error = None  # Clear previous error for fresh attempt
        await broker.update_task(task)
        await broker.enqueue(task, delay=int(delay))

        logger.info(
            "retry.scheduled",
            task_id=task.id,
            retry_count=task.retry_count,
            delay_seconds=int(delay),
        )
