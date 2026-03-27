"""ClaudeWorker — dequeue tasks and execute via PTY."""

import asyncio
import shutil
import signal
import subprocess
import uuid
from datetime import datetime, timezone

import structlog

from open_kknaks.broker.base import AbstractBroker
from open_kknaks.config import ClaudeConfig
from open_kknaks.exceptions import TaskCancelledError
from open_kknaks.middleware.base import Middleware
from open_kknaks.task import StreamEvent, Task, TaskResult, TaskStatus
from open_kknaks.worker.executor import ClaudeCodeExecutor

logger = structlog.get_logger()


class ClaudeWorker:
    """Consume tasks from broker and execute via PTY.

    Architecture:
    - DequeueLoop: polls broker for tasks
    - ProcessorLoop x concurrency: execute tasks with middleware chain
    - HeartbeatLoop: periodic broker heartbeat
    """

    def __init__(
        self,
        broker: AbstractBroker,
        config: ClaudeConfig | None = None,
        middleware: list[Middleware] | None = None,
        queues: list[str] | None = None,
        concurrency: int = 4,
        shutdown_timeout: float = 30.0,
    ) -> None:
        self.broker = broker
        self.config = config or ClaudeConfig()
        self.middleware = middleware or []
        self.queues = queues or ["default"]
        self.concurrency = concurrency
        self.shutdown_timeout = shutdown_timeout
        self.worker_id = f"worker-{uuid.uuid4().hex[:8]}"

        self.stale_timeout = 60.0
        self.maintenance_interval = 30.0

        self._executor = ClaudeCodeExecutor(
            claude_bin=self.config.claude_bin or "claude",
        )
        self._semaphore = asyncio.Semaphore(concurrency)
        self._running = False
        self._stopping = False
        self._tasks: set[asyncio.Task[None]] = set()

    def _check_claude_status(self) -> dict[str, str]:
        """Check Claude CLI availability and version."""
        claude_bin = self.config.claude_bin or "claude"
        path = shutil.which(claude_bin)
        if not path:
            return {"claude": "not_found", "claude_version": "", "claude_path": ""}

        try:
            result = subprocess.run(
                [claude_bin, "--version"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            version = result.stdout.strip() if result.returncode == 0 else "unknown"
        except Exception:
            version = "unknown"

        return {"claude": "ok", "claude_version": version, "claude_path": path}

    async def start(self) -> None:
        """Start the worker loops."""
        self._running = True

        # Check Claude CLI status
        self._claude_status = self._check_claude_status()
        logger.info("claude.check", **self._claude_status)

        # Reap stale workers from previous runs (Redis 찌꺼기 정리)
        try:
            reaped = await self.broker.reap_stale_workers(timeout=self.stale_timeout)
            if reaped:
                logger.info("worker.startup_cleanup", reaped_workers=len(reaped), worker_ids=reaped)
        except Exception:
            logger.error("worker.startup_cleanup_failed", exc_info=True)

        # Register worker with claude status
        await self.broker.register_worker(self.worker_id, self.queues, self._claude_status)

        # Emit before_worker_boot
        for mw in self.middleware:
            try:
                await mw.before_worker_boot(self.broker, self)
            except Exception:
                logger.error("middleware.before_worker_boot_failed", exc_info=True)

        logger.info(
            "worker.started",
            worker_id=self.worker_id,
            queues=self.queues,
            concurrency=self.concurrency,
        )

        # Start loops
        self._dequeue_task = asyncio.create_task(self._dequeue_loop())
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        self._maintenance_task = asyncio.create_task(self._maintenance_loop())

    async def stop(self) -> None:
        """Gracefully stop the worker."""
        if self._stopping:
            return
        self._stopping = True
        self._running = False
        logger.info("worker.stopping", worker_id=self.worker_id)

        # Cancel dequeue, heartbeat, and maintenance loops
        self._dequeue_task.cancel()
        self._heartbeat_task.cancel()
        self._maintenance_task.cancel()

        # Wait for in-flight tasks
        if self._tasks:
            logger.info(
                "worker.waiting_for_tasks",
                in_flight=len(self._tasks),
                timeout=self.shutdown_timeout,
            )
            _done, pending = await asyncio.wait(
                self._tasks,
                timeout=self.shutdown_timeout,
            )

            # Force terminate any remaining and requeue
            if pending:
                logger.warning(
                    "worker.force_terminating",
                    pending=len(pending),
                )
                # Collect active task IDs before cleanup
                active_task_ids = list(self._executor._active.keys())
                terminated = await self._executor.cleanup_all()
                for t in pending:
                    t.cancel()

                # Requeue force-terminated tasks so they're not lost
                for queue in self.queues:
                    if active_task_ids:
                        try:
                            await self.broker.requeue(queue, active_task_ids)
                        except Exception:
                            logger.error("worker.requeue_failed", exc_info=True)

                logger.info(
                    "worker.terminated_processes",
                    count=terminated,
                    requeued=len(active_task_ids),
                )

        # Deregister worker from broker
        try:
            await self.broker.deregister_worker(self.worker_id)
        except Exception:
            logger.error("worker.deregister_failed", exc_info=True)

        # Emit after_worker_shutdown
        for mw in self.middleware:
            try:
                await mw.after_worker_shutdown(self.broker, self)
            except Exception:
                logger.error("middleware.after_worker_shutdown_failed", exc_info=True)

        logger.info("worker.stopped", worker_id=self.worker_id)

    async def run(self) -> None:
        """Start the worker and run until interrupted."""
        loop = asyncio.get_running_loop()

        # Install signal handlers
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, lambda: asyncio.create_task(self.stop()))

        await self.start()

        # Keep running until stopped
        while self._running:
            await asyncio.sleep(1.0)

    def _merge_config(self, task: Task) -> ClaudeConfig:
        """Merge worker config with task-level overrides."""
        overrides: dict[str, object] = {}
        for field in (
            "model",
            "system_prompt",
            "append_system_prompt",
            "max_turns",
            "effort",
            "json_schema",
            "allowed_tools",
            "disallowed_tools",
            "permission_mode",
            "mcp_config",
            "add_dirs",
        ):
            value = getattr(task, field, None)
            if value is not None:
                overrides[field] = value
        return self.config.merge_task_overrides(overrides)

    # ─── Internal Loops ───

    async def _dequeue_loop(self) -> None:
        """Poll broker for tasks and dispatch to processors."""
        while self._running:
            try:
                await self._semaphore.acquire()

                task = await self.broker.dequeue(self.queues, timeout=1.0)
                if task is None:
                    self._semaphore.release()
                    continue

                # Dispatch to processor
                processor = asyncio.create_task(self._process_task(task))
                self._tasks.add(processor)
                processor.add_done_callback(self._tasks.discard)

            except asyncio.CancelledError:
                return
            except Exception:
                logger.error("dequeue.error", exc_info=True)
                self._semaphore.release()
                await asyncio.sleep(1.0)

    async def _heartbeat_loop(self) -> None:
        """Send periodic heartbeats to broker."""
        while self._running:
            try:
                await self.broker.heartbeat(self.worker_id)
                await asyncio.sleep(15.0)
            except asyncio.CancelledError:
                return
            except Exception:
                logger.error("heartbeat.error", exc_info=True)
                await asyncio.sleep(5.0)

    async def _maintenance_loop(self) -> None:
        """Periodically reap stale workers and promote delayed tasks."""
        while self._running:
            try:
                # Reap workers with stale heartbeats
                reaped = await self.broker.reap_stale_workers(timeout=self.stale_timeout)
                if reaped:
                    logger.info("maintenance.reaped_workers", count=len(reaped), worker_ids=reaped)

                # Promote delayed tasks
                for queue_name in self.queues:
                    promoted = await self.broker.promote_delayed(queue_name)
                    if promoted:
                        logger.info("maintenance.promoted_delayed", queue=queue_name, count=promoted)

                await asyncio.sleep(self.maintenance_interval)
            except asyncio.CancelledError:
                return
            except Exception:
                logger.error("maintenance.error", exc_info=True)
                await asyncio.sleep(self.maintenance_interval)

    async def _process_task(self, task: Task) -> None:
        """Execute task with full middleware chain."""
        called_middlewares: list[Middleware] = []
        result: TaskResult | None = None
        exception: BaseException | None = None

        try:
            # Update status to RUNNING
            task.status = TaskStatus.RUNNING
            task.started_at = datetime.now(timezone.utc)
            await self.broker.update_task(task)

            # BEFORE chain (sequential, exception breaks)
            for mw in self.middleware:
                await mw.before_process(self.broker, task)
                called_middlewares.append(mw)

            # Merge config
            config = self._merge_config(task)

            # Execute via PTY
            async def _on_chunk(chunk: StreamEvent) -> None:
                await self.broker.publish_chunk(task.id, chunk)

            result = await self._executor.execute(
                task=task,
                config=config,
                on_chunk=_on_chunk,
            )

            # Success
            task.status = TaskStatus.DONE
            task.result = result.output
            task.exit_code = result.exit_code
            task.result_session_id = result.session_id
            task.usage = result.usage
            task.finished_at = datetime.now(timezone.utc)
            await self.broker.update_task(task)
            await self.broker.ack(task.queue, task.id)

        except TaskCancelledError:
            task.status = TaskStatus.CANCELLED
            task.finished_at = datetime.now(timezone.utc)
            await self.broker.update_task(task)
            await self.broker.ack(task.queue, task.id)

        except Exception as e:
            exception = e
            task.status = TaskStatus.FAILED
            task.error = str(e)
            task.exception_type = type(e).__name__
            task.finished_at = datetime.now(timezone.utc)
            await self.broker.update_task(task)

        finally:
            # AFTER chain (reverse order, all called regardless of exception)
            for mw in reversed(called_middlewares):
                try:
                    await mw.after_process(
                        self.broker,
                        task,
                        result=result,
                        exception=exception,
                    )
                except Exception:
                    logger.error(
                        "middleware.after_process_failed",
                        middleware=type(mw).__name__,
                        task_id=task.id,
                        exc_info=True,
                    )

            # If still FAILED and not being retried → nack (moves to DLQ)
            if task.status == TaskStatus.FAILED:
                await self.broker.nack(task.queue, task.id)

            self._semaphore.release()
