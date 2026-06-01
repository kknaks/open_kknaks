"""Real-time streaming output."""

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
        task_id = await client.submit(
            "Write a simple TODO API in FastAPI with 3 endpoints.",
            **defaults,
        )
        print(f"Submitted: {task_id}\n")

        async for event in client.stream(task_id):
            if event.text:
                print(event.text, end="", flush=True)
        print("\n\nDone!")
    finally:
        await broker.close()


if __name__ == "__main__":
    asyncio.run(main())
