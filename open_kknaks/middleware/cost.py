"""3-stage cost control middleware."""

from collections.abc import Awaitable, Callable

import structlog

from open_kknaks.broker.base import AbstractBroker
from open_kknaks.exceptions import BillingError
from open_kknaks.middleware.base import Middleware
from open_kknaks.task import Task, TaskResult

logger = structlog.get_logger()


class BudgetExceededError(BillingError):
    """Raised when worker or global budget limit is exceeded."""


class CostMiddleware(Middleware):
    """3-stage cost control: worker budget, global budget, API billing.

    Stage 1: Worker budget (in-memory) — checked before_process.
    Stage 2: Global budget (Redis) — checked after_process.
    Stage 3: API billing errors (from StreamParser) — handled in after_process.
    """

    def __init__(
        self,
        worker_budget_usd: float | None = None,
        global_budget_usd: float | None = None,
        on_budget_alert: Callable[[str], Awaitable[None]] | None = None,
        alert_threshold: float = 0.8,
    ) -> None:
        self.worker_budget_usd = worker_budget_usd
        self.global_budget_usd = global_budget_usd
        self.on_budget_alert = on_budget_alert
        self.alert_threshold = alert_threshold
        self._worker_spent: float = 0.0

    async def before_process(self, broker: AbstractBroker, task: Task) -> None:
        """Stage 1 & 2: Check budget limits before execution."""
        # Stage 1: Worker budget
        if self.worker_budget_usd is not None and self._worker_spent >= self.worker_budget_usd:
            raise BudgetExceededError(
                f"Worker budget exceeded: ${self._worker_spent:.4f} >= ${self.worker_budget_usd:.4f}"
            )

        # Stage 2: Global budget
        if self.global_budget_usd is not None:
            total = await broker.get_total_cost()
            if total >= self.global_budget_usd:
                raise BudgetExceededError(f"Global budget exceeded: ${total:.4f} >= ${self.global_budget_usd:.4f}")

    async def after_process(
        self,
        broker: AbstractBroker,
        task: Task,
        *,
        result: TaskResult | None = None,
        exception: BaseException | None = None,
    ) -> None:
        """Stage 3: Record costs and detect API billing errors."""
        # Handle API billing error (HTTP 402)
        if isinstance(exception, BillingError) and not isinstance(exception, BudgetExceededError):
            await self._alert(
                f"API billing error: {exception}. Anthropic billing issue detected. Worker shutdown recommended."
            )
            return

        # Record usage costs
        if result and result.usage:
            cost = result.usage.cost_usd
            self._worker_spent += cost
            await broker.incr_cost(cost)

            logger.info(
                "cost.recorded",
                task_id=task.id,
                cost_usd=cost,
                worker_total=self._worker_spent,
            )

            # Check thresholds and alert
            await self._check_thresholds(broker)

    async def _check_thresholds(self, broker: AbstractBroker) -> None:
        """Check if approaching budget limits and alert."""
        if self.worker_budget_usd is not None:
            ratio = self._worker_spent / self.worker_budget_usd
            if ratio >= self.alert_threshold:
                await self._alert(
                    f"Worker budget alert: ${self._worker_spent:.4f} / ${self.worker_budget_usd:.4f} ({ratio:.0%})"
                )

        if self.global_budget_usd is not None:
            total = await broker.get_total_cost()
            ratio = total / self.global_budget_usd
            if ratio >= self.alert_threshold:
                await self._alert(f"Global budget alert: ${total:.4f} / ${self.global_budget_usd:.4f} ({ratio:.0%})")

    async def _alert(self, message: str) -> None:
        """Send budget alert via callback or log."""
        logger.warning("cost.alert", message=message)
        if self.on_budget_alert:
            try:
                await self.on_budget_alert(message)
            except Exception:
                logger.error("cost.alert_callback_failed", exc_info=True)
