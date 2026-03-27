"""Real-time streaming output."""

import asyncio

from open_kknaks.broker.redis import RedisBroker
from open_kknaks.client import ClaudeClient


async def main() -> None:
    broker = RedisBroker(url="redis://localhost:6379", namespace="example")
    await broker.connect()
    client = ClaudeClient(broker=broker)

    try:
        task_id = await client.submit("Write a simple TODO API in FastAPI with 3 endpoints.")
        print(f"Submitted: {task_id}\n")

        async for event in client.stream(task_id):
            if event.text:
                print(event.text, end="", flush=True)
        print("\n\nDone!")
    finally:
        await broker.close()


if __name__ == "__main__":
    asyncio.run(main())
