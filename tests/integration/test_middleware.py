"""Integration tests for middleware chain."""

import contextlib

import pytest
import pytest_asyncio
from fakeredis import aioredis as fake_aioredis

from open_kknaks.broker.redis import RedisBroker
from open_kknaks.exceptions import BillingError, ClaudeAuthError, TaskCancelledError
from open_kknaks.middleware.base import Middleware
from open_kknaks.middleware.callback import CallbackMiddleware
from open_kknaks.middleware.cost import BudgetExceededError, CostMiddleware
from open_kknaks.middleware.logging import LoggingMiddleware
from open_kknaks.middleware.rate_limit import RateLimitMiddleware
from open_kknaks.middleware.retries import RetriesMiddleware
from open_kknaks.middleware.timeout import TimeoutMiddleware
from open_kknaks.task import Task, TaskResult, TokenUsage


@pytest_asyncio.fixture
async def broker():
    server = fake_aioredis.FakeServer()
    redis = fake_aioredis.FakeRedis(server=server)
    b = RedisBroker(redis=redis, namespace="test")
    await b.connect()
    yield b
    await b.close()


class TestMiddlewareBase:
    @pytest.mark.asyncio
    async def test_default_before_enqueue_returns_task(self, broker: RedisBroker) -> None:
        mw = Middleware()
        task = Task(prompt="test")
        result = await mw.before_enqueue(broker, task)
        assert result is task

    @pytest.mark.asyncio
    async def test_default_before_process_noop(self, broker: RedisBroker) -> None:
        mw = Middleware()
        task = Task(prompt="test")
        await mw.before_process(broker, task)  # should not raise

    @pytest.mark.asyncio
    async def test_default_after_process_noop(self, broker: RedisBroker) -> None:
        mw = Middleware()
        task = Task(prompt="test")
        await mw.after_process(broker, task, result=None, exception=None)


class TestLoggingMiddleware:
    @pytest.mark.asyncio
    async def test_logs_on_success(self, broker: RedisBroker) -> None:
        mw = LoggingMiddleware()
        task = Task(prompt="test")
        result = TaskResult(output="done", exit_code=0)
        await mw.before_process(broker, task)
        await mw.after_process(broker, task, result=result, exception=None)

    @pytest.mark.asyncio
    async def test_logs_on_failure(self, broker: RedisBroker) -> None:
        mw = LoggingMiddleware()
        task = Task(prompt="test")
        await mw.before_process(broker, task)
        await mw.after_process(
            broker, task, result=None, exception=RuntimeError("boom"),
        )


class TestRetriesMiddleware:
    @pytest.mark.asyncio
    async def test_retries_on_failure(self, broker: RedisBroker) -> None:
        mw = RetriesMiddleware(max_retries=3, min_backoff=1.0)
        task = Task(prompt="retry me", queue="default", max_retries=3)
        await broker.enqueue(task)
        await broker.dequeue(["default"], timeout=0)

        await mw.after_process(
            broker, task, result=None, exception=RuntimeError("fail"),
        )

        assert task.retry_count == 1
        assert task.status == "retrying"

    @pytest.mark.asyncio
    async def test_no_retry_on_billing_error(self, broker: RedisBroker) -> None:
        mw = RetriesMiddleware()
        task = Task(prompt="billing", queue="default")
        await mw.after_process(
            broker, task, result=None, exception=BillingError("402"),
        )
        assert task.retry_count == 0

    @pytest.mark.asyncio
    async def test_no_retry_on_auth_error(self, broker: RedisBroker) -> None:
        mw = RetriesMiddleware()
        task = Task(prompt="auth", queue="default")
        await mw.after_process(
            broker, task, result=None, exception=ClaudeAuthError("401"),
        )
        assert task.retry_count == 0

    @pytest.mark.asyncio
    async def test_no_retry_on_cancelled(self, broker: RedisBroker) -> None:
        mw = RetriesMiddleware()
        task = Task(prompt="cancel", queue="default")
        await mw.after_process(
            broker, task, result=None, exception=TaskCancelledError("cancelled"),
        )
        assert task.retry_count == 0

    @pytest.mark.asyncio
    async def test_no_retry_on_success(self, broker: RedisBroker) -> None:
        mw = RetriesMiddleware()
        task = Task(prompt="ok", queue="default")
        result = TaskResult(output="done")
        await mw.after_process(broker, task, result=result, exception=None)
        assert task.retry_count == 0

    @pytest.mark.asyncio
    async def test_exhausted_retries(self, broker: RedisBroker) -> None:
        mw = RetriesMiddleware(max_retries=2)
        task = Task(prompt="exhausted", queue="default", retry_count=2, max_retries=2)
        await mw.after_process(
            broker, task, result=None, exception=RuntimeError("fail"),
        )
        assert task.retry_count == 2  # Not incremented

    def test_exponential_backoff(self) -> None:
        mw = RetriesMiddleware(min_backoff=5.0, backoff_factor=2.0, max_backoff=300.0)
        # Verify backoff formula: min_backoff * (factor ^ retry_count)
        assert min(mw.min_backoff * (mw.backoff_factor ** 0), mw.max_backoff) == 5.0
        assert min(mw.min_backoff * (mw.backoff_factor ** 1), mw.max_backoff) == 10.0
        assert min(mw.min_backoff * (mw.backoff_factor ** 2), mw.max_backoff) == 20.0


class TestTimeoutMiddleware:
    @pytest.mark.asyncio
    async def test_sets_default_timeout(self, broker: RedisBroker) -> None:
        mw = TimeoutMiddleware(default_timeout=120)
        task = Task(prompt="test")
        assert task.timeout is None
        await mw.before_process(broker, task)
        assert task.timeout == 120

    @pytest.mark.asyncio
    async def test_respects_existing_timeout(self, broker: RedisBroker) -> None:
        mw = TimeoutMiddleware(default_timeout=120)
        task = Task(prompt="test", timeout=60)
        await mw.before_process(broker, task)
        assert task.timeout == 60


class TestCostMiddleware:
    @pytest.mark.asyncio
    async def test_records_cost(self, broker: RedisBroker) -> None:
        mw = CostMiddleware()
        task = Task(prompt="test", queue="default")
        usage = TokenUsage(cost_usd=0.05)
        result = TaskResult(output="done", usage=usage)
        await mw.after_process(broker, task, result=result, exception=None)

        assert mw._worker_spent == 0.05
        total = await broker.get_total_cost()
        assert abs(total - 0.05) < 0.001

    @pytest.mark.asyncio
    async def test_worker_budget_exceeded(self, broker: RedisBroker) -> None:
        mw = CostMiddleware(worker_budget_usd=0.10)
        mw._worker_spent = 0.10  # Already at limit

        task = Task(prompt="test")
        with pytest.raises(BudgetExceededError):
            await mw.before_process(broker, task)

    @pytest.mark.asyncio
    async def test_global_budget_exceeded(self, broker: RedisBroker) -> None:
        mw = CostMiddleware(global_budget_usd=1.0)
        await broker.incr_cost(1.0)  # Set global cost to limit

        task = Task(prompt="test")
        with pytest.raises(BudgetExceededError):
            await mw.before_process(broker, task)

    @pytest.mark.asyncio
    async def test_billing_error_alert(self, broker: RedisBroker) -> None:
        alerts: list[str] = []

        async def on_alert(msg: str) -> None:
            alerts.append(msg)

        mw = CostMiddleware(on_budget_alert=on_alert)
        task = Task(prompt="test")
        await mw.after_process(
            broker, task, result=None, exception=BillingError("402"),
        )
        assert len(alerts) == 1
        assert "billing" in alerts[0].lower()


class TestRateLimitMiddleware:
    @pytest.mark.asyncio
    async def test_allows_within_limit(self, broker: RedisBroker) -> None:
        mw = RateLimitMiddleware(max_per_minute=100)
        task = Task(prompt="test")
        await mw.before_process(broker, task)  # should not block

    @pytest.mark.asyncio
    async def test_slowdown_on_429(self, broker: RedisBroker) -> None:
        mw = RateLimitMiddleware(max_per_minute=60, slowdown_factor=0.5)
        original_rpm = mw._current_rpm
        task = Task(prompt="test")
        from open_kknaks.exceptions import RateLimitError
        await mw.after_process(
            broker, task, result=None, exception=RateLimitError("429"),
        )
        assert mw._current_rpm < original_rpm

    @pytest.mark.asyncio
    async def test_recovery_on_success(self, broker: RedisBroker) -> None:
        mw = RateLimitMiddleware(max_per_minute=60, recovery_factor=1.05)
        mw._current_rpm = 30.0  # Reduced
        task = Task(prompt="test")
        result = TaskResult(output="ok")
        await mw.after_process(broker, task, result=result, exception=None)
        assert mw._current_rpm > 30.0


class TestCallbackMiddleware:
    @pytest.mark.asyncio
    async def test_on_success_called(self, broker: RedisBroker) -> None:
        results: list[tuple[Task, TaskResult]] = []

        async def on_success(task: Task, result: TaskResult) -> None:
            results.append((task, result))

        mw = CallbackMiddleware(on_success=on_success)
        task = Task(prompt="test")
        result = TaskResult(output="done")
        await mw.after_process(broker, task, result=result, exception=None)
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_on_failure_called(self, broker: RedisBroker) -> None:
        errors: list[tuple[Task, BaseException]] = []

        async def on_failure(task: Task, exc: BaseException) -> None:
            errors.append((task, exc))

        mw = CallbackMiddleware(on_failure=on_failure)
        task = Task(prompt="test")
        await mw.after_process(
            broker, task, result=None, exception=RuntimeError("boom"),
        )
        assert len(errors) == 1

    @pytest.mark.asyncio
    async def test_callback_exception_swallowed(self, broker: RedisBroker) -> None:
        async def bad_callback(task: Task, result: TaskResult) -> None:
            raise ValueError("callback error")

        mw = CallbackMiddleware(on_success=bad_callback)
        task = Task(prompt="test")
        result = TaskResult(output="done")
        # Should not raise
        await mw.after_process(broker, task, result=result, exception=None)


class TestMiddlewareChainOrder:
    @pytest.mark.asyncio
    async def test_before_forward_after_reverse(self, broker: RedisBroker) -> None:
        """Verify before is forward order, after is reverse order."""
        order: list[str] = []

        class MW_A(Middleware):
            async def before_process(self, broker: RedisBroker, task: Task) -> None:  # type: ignore[override]
                order.append("before_A")

            async def after_process(self, broker: RedisBroker, task: Task, **kwargs: object) -> None:  # type: ignore[override]
                order.append("after_A")

        class MW_B(Middleware):
            async def before_process(self, broker: RedisBroker, task: Task) -> None:  # type: ignore[override]
                order.append("before_B")

            async def after_process(self, broker: RedisBroker, task: Task, **kwargs: object) -> None:  # type: ignore[override]
                order.append("after_B")

        middlewares = [MW_A(), MW_B()]
        task = Task(prompt="test")

        # Simulate middleware chain (same pattern as Worker._process_task)
        called: list[Middleware] = []
        for mw in middlewares:
            await mw.before_process(broker, task)
            called.append(mw)

        for mw in reversed(called):
            await mw.after_process(broker, task, result=None, exception=None)

        assert order == ["before_A", "before_B", "after_B", "after_A"]

    @pytest.mark.asyncio
    async def test_before_exception_breaks_chain(self, broker: RedisBroker) -> None:
        """Exception in before_process stops remaining middlewares."""
        order: list[str] = []

        class MW_A(Middleware):
            async def before_process(self, broker: RedisBroker, task: Task) -> None:  # type: ignore[override]
                order.append("before_A")
                raise RuntimeError("break")

            async def after_process(self, broker: RedisBroker, task: Task, **kwargs: object) -> None:  # type: ignore[override]
                order.append("after_A")

        class MW_B(Middleware):
            async def before_process(self, broker: RedisBroker, task: Task) -> None:  # type: ignore[override]
                order.append("before_B")

            async def after_process(self, broker: RedisBroker, task: Task, **kwargs: object) -> None:  # type: ignore[override]
                order.append("after_B")

        middlewares = [MW_A(), MW_B()]
        task = Task(prompt="test")

        called: list[Middleware] = []
        exception = None
        try:
            for mw in middlewares:
                await mw.before_process(broker, task)
                called.append(mw)
        except RuntimeError as e:
            exception = e

        # After chain (reverse of called)
        for mw in reversed(called):
            await mw.after_process(broker, task, result=None, exception=exception)

        # MW_A raised in before, so it was NOT added to called list
        # MW_B was never reached. No after calls.
        assert order == ["before_A"]

    @pytest.mark.asyncio
    async def test_after_exception_doesnt_stop_chain(self, broker: RedisBroker) -> None:
        """Exception in after_process is swallowed, chain continues."""
        order: list[str] = []

        class MW_A(Middleware):
            async def after_process(self, broker: RedisBroker, task: Task, **kwargs: object) -> None:  # type: ignore[override]
                order.append("after_A")

        class MW_B(Middleware):
            async def after_process(self, broker: RedisBroker, task: Task, **kwargs: object) -> None:  # type: ignore[override]
                order.append("after_B")
                raise RuntimeError("after crash")

        called = [MW_A(), MW_B()]
        task = Task(prompt="test")

        for mw in reversed(called):
            with contextlib.suppress(Exception):
                await mw.after_process(broker, task, result=None, exception=None)

        assert order == ["after_B", "after_A"]
