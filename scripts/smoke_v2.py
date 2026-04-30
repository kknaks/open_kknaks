"""Smoke test for v2.0 TaskResult split.

Calls real Claude CLI once via PTY executor and prints:
  - task.result  (should be the clean final assistant text)
  - task.stream  (should contain delta + assistant + result, noisy)
  - on_chunk count (should be > 0 with --include-partial-messages)
  - whether \\n appears mid-text in result (should be False for short Korean)

Run: uv run python scripts/smoke_v2.py
"""

import asyncio

from open_kknaks.config import ClaudeConfig
from open_kknaks.task import StreamEvent, Task
from open_kknaks.worker.executor import ClaudeCodeExecutor


async def main() -> None:
    executor = ClaudeCodeExecutor()
    task = Task(
        prompt=(
            "한국어로만 답해줘. '안녕하세요'로 시작하고 '트렌드'라는 단어를 "
            "반드시 포함하는 두 문장을 만들어. 다른 부연 설명, 코드 블록, "
            "마크다운 헤더는 절대 쓰지 말고 평문 두 문장만 출력해."
        ),
        timeout=120,
    )
    config = ClaudeConfig(model="haiku")

    chunk_count = {"text": 0, "cost": 0, "init": 0, "other": 0}

    async def on_chunk(event: StreamEvent) -> None:
        if event.type == "text":
            chunk_count["text"] += 1
        elif event.type == "cost":
            chunk_count["cost"] += 1
        elif event.type == "init":
            chunk_count["init"] += 1
        else:
            chunk_count["other"] += 1

    print("Running claude (haiku) ... this hits real Claude API.\n")
    result = await executor.execute(task, config, on_chunk=on_chunk)

    sep = "=" * 70
    print(sep)
    print("RESULT (TaskResult.result — should be clean final text):")
    print(sep)
    print(repr(result.result))
    print()
    print("Rendered:")
    print(result.result)
    print()

    print(sep)
    print("STREAM (TaskResult.stream — noisy concatenation, debug only):")
    print(sep)
    # Truncate stream display to first 400 + last 200 chars to keep terminal sane.
    s = result.stream
    if len(s) > 700:
        print(repr(s[:400]) + "  ...[truncated]...  " + repr(s[-200:]))
    else:
        print(repr(s))
    print()

    print(sep)
    print("CHECKS")
    print(sep)
    print(f"exit_code:                     {result.exit_code}")
    print(f"session_id:                    {result.session_id}")
    print(f"usage.cost_usd:                {result.usage.cost_usd if result.usage else None}")
    print(f"on_chunk text count:           {chunk_count['text']}  (expect > 0)")
    print(f"on_chunk cost count:           {chunk_count['cost']}  (expect 1)")
    print(f"len(result.result):            {len(result.result)}")
    print(f"len(result.stream):            {len(result.stream)}")
    print(f"stream >= result length?:      {len(result.stream) >= len(result.result)}")
    print(f"'\\n' inside result.result?:    {chr(10) in result.result}  (expect False for short prompt)")
    print(f"'안녕' present in result.result?: {'안녕' in result.result}")
    print(f"'트렌드' present in result.result?: {'트렌드' in result.result}")


if __name__ == "__main__":
    asyncio.run(main())
