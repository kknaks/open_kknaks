"""Structured logging middleware using structlog."""

import structlog

from open_kknaks.broker.base import AbstractBroker
from open_kknaks.middleware.base import Middleware
from open_kknaks.task import Task, TaskResult

logger = structlog.get_logger()


class LoggingMiddleware(Middleware):
    """Log task lifecycle events. Always enabled."""

    async def before_process(self, broker: AbstractBroker, task: Task) -> None:
        logger.info(
            "task.start",
            task_id=task.id,
            prompt=task.prompt[:100],
            queue=task.queue,
            priority=task.priority,
            retry_count=task.retry_count,
        )

    async def after_process(
        self,
        broker: AbstractBroker,
        task: Task,
        *,
        result: TaskResult | None = None,
        exception: BaseException | None = None,
    ) -> None:
        if exception is None:
            logger.info(
                "task.done",
                task_id=task.id,
                exit_code=task.exit_code,
                cost_usd=task.usage.cost_usd if task.usage else None,
                duration_ms=task.usage.duration_ms if task.usage else None,
            )
        else:
            logger.error(
                "task.failed",
                task_id=task.id,
                exception_type=type(exception).__name__,
                error=str(exception)[:200],
                retry_count=task.retry_count,
            )

    async def before_worker_boot(self, broker: AbstractBroker, worker: object) -> None:
        logger.info("worker.boot")

    async def after_worker_shutdown(self, broker: AbstractBroker, worker: object) -> None:
        logger.info("worker.shutdown")
