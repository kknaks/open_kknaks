"""PTY process wrapper with 3-stage termination."""

import asyncio
import contextlib
import os
import signal
import time


class PTYProcess:
    """Single PTY session running a Claude Code process.

    Manages the lifecycle of a forked process: liveness checks,
    3-stage termination (SIGHUP → SIGTERM → SIGKILL), and cleanup.
    """

    def __init__(self, pid: int, master_fd: int, pgid: int, task_id: str) -> None:
        self.pid = pid
        self.master_fd = master_fd
        self.pgid = pgid
        self.task_id = task_id
        self.started_at: float = time.monotonic()
        self._closed = False

    def is_alive(self) -> bool:
        """Check if the process is still running."""
        try:
            os.kill(self.pid, 0)
            return True
        except OSError:
            return False

    async def terminate(self, grace_period: float = 5.0) -> int:
        """3-stage termination: SIGHUP → SIGTERM → SIGKILL.

        Stage 1: SIGHUP to entire process group (PTY session).
        Stage 2: SIGTERM to direct process for those ignoring SIGHUP.
        Stage 3: SIGKILL to process group as last resort.

        Returns exit code (-1 if unknown).
        """
        if not self.is_alive():
            return self._reap()

        # Stage 1: SIGHUP → entire process group
        with contextlib.suppress(OSError):
            os.killpg(self.pgid, signal.SIGHUP)
        if await self._wait(grace_period):
            return self._reap()

        # Stage 2: SIGTERM → direct process
        with contextlib.suppress(OSError):
            os.kill(self.pid, signal.SIGTERM)
        if await self._wait(grace_period):
            return self._reap()

        # Stage 3: SIGKILL → force kill process group
        with contextlib.suppress(OSError):
            os.killpg(self.pgid, signal.SIGKILL)
        await self._wait(1.0)
        return self._reap()

    async def _wait(self, timeout: float) -> bool:
        """Wait for process to exit within timeout. Returns True if exited."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                pid, _ = os.waitpid(self.pid, os.WNOHANG)
                if pid != 0:
                    return True
            except ChildProcessError:
                return True
            await asyncio.sleep(0.1)
        return False

    def _reap(self) -> int:
        """Reap zombie process and close master_fd."""
        exit_code = -1
        try:
            pid, status = os.waitpid(self.pid, os.WNOHANG)
            if pid != 0 and os.WIFEXITED(status):
                exit_code = os.WEXITSTATUS(status)
        except ChildProcessError:
            pass
        self._close_fd()
        return exit_code

    def _close_fd(self) -> None:
        """Close master_fd if not already closed."""
        if not self._closed:
            self._closed = True
            with contextlib.suppress(OSError):
                os.close(self.master_fd)
