"""Multi-queue routing: different queues for different task types."""

import asyncio

from common import describe_task_defaults, task_defaults

from open_kknaks.broker.redis import RedisBroker
from open_kknaks.client import AgentClient


async def main() -> None:
    broker = RedisBroker(url="redis://localhost:6379", namespace="example")
    await broker.connect()
    client = AgentClient(broker=broker)

    try:
        defaults = task_defaults()
        print(describe_task_defaults(defaults))
        # Analysis queue
        t1 = await client.submit(
            "Analyze this error log",
            context="TypeError: cannot unpack non-iterable NoneType object",
            queue="analysis",
            **defaults,
        )
        print(f"Analysis task: {t1}")

        # Review queue
        t2 = await client.submit(
            "Review this code",
            context="def foo(x): return x+1",
            queue="review",
            **defaults,
        )
        print(f"Review task: {t2}")

        r1 = await client.result(t1, timeout=120)
        r2 = await client.result(t2, timeout=120)

        if r1:
            print(f"\nAnalysis: {(r1.result or '')[:200]}")
        if r2:
            print(f"\nReview: {(r2.result or '')[:200]}")
    finally:
        await broker.close()


if __name__ == "__main__":
    asyncio.run(main())
