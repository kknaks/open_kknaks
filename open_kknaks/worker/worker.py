"""ClaudeWorker — dequeue tasks and execute via PTY."""

import asyncio
import contextlib
import signal
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone

import structlog

from open_kknaks.broker.base import AbstractBroker
from open_kknaks.config import ClaudeConfig
from open_kknaks.constants import PROVIDER_CLAUDE, PROVIDER_CODEX
from open_kknaks.exceptions import TaskCancelledError
from open_kknaks.middleware.base import Middleware
from open_kknaks.task import StreamEvent, Task, TaskResult, TaskStatus
from open_kknaks.worker.adapter import ClaudeRunnerAdapter, RunnerAdapter
from open_kknaks.worker.codex_adapter import CodexRunnerAdapter

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
        # How often a running task re-checks the broker for a cancellation request, and
        # how long the terminated process is given to unwind before we stop waiting.
        self.cancel_poll_interval = 1.0
        self.cancel_grace_period = 5.0

        self._adapters: dict[str, RunnerAdapter] = {
            PROVIDER_CLAUDE: ClaudeRunnerAdapter(self.config),
            PROVIDER_CODEX: CodexRunnerAdapter(),
        }
        self._semaphore = asyncio.Semaphore(concurrency)
        self._running = False
        self._stopping = False
        self._tasks: set[asyncio.Task[None]] = set()

    def _check_provider_status(self) -> dict[str, str]:
        """Check provider CLI availability and versions."""
        status: dict[str, str] = {}
        for adapter in self._adapters.values():
            status.update(adapter.health_check())
        return status

    async def start(self) -> None:
        """Start the worker loops."""
        self._running = True

        # Check provider CLI status
        self._provider_status = self._check_provider_status()
        logger.info("provider.check", **self._provider_status)

        # Reap stale workers from previous runs (Redis 찌꺼기 정리)
        try:
            reaped = await self.broker.reap_stale_workers(timeout=self.stale_timeout)
            if reaped:
                logger.info("worker.startup_cleanup", reaped_workers=len(reaped), worker_ids=reaped)
        except Exception:
            logger.error("worker.startup_cleanup_failed", exc_info=True)

        # Register worker with provider status
        await self.broker.register_worker(self.worker_id, self.queues, self._provider_status)

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
                active_task_ids: list[str] = []
                terminated = 0
                for adapter in self._adapters.values():
                    active_task_ids.extend(adapter.active_task_ids())
                    terminated += await adapter.cleanup_all()
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
        """Merge worker config with task-level provider overrides."""
        overrides: dict[str, object] = dict(task.provider_options)
        if task.model is not None:
            overrides["model"] = task.model

        merged = self.config.merge_task_overrides(overrides)

        cwd = task.options.get("cwd")
        if isinstance(cwd, str) and cwd:
            merged = merged.model_copy(update={"work_dir": cwd})

        return merged

    def _get_adapter(self, provider: str) -> RunnerAdapter | None:
        return self._adapters.get(provider)

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

    async def _watch_for_cancel(self, task_id: str) -> None:
        """Return once the broker reports this task as CANCELLED.

        Polling, not pub/sub, on purpose:

        - The task hash is the authoritative cancellation record — `ClaudeClient.cancel`
          only writes there — so polling it cannot disagree with the source of truth.
        - Redis pub/sub is at-most-once and has no backlog. A message published while the
          worker was reconnecting would be lost and the process would run to completion,
          which is exactly the failure being fixed here.
        - It also catches a cancel that landed before this watcher started.

        The cost is one HGET per in-flight task per interval, which is negligible beside a
        CLI run, and it mirrors how `subscribe_chunks` already polls task status when the
        stream goes idle.
        """
        while True:
            await asyncio.sleep(self.cancel_poll_interval)
            try:
                current = await self.broker.get_task(task_id)
            except Exception:
                # A transient broker error must not tear down a healthy run; try again.
                logger.warning("task.cancel_poll_failed", task_id=task_id, exc_info=True)
                continue
            if current is not None and current.status == TaskStatus.CANCELLED:
                return

    async def _execute_watching_for_cancel(
        self,
        adapter: RunnerAdapter,
        task: Task,
        config: ClaudeConfig,
        on_chunk: Callable[[StreamEvent], Awaitable[None]],
    ) -> TaskResult:
        """Run the adapter, killing its process if the task gets cancelled mid-flight.

        Raises TaskCancelledError on cancellation so `_process_task` takes its existing
        cancellation branch (status=CANCELLED + ack). Without this the terminated process
        would surface as a non-zero exit code and be recorded as FAILED, then nacked to the
        DLQ — the wrong status for a user-requested stop.
        """
        exec_task = asyncio.create_task(adapter.execute(task=task, config=config, on_chunk=on_chunk))
        watch_task = asyncio.create_task(self._watch_for_cancel(task.id))

        try:
            done, _pending = await asyncio.wait({exec_task, watch_task}, return_when=asyncio.FIRST_COMPLETED)

            # If the run finished on its own, report its real outcome even when a cancel
            # landed in the same instant — there is no longer a process to stop.
            if exec_task in done:
                return await exec_task

            logger.info("task.cancel_detected", task_id=task.id, provider=task.provider)
            await adapter.cancel(task.id)

            # Let the adapter reap the terminated process so its own bookkeeping (fd close,
            # waitpid) runs. asyncio.wait never cancels, so a slow unwind just times out.
            await asyncio.wait({exec_task}, timeout=self.cancel_grace_period)

            raise TaskCancelledError(f"Task {task.id} cancelled while running")
        finally:
            watch_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await watch_task
            if not exec_task.done():
                exec_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await exec_task

    async def _process_task(self, task: Task) -> None:
        """Execute task with full middleware chain."""
        called_middlewares: list[Middleware] = []
        result: TaskResult | None = None
        exception: BaseException | None = None

        try:
            if task.status == TaskStatus.CANCELLED:
                task.finished_at = datetime.now(timezone.utc)
                await self.broker.update_task(task)
                await self.broker.ack(task.queue, task.id)
                return

            adapter = self._get_adapter(task.provider)
            if adapter is None:
                task.status = TaskStatus.FAILED
                task.error = f"Unsupported provider: {task.provider}"
                task.finished_at = datetime.now(timezone.utc)
                await self.broker.update_task(task)
                return

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

            # Execute via provider adapter
            async def _on_chunk(chunk: StreamEvent) -> None:
                await self.broker.publish_chunk(task.id, chunk)

            result = await self._execute_watching_for_cancel(
                adapter=adapter,
                task=task,
                config=config,
                on_chunk=_on_chunk,
            )

            # Check exit code
            task.result = result.result
            task.exit_code = result.exit_code
            task.result_session_id = result.session_id
            task.usage = result.usage
            task.finished_at = datetime.now(timezone.utc)

            if result.exit_code != 0:
                task.status = TaskStatus.FAILED
                # Prefer the full stream for error context — when the process
                # crashes the result text is usually empty, but stream may carry
                # partial assistant output or error messages from the CLI.
                error_context = result.stream.strip() or result.result.strip() or (result.debug_context or "").strip()
                task.error = (
                    error_context[:500]
                    if error_context
                    else f"Process exited with code {result.exit_code} (empty output)"
                )
                logger.error(
                    "task.failed",
                    task_id=task.id,
                    exit_code=result.exit_code,
                    error=task.error[:200],
                )
                await self.broker.update_task(task)
            else:
                task.status = TaskStatus.DONE
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
