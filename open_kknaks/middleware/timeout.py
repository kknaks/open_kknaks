"""Timeout middleware — monitors task execution duration."""

import structlog

from open_kknaks.broker.base import AbstractBroker
from open_kknaks.middleware.base import Middleware
from open_kknaks.task import Task, TaskResult

logger = structlog.get_logger()

DEFAULT_TIMEOUT = 600  # 10 minutes


class TimeoutMiddleware(Middleware):
    """Enforce task-level timeout.

    The actual timeout enforcement is handled by ClaudeCodeExecutor's
    dual timeout (deadline + idle). This middleware logs and can adjust
    the timeout before execution starts.
    """

    def __init__(self, default_timeout: int = DEFAULT_TIMEOUT) -> None:
        self.default_timeout = default_timeout

    async def before_process(self, broker: AbstractBroker, task: Task) -> None:
        # Ensure task has a timeout set
        if task.timeout is None:
            task.timeout = self.default_timeout
        logger.debug(
            "timeout.set",
            task_id=task.id,
            timeout=task.timeout,
        )

    async def after_process(
        self,
        broker: AbstractBroker,
        task: Task,
        *,
        result: TaskResult | None = None,
        exception: BaseException | None = None,
    ) -> None:
        from open_kknaks.exceptions import IdleTimeoutError, TaskTimeoutError

        if isinstance(exception, (TaskTimeoutError, IdleTimeoutError)):
            logger.warning(
                "timeout.exceeded",
                task_id=task.id,
                timeout=task.timeout,
                exception_type=type(exception).__name__,
            )
