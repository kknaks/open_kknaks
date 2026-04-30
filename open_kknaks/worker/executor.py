"""PTY-based Claude Code CLI executor."""

import asyncio
import contextlib
import errno
import fcntl
import os
import pty
import struct
import termios
import time
from collections.abc import Awaitable, Callable

import structlog

from open_kknaks.config import ClaudeConfig
from open_kknaks.exceptions import IdleTimeoutError, TaskTimeoutError
from open_kknaks.task import StreamEvent, Task, TaskResult, TokenUsage
from open_kknaks.worker.line_buffer import LineBuffer
from open_kknaks.worker.pty_process import PTYProcess
from open_kknaks.worker.stream_parser import parse_stream_json_line

logger = structlog.get_logger()

DEFAULT_TIMEOUT = 600  # 10 minutes
IDLE_TIMEOUT = 30  # 30 seconds no output
READ_SIZE = 4096
TERMINAL_ROWS = 24
TERMINAL_COLS = 200


class ClaudeCodeExecutor:
    """Execute Claude Code CLI via PTY with stream-json parsing."""

    def __init__(self, claude_bin: str = "claude") -> None:
        self.claude_bin = claude_bin
        self._active: dict[str, PTYProcess] = {}
        self._lock = asyncio.Lock()

    async def execute(
        self,
        task: Task,
        config: ClaudeConfig,
        on_chunk: Callable[[StreamEvent], Awaitable[None]] | None = None,
    ) -> TaskResult:
        """Execute a task via PTY and return the result.

        Args:
            task: Task to execute.
            config: Merged ClaudeConfig for this run.
            on_chunk: Optional async callback for real-time streaming.

        Returns:
            TaskResult with result, stream, exit_code, session_id, usage.

        Raises:
            TaskTimeoutError: If task exceeds its timeout.
            IdleTimeoutError: If no output for IDLE_TIMEOUT seconds.
            BillingError: If billing error detected in stream.
            ClaudeAuthError: If auth error detected in stream.
        """
        cmd = self._build_command(task, config)
        claude_bin = config.claude_bin or self.claude_bin

        # Create PTY
        master_fd, slave_fd = pty.openpty()

        # Set terminal size
        winsize = struct.pack("HHHH", TERMINAL_ROWS, TERMINAL_COLS, 0, 0)
        fcntl.ioctl(slave_fd, termios.TIOCSWINSZ, winsize)

        # Fork
        pid = os.fork()

        if pid == 0:
            # Child process — all errors must be caught and written to PTY
            try:
                os.close(master_fd)
                os.setsid()
                os.dup2(slave_fd, 0)
                os.dup2(slave_fd, 1)
                os.dup2(slave_fd, 2)
                if slave_fd > 2:
                    os.close(slave_fd)

                # Set working directory
                if config.work_dir:
                    work_dir = os.path.expanduser(config.work_dir)
                    if not os.path.isdir(work_dir):
                        msg = f"open_kknaks: work_dir does not exist: {work_dir}\n"
                        os.write(2, msg.encode())
                        os._exit(1)
                    os.chdir(work_dir)

                # Write context to stdin if provided
                if task.context:
                    os.environ["CLAUDE_CONTEXT"] = task.context

                os.execvp(claude_bin, cmd)
            except Exception as exc:
                try:
                    msg = f"open_kknaks: child process error: {exc}\n"
                    os.write(2, msg.encode())
                except Exception:
                    pass
                os._exit(1)

        # Parent process
        os.close(slave_fd)

        # Set master_fd to non-blocking
        flags = fcntl.fcntl(master_fd, fcntl.F_GETFL)
        fcntl.fcntl(master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

        process = PTYProcess(pid=pid, master_fd=master_fd, pgid=pid, task_id=task.id)

        async with self._lock:
            self._active[task.id] = process

        try:
            result = await self._read_pty_output(process, task, on_chunk)
        finally:
            async with self._lock:
                self._active.pop(task.id, None)

        return result

    async def cancel(self, task_id: str) -> bool:
        """Cancel a running task. Returns True if found and terminated."""
        async with self._lock:
            process = self._active.pop(task_id, None)

        if process is None:
            return False

        await process.terminate()
        return True

    async def cleanup_all(self) -> int:
        """Terminate all active processes. Returns count terminated."""
        async with self._lock:
            processes = list(self._active.values())
            self._active.clear()

        for proc in processes:
            await proc.terminate()
        return len(processes)

    def _build_command(self, task: Task, config: ClaudeConfig) -> list[str]:
        """Build Claude CLI command from task and config."""
        claude_bin = config.claude_bin or self.claude_bin
        cmd = [claude_bin, "-p", "--output-format", "stream-json", "--verbose", "--include-partial-messages"]

        if config.model:
            cmd.extend(["--model", config.model])
        if config.system_prompt:
            cmd.extend(["--system-prompt", config.system_prompt])
        if config.append_system_prompt:
            cmd.extend(["--append-system-prompt", config.append_system_prompt])
        if config.max_turns is not None:
            cmd.extend(["--max-turns", str(config.max_turns)])
        if config.effort:
            cmd.extend(["--effort", config.effort])
        if config.json_schema:
            cmd.extend(["--json-schema", config.json_schema])
        if config.allowed_tools:
            for tool in config.allowed_tools:
                cmd.extend(["--allowedTools", tool])
        if config.disallowed_tools:
            for tool in config.disallowed_tools:
                cmd.extend(["--disallowedTools", tool])
        if config.permission_mode == "dangerously-skip-permissions":
            cmd.append("--dangerously-skip-permissions")
        elif config.permission_mode and config.permission_mode != "default":
            cmd.extend(["--permission-mode", config.permission_mode])
        if task.session_id:
            cmd.extend(["--resume", task.session_id])
        if config.mcp_config:
            cmd.extend(["--mcp-config", config.mcp_config])
        if config.add_dirs:
            for d in config.add_dirs:
                cmd.extend(["--add-dir", d])

        # Prompt is the last argument
        cmd.append(task.prompt)

        return cmd

    async def _read_pty_output(
        self,
        process: PTYProcess,
        task: Task,
        on_chunk: Callable[[StreamEvent], Awaitable[None]] | None,
    ) -> TaskResult:
        """Read PTY output, parse stream-json, handle timeouts."""
        loop = asyncio.get_running_loop()
        line_buffer = LineBuffer()
        result_text: str = ""
        stream_parts: list[str] = []
        usage: TokenUsage | None = None
        session_id: str | None = None
        done = asyncio.Event()

        timeout = task.timeout or DEFAULT_TIMEOUT
        deadline = time.monotonic() + timeout
        last_data_time = time.monotonic()

        def _on_readable() -> None:
            nonlocal last_data_time
            try:
                data = os.read(process.master_fd, READ_SIZE)
                if not data:
                    done.set()
                    return
                last_data_time = time.monotonic()
                line_buffer.feed(data)
            except OSError as e:
                if e.errno == errno.EIO:
                    # Normal PTY termination
                    done.set()
                else:
                    done.set()

        loop.add_reader(process.master_fd, _on_readable)

        try:
            while not done.is_set():
                # Calculate wait time
                now = time.monotonic()
                time_left = deadline - now
                idle_left = IDLE_TIMEOUT - (now - last_data_time)
                wait_time = min(time_left, idle_left, 1.0)

                if time_left <= 0:
                    await process.terminate()
                    raise TaskTimeoutError(f"Task {task.id} exceeded timeout of {timeout}s")

                if idle_left <= 0:
                    await process.terminate()
                    raise IdleTimeoutError(f"Task {task.id} no output for {IDLE_TIMEOUT}s")

                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(done.wait(), timeout=max(wait_time, 0.1))

                # Process buffered lines
                for line in line_buffer.get_lines():
                    parsed = parse_stream_json_line(line)
                    if parsed is None:
                        continue

                    # Normalize: single dict → list
                    events_list = parsed if isinstance(parsed, list) else [parsed]

                    for event_data in events_list:
                        event_type = event_data["type"]

                        if event_type == "text":
                            content = str(event_data["content"])
                            source = event_data.get("source")
                            stream_parts.append(content)
                            if source == "result":
                                result_text = content
                            if on_chunk:
                                try:
                                    await on_chunk(StreamEvent(type="text", text=content))
                                except Exception:
                                    logger.warning("on_chunk callback error", task_id=task.id, exc_info=True)

                        elif event_type == "cost":
                            usage = TokenUsage(
                                cost_usd=float(event_data.get("cost_usd", 0) or 0),
                                input_tokens=int(event_data.get("input_tokens", 0) or 0),
                                output_tokens=int(event_data.get("output_tokens", 0) or 0),
                                cache_read_tokens=int(event_data.get("cache_read_tokens", 0) or 0),
                                cache_write_tokens=int(event_data.get("cache_write_tokens", 0) or 0),
                                duration_ms=int(event_data.get("duration_ms", 0) or 0),
                            )
                            session_id = event_data.get("session_id") or session_id
                            if on_chunk:
                                try:
                                    await on_chunk(StreamEvent(type="cost", cost_usd=usage.cost_usd))
                                except Exception:
                                    logger.warning("on_chunk callback error", task_id=task.id, exc_info=True)

                        elif event_type == "retry":
                            if on_chunk:
                                try:
                                    await on_chunk(
                                        StreamEvent(
                                            type="retry",
                                            retry_info=str(event_data.get("error", "unknown")),
                                        )
                                    )
                                except Exception:
                                    logger.warning("on_chunk callback error", task_id=task.id, exc_info=True)

                        elif event_type == "tool_use":
                            if on_chunk:
                                try:
                                    await on_chunk(
                                        StreamEvent(
                                            type="tool_use",
                                            tool_name=event_data.get("tool_name"),
                                            tool_input=event_data.get("tool_input"),
                                        )
                                    )
                                except Exception:
                                    logger.warning("on_chunk callback error", task_id=task.id, exc_info=True)

                        elif event_type == "tool_result":
                            if on_chunk:
                                try:
                                    await on_chunk(
                                        StreamEvent(
                                            type="tool_result",
                                            tool_result=event_data.get("tool_result"),
                                            tool_is_error=event_data.get("tool_is_error"),
                                        )
                                    )
                                except Exception:
                                    logger.warning("on_chunk callback error", task_id=task.id, exc_info=True)

                        elif event_type == "thinking":
                            if on_chunk:
                                try:
                                    await on_chunk(
                                        StreamEvent(
                                            type="thinking",
                                            text=str(event_data.get("content", "")),
                                        )
                                    )
                                except Exception:
                                    logger.warning("on_chunk callback error", task_id=task.id, exc_info=True)

                        elif event_type == "init":
                            session_id = event_data.get("session_id") or session_id
                            if on_chunk:
                                try:
                                    await on_chunk(
                                        StreamEvent(
                                            type="init",
                                            model=event_data.get("model"),
                                            session_id=event_data.get("session_id"),
                                        )
                                    )
                                except Exception:
                                    logger.warning("on_chunk callback error", task_id=task.id, exc_info=True)

                        elif event_type == "progress":
                            if on_chunk:
                                try:
                                    await on_chunk(
                                        StreamEvent(
                                            type="progress",
                                            total_tokens=event_data.get("total_tokens"),
                                            tool_uses=event_data.get("tool_uses"),
                                            duration_ms=event_data.get("duration_ms"),
                                            description=event_data.get("description"),
                                            last_tool_name=event_data.get("last_tool_name"),
                                        )
                                    )
                                except Exception:
                                    logger.warning("on_chunk callback error", task_id=task.id, exc_info=True)

            # Process any remaining lines after done
            for line in line_buffer.get_lines():
                parsed = parse_stream_json_line(line)
                if parsed is None:
                    continue
                remaining_events = parsed if isinstance(parsed, list) else [parsed]
                for ev in remaining_events:
                    if ev["type"] == "text":
                        content = str(ev["content"])
                        stream_parts.append(content)
                        if ev.get("source") == "result":
                            result_text = content
                    elif ev["type"] == "cost":
                        usage = TokenUsage(
                            cost_usd=float(ev.get("cost_usd", 0) or 0),
                            input_tokens=int(ev.get("input_tokens", 0) or 0),
                            output_tokens=int(ev.get("output_tokens", 0) or 0),
                            cache_read_tokens=int(ev.get("cache_read_tokens", 0) or 0),
                            cache_write_tokens=int(ev.get("cache_write_tokens", 0) or 0),
                            duration_ms=int(ev.get("duration_ms", 0) or 0),
                        )
                        session_id = ev.get("session_id") or session_id
                    elif ev["type"] == "init":
                        session_id = ev.get("session_id") or session_id

            # Flush remaining buffer
            remaining = line_buffer.flush()
            if remaining:
                parsed = parse_stream_json_line(remaining)
                if parsed is not None:
                    flush_events = parsed if isinstance(parsed, list) else [parsed]
                    for ev in flush_events:
                        if ev["type"] == "text":
                            content = str(ev["content"])
                            stream_parts.append(content)
                            if ev.get("source") == "result":
                                result_text = content

        finally:
            loop.remove_reader(process.master_fd)

        # Wait for process exit
        exit_code = await self._wait_for_exit(process)
        stream = "\n".join(stream_parts)

        if exit_code != 0 and not stream.strip():
            logger.error(
                "executor.empty_result_nonzero_exit",
                task_id=task.id,
                exit_code=exit_code,
                hint="child process likely crashed before producing output (bad work_dir, missing claude binary, etc.)",
            )

        return TaskResult(
            result=result_text,
            stream=stream,
            exit_code=exit_code,
            session_id=session_id,
            usage=usage,
        )

    async def _wait_for_exit(self, process: PTYProcess, timeout: float = 5.0) -> int:
        """Wait for process to exit and return exit code."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                pid, status = os.waitpid(process.pid, os.WNOHANG)
                if pid != 0:
                    process._close_fd()
                    if os.WIFEXITED(status):
                        return os.WEXITSTATUS(status)
                    return -1
            except ChildProcessError:
                process._close_fd()
                return -1
            await asyncio.sleep(0.1)

        # Process didn't exit in time — terminate
        return await process.terminate()
