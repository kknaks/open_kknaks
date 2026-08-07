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

# `codex exec resume` accepts a strict subset of `codex exec` flags. Passing any of these
# makes the CLI exit 2 with "unexpected argument" before the session is even loaded, so
# they are dropped in resume mode rather than forwarded. (Measured on codex-cli 0.146.0;
# `--model`, `--skip-git-repo-check`, `--ephemeral`, `--config`, `--image`,
# `--output-last-message`, `--output-schema` and the remaining options *are* accepted.)
CODEX_RESUME_UNSUPPORTED_OPTIONS = frozenset(
    {"sandbox", "add_dirs", "color", "profile", "profile_v2", "local_provider", "oss"}
)

# Codex item types that map to tool_use / tool_result stream events.
CODEX_TOOL_ITEM_TYPES = frozenset({"command_execution", "mcp_tool_call", "collab_tool_call", "web_search"})


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
        is_resume = mode in {"session", "last"}

        cmd = [self.codex_bin, "exec"]
        if mode == "session":
            cmd.extend(["resume", str(resume["session_id"])])
        elif mode == "last":
            cmd.extend(["resume", "--last"])

        cmd.append("--json")

        if task.model:
            cmd.extend(["--model", task.model])

        cwd = task.options.get("cwd")
        # `--cd` is rejected by the resume subcommand; a resumed session keeps its own cwd.
        if isinstance(cwd, str) and cwd and not is_resume:
            cmd.extend(["--cd", cwd])

        provider_options = dict(task.provider_options)
        if is_resume:
            for key in CODEX_RESUME_UNSUPPORTED_OPTIONS:
                provider_options.pop(key, None)

        # In resume mode the key is already gone and the default is empty, so no
        # `--sandbox` is emitted; a resumed session reuses the sandbox it was created with.
        sandbox = provider_options.pop("sandbox", "" if is_resume else "workspace-write")
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
        """Map a `turn.completed` usage payload onto the shared TokenUsage model.

        codex reports (measured on codex-cli 0.146.0)::

            {"input_tokens": 15059, "cached_input_tokens": 11008,
             "cache_write_input_tokens": 0, "output_tokens": 5,
             "reasoning_output_tokens": 0}

        The old `cache_read_tokens` / `cache_write_tokens` lookups matched none of these,
        so cache accounting silently reported 0. The legacy names are kept as fallbacks
        so a codex build that emits them still works.

        Note the counts are *inclusive*: `cached_input_tokens` is part of `input_tokens`
        and `reasoning_output_tokens` is part of `output_tokens` — do not add them up.
        """
        usage = event.get("usage")
        if not isinstance(usage, dict):
            return None

        def _int(*keys: str) -> int:
            for key in keys:
                value = usage.get(key)
                if value is not None:
                    return int(value)
            return 0

        return TokenUsage(
            input_tokens=_int("input_tokens"),
            output_tokens=_int("output_tokens"),
            cache_read_tokens=_int("cached_input_tokens", "cache_read_tokens"),
            cache_write_tokens=_int("cache_write_input_tokens", "cache_write_tokens"),
            reasoning_output_tokens=_int("reasoning_output_tokens"),
            duration_ms=_int("duration_ms"),
        )

    def _tool_is_error(self, payload: dict[str, Any]) -> bool | None:
        if "is_error" in payload:
            is_error = payload["is_error"]
            return bool(is_error) if is_error is not None else None
        exit_code = payload.get("exit_code")
        if isinstance(exit_code, int):
            return exit_code != 0
        status = payload.get("status")
        if isinstance(status, str) and status in {"failed", "error"}:
            return True
        return None

    def _events_from_item(self, event: dict[str, Any]) -> list[StreamEvent]:
        item = event.get("item")
        if not isinstance(item, dict):
            return [StreamEvent(type="progress", description=str(event.get("type", "")))]

        # `item.id` is stable across item.started / item.updated / item.completed for the
        # same item, so it is what pairs a tool_use with its tool_result.
        item_id = item.get("id")
        tool_use_id = str(item_id) if item_id else None

        # codex >= 0.146 emits a flat item ({"id", "type", "command", ...}); older builds
        # nest the same payload under `item.details`. Support both.
        details = item.get("details")
        payload = details if isinstance(details, dict) else item
        payload_type = payload.get("type")

        if payload_type == "agent_message":
            return [StreamEvent(type="text", text=str(payload.get("text") or payload.get("message") or ""))]
        if payload_type == "reasoning":
            return [StreamEvent(type="thinking", text=str(payload.get("text") or payload.get("summary") or ""))]
        if payload_type in CODEX_TOOL_ITEM_TYPES:
            event_type = event.get("type")
            if event_type == "item.started":
                # mcp_tool_call 은 실제 툴명이 `tool` 에 실린다 — 타입명 폴백은 소비자의
                # 카탈로그 표시명 매칭을 전부 깨뜨린다(2026-08-06 mediness P8 실측).
                name = payload.get("name") or payload.get("tool") or payload.get("command") or payload_type
                return [
                    StreamEvent(
                        type="tool_use",
                        tool_name=str(name),
                        tool_input=payload,
                        tool_use_id=tool_use_id,
                    )
                ]
            if event_type == "item.completed":
                result = (
                    payload.get("aggregated_output")
                    or payload.get("output")
                    or payload.get("result")
                    or payload.get("text")
                    or ""
                )
                return [
                    StreamEvent(
                        type="tool_result",
                        tool_result=str(result),
                        tool_is_error=self._tool_is_error(payload),
                        tool_use_id=tool_use_id,
                    )
                ]
            # item.updated is an in-progress snapshot, not a result — do not emit a
            # second tool_result under the same tool_use_id.
            return [StreamEvent(type="progress", description=str(payload_type), tool_use_id=tool_use_id)]
        if payload_type in {"file_change", "todo_list"}:
            return [StreamEvent(type="progress", description=str(payload_type))]

        return [StreamEvent(type="progress", description=str(payload_type or event.get("type", "")))]

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
                # MCP tool 결과가 JSONL 한 줄로 오므로 asyncio 기본 64KiB 줄 한계를 넘기면
                # readline() 이 ValueError("Separator is found, but chunk is longer than limit") 로
                # 태스크를 죽인다(2026-08-06 mediness P8 실측 — 태스크 목록 결과에서 재현).
                limit=10 * 1024 * 1024,
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
