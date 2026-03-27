"""Rate limiting middleware with adaptive throttling."""

import asyncio
import time

import structlog

from open_kknaks.broker.base import AbstractBroker
from open_kknaks.exceptions import RateLimitError
from open_kknaks.middleware.base import Middleware
from open_kknaks.task import Task, TaskResult

logger = structlog.get_logger()


class RateLimitMiddleware(Middleware):
    """Preemptive + reactive rate limiting.

    Preemptive: Enforces max_per_minute before starting a task.
    Reactive: On 429 response, slows down; on success, gradually recovers.
    """

    def __init__(
        self,
        max_per_minute: int = 60,
        slowdown_factor: float = 0.5,
        recovery_factor: float = 1.05,
    ) -> None:
        self.max_per_minute = max_per_minute
        self.slowdown_factor = slowdown_factor
        self.recovery_factor = recovery_factor
        self._timestamps: list[float] = []
        self._current_rpm: float = float(max_per_minute)

    async def before_process(self, broker: AbstractBroker, task: Task) -> None:
        """Preemptive: Wait if we're at the rate limit."""
        now = time.monotonic()
        cutoff = now - 60.0

        # Remove timestamps older than 1 minute
        self._timestamps = [t for t in self._timestamps if t > cutoff]

        # If at limit, wait
        if len(self._timestamps) >= int(self._current_rpm):
            oldest = self._timestamps[0]
            wait_time = 60.0 - (now - oldest)
            if wait_time > 0:
                logger.info(
                    "rate_limit.waiting",
                    task_id=task.id,
                    wait_seconds=round(wait_time, 1),
                    current_rpm=int(self._current_rpm),
                )
                await asyncio.sleep(wait_time)

        self._timestamps.append(time.monotonic())

    async def after_process(
        self,
        broker: AbstractBroker,
        task: Task,
        *,
        result: TaskResult | None = None,
        exception: BaseException | None = None,
    ) -> None:
        """Reactive: Adjust rate based on success/failure."""
        if isinstance(exception, RateLimitError):
            # Slow down on 429
            old_rpm = self._current_rpm
            self._current_rpm = max(self._current_rpm * self.slowdown_factor, 1.0)
            logger.warning(
                "rate_limit.slowdown",
                task_id=task.id,
                old_rpm=int(old_rpm),
                new_rpm=int(self._current_rpm),
            )
        elif exception is None:
            # Gradually recover on success
            self._current_rpm = min(
                self._current_rpm * self.recovery_factor,
                float(self.max_per_minute),
            )
