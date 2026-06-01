"""BatchRunner — manage batch task execution."""

import asyncio
import uuid
from enum import Enum
from typing import Any

from open_kknaks.broker.base import AbstractBroker
from open_kknaks.constants import DEFAULT_PROVIDER
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
        items: list[dict[str, Any]],
        *,
        queue: str = "default",
    ) -> tuple[str, list[str]]:
        """Submit a batch of tasks. Returns (batch_id, task_ids).

        Args:
            items: List of dicts with at least "prompt" key.
            queue: Target queue name.
        """
        batch_id = str(uuid.uuid4())
        task_ids: list[str] = []

        for item in items:
            task = Task(
                prompt=str(item["prompt"]),
                context=item.get("context"),
                queue=queue,
                provider=str(item.get("provider", DEFAULT_PROVIDER)),
                model=str(item["model"]) if item.get("model") else None,
                options=dict(item.get("options", {})),
                provider_options=dict(item.get("provider_options", {})),
                batch_id=batch_id,
                metadata=dict(item.get("metadata", {})),
            )
            await self.broker.enqueue(task)
            task_ids.append(task.id)

        return batch_id, task_ids

    async def get_batch_status(self, batch_id: str, task_ids: list[str]) -> BatchStatus:
        """Compute batch status from individual task statuses."""
        done_count = 0
        failed_count = 0
        running_count = 0
        seen_count = 0

        for task_id in task_ids:
            task = await self.broker.get_task(task_id)
            if task is None:
                continue
            seen_count += 1
            if task.status == TaskStatus.DONE:
                done_count += 1
            elif task.status in (TaskStatus.FAILED, TaskStatus.CANCELLED):
                failed_count += 1
            elif task.status == TaskStatus.RUNNING:
                running_count += 1

        total = len(task_ids)
        terminal_count = done_count + failed_count
        if seen_count == 0:
            return BatchStatus.PENDING
        if done_count == total:
            return BatchStatus.COMPLETED
        if failed_count == total:
            return BatchStatus.FAILED
        if terminal_count == total:
            return BatchStatus.PARTIAL_FAILURE
        if running_count > 0 or terminal_count > 0:
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
                        TaskStatus.DONE,
                        TaskStatus.FAILED,
                        TaskStatus.CANCELLED,
                    ):
                        results.append(task)
                        done.add(task_id)
                pending.difference_update(done)
                if pending:
                    await asyncio.sleep(poll_interval)

        try:
            await asyncio.wait_for(_poll(), timeout=timeout)
        except (TimeoutError, asyncio.TimeoutError):
            # Collect terminal snapshots only.
            for task_id in pending:
                task = await self.broker.get_task(task_id)
                if task and task.status in (
                    TaskStatus.DONE,
                    TaskStatus.FAILED,
                    TaskStatus.CANCELLED,
                ):
                    results.append(task)

        return results
