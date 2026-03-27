"""Middleware base class with 6 signal hooks."""

from open_kknaks.broker.base import AbstractBroker
from open_kknaks.task import Task, TaskResult


class Middleware:
    """Base middleware class. Subclass and override signal methods.

    All signal methods receive broker as first argument (not via constructor).
    This follows the Dramatiq pattern for testability and clarity.
    """

    async def before_enqueue(self, broker: AbstractBroker, task: Task) -> Task | None:
        """Called before task is added to queue.

        Return the task to proceed, or None to cancel enqueue.
        """
        return task

    async def after_enqueue(self, broker: AbstractBroker, task: Task) -> None:
        """Called after task is successfully added to queue."""

    async def before_process(self, broker: AbstractBroker, task: Task) -> None:
        """Called before task execution.

        Raise an exception to break the middleware chain and fail the task.
        """

    async def after_process(
        self,
        broker: AbstractBroker,
        task: Task,
        *,
        result: TaskResult | None = None,
        exception: BaseException | None = None,
    ) -> None:
        """Called after task execution (success or failure).

        Called in reverse order. ALL middlewares are called even if one raises.
        """

    async def before_worker_boot(self, broker: AbstractBroker, worker: object) -> None:
        """Called when worker starts up."""

    async def after_worker_shutdown(self, broker: AbstractBroker, worker: object) -> None:
        """Called when worker shuts down."""
