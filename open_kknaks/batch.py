"""BatchRunner — manage batch task execution."""

import asyncio
import uuid
from enum import Enum

from open_kknaks.broker.base import AbstractBroker
from open_kknaks.task import Task, TaskStatus


class BatchStatus(str, Enum):
    """Batch lifecycle states."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL_FAILURE = "partial_failure"
    FAILED = "failed"


class BatchRunner:
    """Track and manage batch task execution."""

    def __init__(self, broker: AbstractBroker) -> None:
        self.broker = broker

    async def submit_batch(
        self,
        prompts: list[dict[str, str]],
        *,
        queue: str = "default",
        mode: str = "parallel",
    ) -> tuple[str, list[str]]:
        """Submit a batch of tasks. Returns (batch_id, task_ids).

        Args:
            prompts: List of dicts with at least "prompt" key.
            queue: Target queue name.
            mode: "parallel" (all at once) or "sequential" (one by one).
        """
        batch_id = str(uuid.uuid4())
        task_ids: list[str] = []

        for item in prompts:
            task = Task(
                prompt=item["prompt"],
                context=item.get("context"),
                queue=queue,
                batch_id=batch_id,
            )
            await self.broker.enqueue(task)
            task_ids.append(task.id)

        return batch_id, task_ids

    async def get_batch_status(self, batch_id: str, task_ids: list[str]) -> BatchStatus:
        """Compute batch status from individual task statuses."""
        done_count = 0
        failed_count = 0

        for task_id in task_ids:
            task = await self.broker.get_task(task_id)
            if task is None:
                continue
            if task.status == TaskStatus.DONE:
                done_count += 1
            elif task.status in (TaskStatus.FAILED, TaskStatus.CANCELLED):
                failed_count += 1

        total = len(task_ids)
        if done_count == total:
            return BatchStatus.COMPLETED
        if failed_count == total:
            return BatchStatus.FAILED
        if done_count + failed_count == total:
            return BatchStatus.PARTIAL_FAILURE
        if done_count > 0 or failed_count > 0:
            return BatchStatus.RUNNING
        return BatchStatus.PENDING

    async def wait_batch(
        self,
        task_ids: list[str],
        *,
        timeout: float = 3600,
        poll_interval: float = 1.0,
    ) -> list[Task]:
        """Wait for all batch tasks to complete. Returns list of Tasks."""
        results: list[Task] = []
        pending = set(task_ids)

        async def _poll() -> None:
            while pending:
                done: set[str] = set()
                for task_id in list(pending):
                    task = await self.broker.get_task(task_id)
                    if task and task.status in (
                        TaskStatus.DONE, TaskStatus.FAILED, TaskStatus.CANCELLED,
                    ):
                        results.append(task)
                        done.add(task_id)
                pending.difference_update(done)
                if pending:
                    await asyncio.sleep(poll_interval)

        try:
            await asyncio.wait_for(_poll(), timeout=timeout)
        except (TimeoutError, asyncio.TimeoutError):
            # Collect whatever is done
            for task_id in pending:
                task = await self.broker.get_task(task_id)
                if task:
                    results.append(task)

        return results
