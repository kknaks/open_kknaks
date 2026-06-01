"""Codex headless runner adapter."""

import asyncio
import contextlib
import json
import shutil
import subprocess
from collections.abc import Awaitable, Callable
from typing import Any

from open_kknaks.config import ClaudeConfig
from open_kknaks.constants import PROVIDER_CODEX
from open_kknaks.task import StreamEvent, Task, TaskResult, TokenUsage

CODEX_ALLOWED_PROVIDER_OPTIONS = frozenset(
    {
        "json",
        "output_last_message",
        "output_schema",
        "ephemeral",
        "skip_git_repo_check",
        "ignore_user_config",
        "ignore_rules",
        "strict_config",
        "color",
        "sandbox",
        "bypass_approvals_and_sandbox",
        "bypass_hook_trust",
        "profile",
        "profile_v2",
        "add_dirs",
        "images",
        "oss",
        "local_provider",
        "config",
        "enable",
        "disable",
    }
)


class CodexRunnerAdapter:
    """Codex provider adapter using `codex exec --json` JSONL output."""

    provider = PROVIDER_CODEX

    def __init__(self, codex_bin: str = "codex") -> None:
        self.codex_bin = codex_bin
        self._active: dict[str, asyncio.subprocess.Process] = {}

    def health_check(self) -> dict[str, str]:
        path = shutil.which(self.codex_bin)
        if not path:
            return {"codex": "not_found", "codex_version": "", "codex_path": ""}

        try:
            result = subprocess.run(
                [self.codex_bin, "--version"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            version = result.stdout.strip() if result.returncode == 0 else "unknown"
        except Exception:
            version = "unknown"

        return {"codex": "ok", "codex_version": version, "codex_path": path}

    def _validate_options(self, task: Task) -> str | None:
        unknown = set(task.provider_options) - CODEX_ALLOWED_PROVIDER_OPTIONS
        if unknown:
            return f"Unsupported codex provider_options: {', '.join(sorted(unknown))}"

        if task.provider_options.get("json") is False:
            return "codex provider_options.json=false is not supported"

        resume = task.options.get("resume", {})
        if not isinstance(resume, dict):
            return "options.resume must be an object"

        mode = resume.get("mode", "new")
        if mode not in {"new", "session", "last"}:
            return f"Unsupported codex resume.mode: {mode}"
        if mode == "session" and not resume.get("session_id"):
            return "options.resume.session_id is required when resume.mode=session"
        if mode != "new" and task.provider_options.get("ephemeral") is True:
            return "provider_options.ephemeral=true cannot be used with resume"

        return None

    def _build_command(self, task: Task) -> list[str]:
        error = self._validate_options(task)
        if error:
            raise ValueError(error)

        resume = task.options.get("resume", {})
        mode = resume.get("mode", "new") if isinstance(resume, dict) else "new"

        cmd = [self.codex_bin, "exec"]
        if mode == "session":
            cmd.extend(["resume", str(resume["session_id"])])
        elif mode == "last":
            cmd.extend(["resume", "--last"])

        cmd.append("--json")

        if task.model:
            cmd.extend(["--model", task.model])

        cwd = task.options.get("cwd")
        if isinstance(cwd, str) and cwd:
            cmd.extend(["--cd", cwd])

        provider_options = dict(task.provider_options)
        sandbox = provider_options.pop("sandbox", "workspace-write")
        if sandbox:
            cmd.extend(["--sandbox", str(sandbox)])

        if provider_options.pop("output_last_message", None):
            cmd.extend(["--output-last-message", str(task.provider_options["output_last_message"])])
        if provider_options.pop("output_schema", None):
            cmd.extend(["--output-schema", str(task.provider_options["output_schema"])])

        bool_flags = {
            "ephemeral": "--ephemeral",
            "skip_git_repo_check": "--skip-git-repo-check",
            "ignore_user_config": "--ignore-user-config",
            "ignore_rules": "--ignore-rules",
            "strict_config": "--strict-config",
            "bypass_approvals_and_sandbox": "--dangerously-bypass-approvals-and-sandbox",
            "bypass_hook_trust": "--dangerously-bypass-hook-trust",
            "oss": "--oss",
        }
        for key, flag in bool_flags.items():
            if provider_options.pop(key, False):
                cmd.append(flag)

        value_flags = {
            "color": "--color",
            "profile": "--profile",
            "profile_v2": "--profile-v2",
            "local_provider": "--local-provider",
        }
        for key, flag in value_flags.items():
            value = provider_options.pop(key, None)
            if value:
                cmd.extend([flag, str(value)])

        repeated_flags = {
            "add_dirs": "--add-dir",
            "images": "--image",
            "config": "--config",
            "enable": "--enable",
            "disable": "--disable",
        }
        for key, flag in repeated_flags.items():
            values = provider_options.pop(key, None)
            if isinstance(values, str):
                values = [values]
            if values:
                for value in values:
                    cmd.extend([flag, str(value)])

        provider_options.pop("json", None)

        cmd.append(task.prompt)
        return cmd

    def _usage_from_event(self, event: dict[str, Any]) -> TokenUsage | None:
        usage = event.get("usage")
        if not isinstance(usage, dict):
            return None
        return TokenUsage(
            input_tokens=int(usage.get("input_tokens", 0) or 0),
            output_tokens=int(usage.get("output_tokens", 0) or 0),
            cache_read_tokens=int(usage.get("cache_read_tokens", 0) or 0),
            cache_write_tokens=int(usage.get("cache_write_tokens", 0) or 0),
            duration_ms=int(usage.get("duration_ms", 0) or 0),
        )

    def _events_from_item(self, event: dict[str, Any]) -> list[StreamEvent]:
        item = event.get("item")
        if not isinstance(item, dict):
            return [StreamEvent(type="progress", description=str(event.get("type", "")))]
        item_type = item.get("type")
        if item_type == "agent_message":
            return [StreamEvent(type="text", text=str(item.get("text") or ""))]
        if item_type == "reasoning":
            return [StreamEvent(type="thinking", text=str(item.get("text") or item.get("summary") or ""))]

        details = item.get("details")
        if not isinstance(details, dict):
            return [StreamEvent(type="progress", description=str(event.get("type", "")))]

        detail_type = details.get("type")
        if detail_type == "agent_message":
            text = details.get("text") or details.get("message") or ""
            return [StreamEvent(type="text", text=str(text))]
        if detail_type == "reasoning":
            text = details.get("text") or details.get("summary") or ""
            return [StreamEvent(type="thinking", text=str(text))]
        if detail_type in {"command_execution", "mcp_tool_call", "collab_tool_call", "web_search"}:
            name = details.get("name") or details.get("command") or detail_type
            if event.get("type") == "item.started":
                return [StreamEvent(type="tool_use", tool_name=str(name), tool_input=details)]
            result = details.get("output") or details.get("result") or details.get("text") or ""
            return [StreamEvent(type="tool_result", tool_result=str(result), tool_is_error=details.get("is_error"))]
        if detail_type in {"file_change", "todo_list"}:
            return [StreamEvent(type="progress", description=str(detail_type))]

        return [StreamEvent(type="progress", description=str(detail_type or event.get("type", "")))]

    def _parse_json_event(
        self,
        event: dict[str, Any],
    ) -> tuple[list[StreamEvent], str | None, TokenUsage | None, str | None]:
        event_type = event.get("type")
        if event_type == "thread.started":
            thread_id = event.get("thread_id")
            event_session_id = str(thread_id) if thread_id else None
            return [StreamEvent(type="init", session_id=event_session_id)], None, None, event_session_id
        if event_type == "turn.started":
            return [StreamEvent(type="progress", description="turn.started")], None, None, None
        if event_type == "turn.completed":
            usage = self._usage_from_event(event)
            events = [StreamEvent(type="cost", cost_usd=usage.cost_usd if usage else 0.0)]
            return events, None, usage, None
        if event_type in {"turn.failed", "error"}:
            return [], None, None, json.dumps(event, ensure_ascii=False)
        if event_type in {"item.started", "item.updated", "item.completed"}:
            events = self._events_from_item(event)
            result_text = None
            for stream_event in events:
                if stream_event.type == "text" and stream_event.text:
                    result_text = stream_event.text
            return events, result_text, None, None
        return [StreamEvent(type="progress", description=str(event_type))], None, None, None

    async def execute(
        self,
        task: Task,
        config: ClaudeConfig,
        on_chunk: Callable[[StreamEvent], Awaitable[None]] | None = None,
    ) -> TaskResult:
        del config
        error = self._validate_options(task)
        if error:
            return TaskResult(exit_code=1, debug_context=error)

        cmd = self._build_command(task)
        timeout_option = task.options.get("timeout_sec")
        timeout = timeout_option if isinstance(timeout_option, int) else None

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            return TaskResult(exit_code=127, debug_context=str(exc))

        self._active[task.id] = process
        result_text = ""
        stream_parts: list[str] = []
        debug_lines: list[str] = []
        usage: TokenUsage | None = None
        session_id: str | None = None

        async def publish(event: StreamEvent) -> None:
            if on_chunk:
                await on_chunk(event)

        async def read_stdout() -> None:
            nonlocal result_text, usage, session_id
            assert process.stdout is not None
            while True:
                raw = await process.stdout.readline()
                if not raw:
                    return
                line = raw.decode(errors="replace").strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    debug_lines.append(line)
                    del debug_lines[:-20]
                    continue
                if not isinstance(event, dict):
                    continue
                stream_events, event_result, event_usage, event_session_id = self._parse_json_event(event)
                if event_result:
                    result_text = event_result
                    stream_parts.append(event_result)
                if event_usage:
                    usage = event_usage
                if event_session_id:
                    session_id = event_session_id
                for stream_event in stream_events:
                    await publish(stream_event)

        async def read_stderr() -> None:
            assert process.stderr is not None
            while True:
                raw = await process.stderr.readline()
                if not raw:
                    return
                line = raw.decode(errors="replace").strip()
                if line:
                    debug_lines.append(line)
                    del debug_lines[:-20]

        try:
            await asyncio.wait_for(asyncio.gather(read_stdout(), read_stderr(), process.wait()), timeout=timeout)
            exit_code = process.returncode or 0
        except TimeoutError:
            process.terminate()
            with contextlib.suppress(ProcessLookupError):
                await asyncio.wait_for(process.wait(), timeout=2.0)
            exit_code = -1
            debug_lines.append(f"codex task timed out after {timeout}s")
        finally:
            self._active.pop(task.id, None)

        return TaskResult(
            result=result_text,
            stream="\n".join(stream_parts),
            exit_code=exit_code,
            session_id=session_id,
            usage=usage,
            debug_context="\n".join(debug_lines) if debug_lines else None,
        )

    async def cancel(self, task_id: str) -> bool:
        process = self._active.pop(task_id, None)
        if process is None:
            return False
        process.terminate()
        return True

    async def cleanup_all(self) -> int:
        processes = list(self._active.values())
        self._active.clear()
        for process in processes:
            process.terminate()
        return len(processes)

    def active_task_ids(self) -> list[str]:
        return list(self._active.keys())
