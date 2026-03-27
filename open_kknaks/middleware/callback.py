"""Callback middleware for task completion notifications."""

from collections.abc import Awaitable, Callable

import structlog

from open_kknaks.broker.base import AbstractBroker
from open_kknaks.middleware.base import Middleware
from open_kknaks.task import Task, TaskResult

logger = structlog.get_logger()


class CallbackMiddleware(Middleware):
    """Invoke callbacks on task success or failure."""

    def __init__(
        self,
        on_success: Callable[[Task, TaskResult], Awaitable[None]] | None = None,
        on_failure: Callable[[Task, BaseException], Awaitable[None]] | None = None,
    ) -> None:
        self.on_success = on_success
        self.on_failure = on_failure

    async def after_process(
        self,
        broker: AbstractBroker,
        task: Task,
        *,
        result: TaskResult | None = None,
        exception: BaseException | None = None,
    ) -> None:
        if exception is None and result is not None and self.on_success:
            try:
                await self.on_success(task, result)
            except Exception:
                logger.error("callback.on_success_failed", task_id=task.id, exc_info=True)

        if exception is not None and self.on_failure:
            try:
                await self.on_failure(task, exception)
            except Exception:
                logger.error("callback.on_failure_failed", task_id=task.id, exc_info=True)
