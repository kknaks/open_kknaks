"""Abstract broker interface for task queue."""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

from open_kknaks.task import StreamEvent, Task


class AbstractBroker(ABC):
    """Base interface for task queue broker.

    All implementations must be async-first.
    Only RedisBroker is supported — no InMemoryBroker.
    """

    # ─── Queueing ───

    @abstractmethod
    async def enqueue(self, task: Task, *, delay: int | None = None) -> None:
        """Add task to queue. If delay > 0, task becomes visible after delay seconds."""

    @abstractmethod
    async def dequeue(self, queue_names: list[str], timeout: float = 1.0) -> Task | None:
        """Dequeue highest-priority task from one of the queues.

        Returns None if no task is available within timeout.
        """

    @abstractmethod
    async def ack(self, queue_name: str, task_id: str) -> None:
        """Acknowledge successful task completion."""

    @abstractmethod
    async def nack(self, queue_name: str, task_id: str) -> None:
        """Negative acknowledge — move task to DLQ."""

    @abstractmethod
    async def requeue(self, queue_name: str, task_ids: list[str]) -> None:
        """Requeue tasks from processing back to main queue."""

    # ─── State / Results ───

    @abstractmethod
    async def get_task(self, task_id: str) -> Task | None:
        """Retrieve task by ID. Returns None if not found."""

    @abstractmethod
    async def update_task(self, task: Task) -> None:
        """Update task state and results in storage."""

    # ─── Streaming ───

    @abstractmethod
    async def publish_chunk(self, task_id: str, chunk: StreamEvent) -> None:
        """Publish a stream event chunk for real-time streaming."""

    @abstractmethod
    async def subscribe_chunks(self, task_id: str) -> AsyncIterator[StreamEvent]:
        """Subscribe to event stream for a task.

        Returns an async iterator that yields StreamEvent objects.
        """
        yield  # type: ignore[misc]

    # ─── DLQ ───

    @abstractmethod
    async def list_dlq(self, queue_name: str, limit: int = 100) -> list[Task]:
        """List tasks in the Dead Letter Queue."""

    @abstractmethod
    async def retry_from_dlq(self, queue_name: str, task_id: str) -> None:
        """Move a task from DLQ back to the main queue."""

    @abstractmethod
    async def purge_dlq(self, queue_name: str) -> None:
        """Clear all tasks from DLQ."""

    # ─── Worker Management ───

    @abstractmethod
    async def register_worker(
        self, worker_id: str, queues: list[str], extra: dict[str, str] | None = None,
    ) -> None:
        """Register worker and its queue subscriptions."""

    @abstractmethod
    async def deregister_worker(self, worker_id: str) -> None:
        """Remove worker from registry (called on graceful shutdown)."""

    @abstractmethod
    async def heartbeat(self, worker_id: str) -> None:
        """Update worker heartbeat timestamp."""

    @abstractmethod
    async def reap_stale_workers(self, timeout: float = 60.0) -> list[str]:
        """Find workers whose heartbeat is older than timeout seconds.

        For each stale worker:
        1. Requeue any tasks in its active sets
        2. Remove from worker registry

        Returns list of reaped worker IDs.
        """

    @abstractmethod
    async def queue_size(self, queue_name: str) -> int:
        """Get number of pending tasks in queue."""

    # ─── Costs ───

    @abstractmethod
    async def incr_cost(self, amount: float, worker_id: str | None = None) -> None:
        """Increment total and optionally worker-specific cost."""

    @abstractmethod
    async def get_total_cost(self) -> float:
        """Get namespace-wide cumulative cost."""

    @abstractmethod
    async def get_worker_cost(self, worker_id: str) -> float:
        """Get worker-specific cumulative cost."""

    # ─── Lifecycle ───

    @abstractmethod
    async def connect(self) -> None:
        """Connect to underlying backend."""

    @abstractmethod
    async def close(self) -> None:
        """Disconnect and cleanup resources."""
