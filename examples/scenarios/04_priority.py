"""Priority and delayed execution."""

import asyncio

from open_kknaks.broker.redis import RedisBroker
from open_kknaks.client import ClaudeClient
from open_kknaks.task import Priority


async def main() -> None:
    broker = RedisBroker(url="redis://localhost:6379", namespace="example")
    await broker.connect()
    client = ClaudeClient(broker=broker)

    try:
        # High priority task
        t1 = await client.submit(
            "What is 2+2?",
            priority=Priority.HIGH,
        )
        print(f"HIGH priority: {t1}")

        # Normal priority, delayed by 10 seconds
        t2 = await client.submit(
            "What is 3+3?",
            priority=Priority.NORMAL,
            delay_seconds=10,
        )
        print(f"NORMAL priority (10s delay): {t2}")

        # Low priority task
        t3 = await client.submit(
            "What is 4+4?",
            priority=Priority.LOW,
        )
        print(f"LOW priority: {t3}")

        # Wait for high priority first
        r1 = await client.result(t1, timeout=60)
        if r1:
            print(f"\nHIGH result: {r1.result}")
    finally:
        await broker.close()


if __name__ == "__main__":
    asyncio.run(main())
