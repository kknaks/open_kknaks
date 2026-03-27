"""ClaudeClient — async client for submitting tasks and monitoring results."""

import asyncio
from collections.abc import AsyncIterator

from open_kknaks.broker.base import AbstractBroker
from open_kknaks.task import Priority, StreamEvent, Task, TaskStatus


class ClaudeClient:
    """Thin async client for the task queue.

    Submits tasks to the broker and monitors results.
    Does NOT run Claude Code CLI directly — that's the Worker's job.
    """

    def __init__(self, broker: AbstractBroker) -> None:
        self.broker = broker

    async def submit(
        self,
        prompt: str,
        *,
        context: str | None = None,
        queue: str = "default",
        priority: int | Priority = Priority.NORMAL,
        delay_seconds: int | None = None,
        timeout: int | None = None,
        max_retries: int = 0,
        model: str | None = None,
        system_prompt: str | None = None,
        append_system_prompt: str | None = None,
        max_turns: int | None = None,
        effort: str | None = None,
        json_schema: str | None = None,
        allowed_tools: list[str] | None = None,
        disallowed_tools: list[str] | None = None,
        permission_mode: str | None = None,
        session_id: str | None = None,
        mcp_config: str | None = None,
        add_dirs: list[str] | None = None,
        metadata: dict[str, str | int | float | bool | None] | None = None,
    ) -> str:
        """Submit a task to the queue. Returns task_id."""
        task = Task(
            prompt=prompt,
            context=context,
            queue=queue,
            priority=int(priority),
            timeout=timeout,
            max_retries=max_retries,
            model=model,
            system_prompt=system_prompt,
            append_system_prompt=append_system_prompt,
            max_turns=max_turns,
            effort=effort,
            json_schema=json_schema,
            allowed_tools=allowed_tools,
            disallowed_tools=disallowed_tools,
            permission_mode=permission_mode,
            session_id=session_id,
            mcp_config=mcp_config,
            add_dirs=add_dirs,
            metadata=metadata or {},
        )
        await self.broker.enqueue(task, delay=delay_seconds)
        return task.id

    async def status(self, task_id: str) -> str | None:
        """Get current task status. Returns None if not found."""
        task = await self.broker.get_task(task_id)
        if task is None:
            return None
        return task.status

    async def result(self, task_id: str, *, timeout: float = 600) -> Task | None:
        """Wait for task completion and return the full Task.

        Uses XREAD BLOCK on Redis Stream — no polling.
        Returns None if task not found.
        """
        task = await self.broker.get_task(task_id)
        if task is None:
            return None

        # If already done, return immediately
        if task.status in (TaskStatus.DONE, TaskStatus.FAILED, TaskStatus.CANCELLED):
            return task

        # Wait for completion via stream subscription
        async def _wait() -> Task | None:
            async for _event in self.broker.subscribe_chunks(task_id):
                t = await self.broker.get_task(task_id)
                if t and t.status in (
                    TaskStatus.DONE, TaskStatus.FAILED, TaskStatus.CANCELLED,
                ):
                    return t
            return None

        try:
            result = await asyncio.wait_for(_wait(), timeout=timeout)
            if result is not None:
                return result
        except (TimeoutError, asyncio.TimeoutError):
            pass

        # Final check
        return await self.broker.get_task(task_id)

    async def stream(self, task_id: str, *, timeout: float = 600) -> AsyncIterator[StreamEvent]:
        """Stream task output in real-time.

        Yields StreamEvent objects as they arrive via XREAD BLOCK.
        Stops after timeout seconds.
        """
        import time

        deadline = time.monotonic() + timeout
        async for event in self.broker.subscribe_chunks(task_id):
            yield event
            if time.monotonic() >= deadline:
                return

    async def cancel(self, task_id: str) -> bool:
        """Request task cancellation. Returns True if task was found."""
        task = await self.broker.get_task(task_id)
        if task is None:
            return False

        task.status = TaskStatus.CANCELLED
        await self.broker.update_task(task)
        return True
