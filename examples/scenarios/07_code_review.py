"""Code analysis: summarize the open_kknaks project structure.

Worker must have work_dir pointing to the project root
so Claude Code can read the actual source files.
"""

import asyncio

from open_kknaks.broker.redis import RedisBroker
from open_kknaks.client import ClaudeClient


async def main() -> None:
    broker = RedisBroker(url="redis://localhost:6379", namespace="example")
    await broker.connect()
    client = ClaudeClient(broker=broker)

    try:
        task_id = await client.submit(
            "open_kknaks/ 디렉토리의 구조를 분석하고, "
            "각 레이어(L0~L5)별로 핵심 파일과 역할을 한 줄씩 요약해줘. "
            "코드를 직접 읽어서 답해줘.",
        )
        print(f"Submitted: {task_id}")
        print("Waiting for Claude to analyze the project...\n")

        result = await client.result(task_id, timeout=300)
        if result:
            print(f"Status: {result.status}")
            print(f"\n{result.result}")
            if result.usage:
                print(f"\nCost: ${result.usage.cost_usd:.4f}")
        else:
            print("Task not found")
    finally:
        await broker.close()


if __name__ == "__main__":
    asyncio.run(main())
