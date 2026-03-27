"""Tests for PTYProcess."""

import asyncio
import contextlib
import os
import pty
import time

import pytest

from open_kknaks.worker.pty_process import PTYProcess


def _spawn_sleep(seconds: float = 60.0) -> tuple[int, int]:
    """Spawn a child process via PTY that runs sleep."""
    master_fd, slave_fd = pty.openpty()
    pid = os.fork()
    if pid == 0:
        # Child
        os.close(master_fd)
        os.setsid()
        os.dup2(slave_fd, 0)
        os.dup2(slave_fd, 1)
        os.dup2(slave_fd, 2)
        if slave_fd > 2:
            os.close(slave_fd)
        os.execvp("sleep", ["sleep", str(seconds)])
    else:
        # Parent
        os.close(slave_fd)
        return pid, master_fd
    raise RuntimeError("unreachable")


class TestPTYProcessBasic:
    def test_is_alive_running(self) -> None:
        pid, master_fd = _spawn_sleep(60)
        proc = PTYProcess(pid=pid, master_fd=master_fd, pgid=pid, task_id="t1")
        try:
            assert proc.is_alive()
        finally:
            os.kill(pid, 9)
            with contextlib.suppress(ChildProcessError):
                os.waitpid(pid, 0)
            with contextlib.suppress(OSError):
                os.close(master_fd)

    def test_is_alive_dead(self) -> None:
        pid, master_fd = _spawn_sleep(0.01)
        time.sleep(0.1)
        with contextlib.suppress(ChildProcessError):
            os.waitpid(pid, os.WNOHANG)
        proc = PTYProcess(pid=pid, master_fd=master_fd, pgid=pid, task_id="t2")
        assert not proc.is_alive()
        proc._close_fd()


class TestPTYProcessTerminate:
    @pytest.mark.asyncio
    async def test_terminate_returns_exit_code(self) -> None:
        pid, master_fd = _spawn_sleep(60)
        proc = PTYProcess(pid=pid, master_fd=master_fd, pgid=pid, task_id="t3")
        assert proc.is_alive()
        exit_code = await proc.terminate(grace_period=2.0)
        assert isinstance(exit_code, int)
        assert not proc.is_alive()

    @pytest.mark.asyncio
    async def test_terminate_already_dead(self) -> None:
        pid, master_fd = _spawn_sleep(0.01)
        await asyncio.sleep(0.2)
        proc = PTYProcess(pid=pid, master_fd=master_fd, pgid=pid, task_id="t4")
        exit_code = await proc.terminate()
        assert isinstance(exit_code, int)

    @pytest.mark.asyncio
    async def test_fd_closed_after_terminate(self) -> None:
        pid, master_fd = _spawn_sleep(60)
        proc = PTYProcess(pid=pid, master_fd=master_fd, pgid=pid, task_id="t5")
        await proc.terminate(grace_period=2.0)
        assert proc._closed


class TestPTYProcessCloseFd:
    def test_close_fd_idempotent(self) -> None:
        pid, master_fd = _spawn_sleep(0.01)
        time.sleep(0.1)
        with contextlib.suppress(ChildProcessError):
            os.waitpid(pid, os.WNOHANG)
        proc = PTYProcess(pid=pid, master_fd=master_fd, pgid=pid, task_id="t6")
        proc._close_fd()
        proc._close_fd()  # should not raise
        assert proc._closed
