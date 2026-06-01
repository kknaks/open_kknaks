"""Basic usage: submit -> result."""

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
            "Explain what a Python decorator is in 3 sentences.",
            **defaults,
        )
        print(f"Submitted: {task_id}")

        result = await client.result(task_id, timeout=120)
        if result:
            print(f"Status: {result.status}")
            print(f"Result:\n{result.result}")
            if result.usage:
                print(f"Tokens: {result.usage.input_tokens} in / {result.usage.output_tokens} out")
                print(f"Cost: ${result.usage.cost_usd:.4f}")
        else:
            print("Task not found")
    finally:
        await broker.close()


if __name__ == "__main__":
    asyncio.run(main())
