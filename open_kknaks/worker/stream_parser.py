"""Parse Claude Code stream-json output into typed events."""

import json
import re
from typing import Any

from open_kknaks.exceptions import BillingError, ClaudeAuthError

# ANSI escape sequence pattern
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def strip_ansi(text: str) -> str:
    """Remove ANSI escape codes from text."""
    return _ANSI_RE.sub("", text)


def parse_stream_json_line(line: str) -> dict[str, Any] | list[dict[str, Any]] | None:
    """Parse a single stream-json line from Claude Code CLI.

    Returns one of:
        {"type": "text", "content": str}
        {"type": "cost", "cost_usd": float, "input_tokens": int, ...}
        {"type": "retry", "error": str, "error_status": int | None, ...}
        {"type": "tool_use", "tool_name": str, "tool_input": dict}
        {"type": "tool_result", "tool_result": str, "tool_is_error": bool}
        {"type": "thinking", "content": str}
        {"type": "init", "model": str, "session_id": str}
        {"type": "progress", "total_tokens": int, "tool_uses": int, ...}
        list[dict] — when an assistant message contains multiple content blocks
        None — line to ignore (empty, malformed, or unrecognized type)

    Raises:
        BillingError: On billing_error (HTTP 402)
        ClaudeAuthError: On authentication_failed (HTTP 401)
    """
    line = strip_ansi(line.strip())
    if not line:
        return None

    try:
        obj = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return None

    if not isinstance(obj, dict):
        return None

    msg_type = obj.get("type", "")

    # --- Final result ---
    if msg_type == "result":
        result_text = obj.get("result", "")
        cost_usd = obj.get("cost_usd")
        usage = obj.get("usage", {})

        # Cost info (always emit if present)
        if cost_usd is not None or usage:
            return {
                "type": "cost",
                "cost_usd": cost_usd or 0.0,
                "input_tokens": usage.get("input_tokens", 0),
                "output_tokens": usage.get("output_tokens", 0),
                "cache_read_tokens": usage.get("cache_read_tokens", 0),
                "cache_write_tokens": usage.get("cache_write_tokens", 0),
                "duration_ms": obj.get("duration_ms", 0),
                "session_id": obj.get("session_id"),
            }

        # Text result
        if isinstance(result_text, str) and result_text.strip():
            return {"type": "text", "content": result_text.strip()}

    # --- Assistant message (intermediate output) ---
    elif msg_type == "assistant":
        content = obj.get("message", {}).get("content", [])
        events: list[dict[str, Any]] = []
        for block in content:
            if isinstance(block, dict):
                block_type = block.get("type", "")
                if block_type == "text":
                    text = block.get("text", "")
                    if text:
                        events.append({"type": "text", "content": text})
                elif block_type == "tool_use":
                    events.append({
                        "type": "tool_use",
                        "tool_name": block.get("name", ""),
                        "tool_input": block.get("input", {}),
                    })
                elif block_type == "thinking":
                    text = block.get("thinking", "") or block.get("text", "")
                    if text:
                        events.append({"type": "thinking", "content": text})
            elif isinstance(block, str) and block:
                events.append({"type": "text", "content": block})
        if not events:
            return None
        return events[0] if len(events) == 1 else events

    # --- Tool result (separate message) ---
    elif msg_type == "tool_result":
        content = obj.get("content", "")
        if isinstance(content, list):
            texts = [b.get("text", "") for b in content if isinstance(b, dict)]
            content = "\n".join(t for t in texts if t)
        return {
            "type": "tool_result",
            "tool_result": str(content) if content else "",
            "tool_is_error": obj.get("is_error", False),
        }

    # --- System events (retry, init, progress, errors) ---
    elif msg_type == "system":
        subtype = obj.get("subtype", "")

        if subtype == "api_retry":
            error = obj.get("error", "unknown")
            error_status = obj.get("error_status")

            # Fatal errors — raise immediately
            if error == "billing_error" or error_status == 402:
                raise BillingError(f"Billing error (HTTP 402): {error}")
            if error == "authentication_failed" or error_status == 401:
                raise ClaudeAuthError(f"Authentication failed (HTTP 401): {error}")

            return {
                "type": "retry",
                "error": error,
                "error_status": error_status,
                "attempt": obj.get("attempt", 0),
                "max_retries": obj.get("max_retries", 0),
                "retry_delay_ms": obj.get("retry_delay_ms", 0),
            }

        if subtype == "init":
            return {
                "type": "init",
                "model": obj.get("model", ""),
                "session_id": obj.get("session_id", ""),
            }

        if subtype == "task_progress":
            usage = obj.get("usage", {})
            return {
                "type": "progress",
                "total_tokens": usage.get("total_tokens", 0),
                "tool_uses": usage.get("tool_uses", 0),
                "duration_ms": usage.get("duration_ms", 0),
                "description": obj.get("description", ""),
                "last_tool_name": obj.get("last_tool_name", ""),
            }

    # --- Partial streaming (--include-partial-messages) ---
    elif msg_type == "stream_event":
        event = obj.get("event", {})
        event_type = event.get("type", "")
        if event_type == "content_block_delta":
            delta = event.get("delta", {})
            delta_type = delta.get("type", "")
            if delta_type == "text_delta":
                text = delta.get("text", "")
                if text:
                    return {"type": "text", "content": text}
            elif delta_type == "thinking_delta":
                text = delta.get("thinking", "") or delta.get("text", "")
                if text:
                    return {"type": "thinking", "content": text}

    return None
