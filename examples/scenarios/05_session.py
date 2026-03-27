"""Session continuation: resume a previous conversation."""

import asyncio

from open_kknaks.broker.redis import RedisBroker
from open_kknaks.client import ClaudeClient


async def main() -> None:
    broker = RedisBroker(url="redis://localhost:6379", namespace="example")
    await broker.connect()
    client = ClaudeClient(broker=broker)

    try:
        # First message
        t1 = await client.submit("My name is Alice. Remember it.")
        r1 = await client.result(t1, timeout=120)
        if not r1:
            print("First task failed")
            return

        session_id = r1.result_session_id
        print(f"First result: {r1.result}")
        print(f"Session ID: {session_id}")

        if session_id:
            # Continue the session
            t2 = await client.submit(
                "What is my name?",
                session_id=session_id,
            )
            r2 = await client.result(t2, timeout=120)
            if r2:
                print(f"\nSecond result: {r2.result}")
    finally:
        await broker.close()


if __name__ == "__main__":
    asyncio.run(main())
