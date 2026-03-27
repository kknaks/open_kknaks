"""Example worker — run via docker compose or directly."""

import asyncio
import os

from open_kknaks.broker.redis import RedisBroker
from open_kknaks.config import ClaudeConfig
from open_kknaks.middleware.cost import CostMiddleware
from open_kknaks.middleware.logging import LoggingMiddleware
from open_kknaks.middleware.retries import RetriesMiddleware
from open_kknaks.middleware.timeout import TimeoutMiddleware
from open_kknaks.worker.worker import ClaudeWorker


async def main() -> None:
    broker = RedisBroker(
        url=os.environ.get("REDIS_URL", "redis://localhost:6379"),
        namespace=os.environ.get("NAMESPACE", "example"),
    )
    await broker.connect()

    config = ClaudeConfig(
        work_dir=os.environ.get("WORK_DIR", "/project"),
    )

    worker = ClaudeWorker(
        broker=broker,
        config=config,
        queues=os.environ.get("QUEUES", "default").split(","),
        concurrency=int(os.environ.get("CONCURRENCY", "2")),
        middleware=[
            LoggingMiddleware(),
            RetriesMiddleware(max_retries=2),
            TimeoutMiddleware(),
            CostMiddleware(
                worker_budget_usd=5.0,
                global_budget_usd=20.0,
            ),
        ],
    )

    print(f"Worker starting: queues={worker.queues}, concurrency={worker.concurrency}")

    try:
        await worker.run()
    finally:
        await broker.close()


if __name__ == "__main__":
    asyncio.run(main())
