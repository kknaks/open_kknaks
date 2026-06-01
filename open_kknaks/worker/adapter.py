"""Provider runner adapter contracts and Claude implementation."""

import shutil
import subprocess
from collections.abc import Awaitable, Callable
from typing import Protocol

from open_kknaks.config import ClaudeConfig
from open_kknaks.constants import PROVIDER_CLAUDE
from open_kknaks.task import StreamEvent, Task, TaskResult
from open_kknaks.worker.executor import ClaudeCodeExecutor


class RunnerAdapter(Protocol):
    """Provider-specific headless runner adapter."""

    provider: str

    def health_check(self) -> dict[str, str]:
        """Return provider CLI health information for worker registration."""
        ...

    async def execute(
        self,
        task: Task,
        config: ClaudeConfig,
        on_chunk: Callable[[StreamEvent], Awaitable[None]] | None = None,
    ) -> TaskResult:
        """Execute a task and return a normalized TaskResult."""
        ...

    async def cancel(self, task_id: str) -> bool:
        """Cancel an adapter-owned process if it is running."""
        ...

    async def cleanup_all(self) -> int:
        """Terminate all adapter-owned processes."""
        ...

    def active_task_ids(self) -> list[str]:
        """Return adapter-owned running task ids."""
        ...


class ClaudeRunnerAdapter:
    """Claude provider adapter preserving the legacy PTY executor behavior."""

    provider = PROVIDER_CLAUDE

    def __init__(self, config: ClaudeConfig) -> None:
        self.config = config
        self._executor = ClaudeCodeExecutor(claude_bin=config.claude_bin or "claude")

    def health_check(self) -> dict[str, str]:
        claude_bin = self.config.claude_bin or "claude"
        path = shutil.which(claude_bin)
        if not path:
            return {"claude": "not_found", "claude_version": "", "claude_path": ""}

        try:
            result = subprocess.run(
                [claude_bin, "--version"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            version = result.stdout.strip() if result.returncode == 0 else "unknown"
        except Exception:
            version = "unknown"

        return {"claude": "ok", "claude_version": version, "claude_path": path}

    async def execute(
        self,
        task: Task,
        config: ClaudeConfig,
        on_chunk: Callable[[StreamEvent], Awaitable[None]] | None = None,
    ) -> TaskResult:
        return await self._executor.execute(task=task, config=config, on_chunk=on_chunk)

    async def cancel(self, task_id: str) -> bool:
        return await self._executor.cancel(task_id)

    async def cleanup_all(self) -> int:
        return await self._executor.cleanup_all()

    def active_task_ids(self) -> list[str]:
        return list(self._executor._active.keys())
