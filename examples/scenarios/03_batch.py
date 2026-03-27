"""Batch execution: 3 tasks in parallel."""

import asyncio

from open_kknaks.batch import BatchRunner
from open_kknaks.broker.redis import RedisBroker


async def main() -> None:
    broker = RedisBroker(url="redis://localhost:6379", namespace="example")
    await broker.connect()
    runner = BatchRunner(broker=broker)

    try:
        batch_id, task_ids = await runner.submit_batch(
            [
                {"prompt": "Explain Python's GIL in 2 sentences."},
                {"prompt": "Explain asyncio event loop in 2 sentences."},
                {"prompt": "Compare multiprocessing vs threading in Python in 2 sentences."},
            ],
            queue="default",
        )
        print(f"Batch submitted: {batch_id}")
        print(f"Task IDs: {task_ids}")

        results = await runner.wait_batch(task_ids, timeout=300)
        for r in results:
            print(f"\n{'=' * 60}")
            print(f"[{r.status}] {(r.result or 'no result')[:200]}...")
    finally:
        await broker.close()


if __name__ == "__main__":
    asyncio.run(main())
