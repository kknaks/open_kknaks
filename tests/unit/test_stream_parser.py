"""Tests for StreamParser."""

import json

import pytest

from open_kknaks.exceptions import BillingError, ClaudeAuthError
from open_kknaks.worker.stream_parser import parse_stream_json_line, strip_ansi


class TestStripAnsi:
    def test_no_ansi(self) -> None:
        assert strip_ansi("hello world") == "hello world"

    def test_color_codes(self) -> None:
        assert strip_ansi("\x1b[31mred\x1b[0m") == "red"

    def test_cursor_codes(self) -> None:
        assert strip_ansi("\x1b[2Jhello") == "hello"

    def test_empty_string(self) -> None:
        assert strip_ansi("") == ""


class TestParseResult:
    def test_result_with_cost(self) -> None:
        line = json.dumps(
            {
                "type": "result",
                "result": "done",
                "cost_usd": 0.015,
                "usage": {
                    "input_tokens": 500,
                    "output_tokens": 200,
                    "cache_read_tokens": 100,
                    "cache_write_tokens": 50,
                },
                "duration_ms": 8500,
                "session_id": "abc-123",
            }
        )
        parsed = parse_stream_json_line(line)
        assert parsed is not None
        assert parsed["type"] == "cost"
        assert parsed["cost_usd"] == 0.015
        assert parsed["input_tokens"] == 500
        assert parsed["output_tokens"] == 200
        assert parsed["cache_read_tokens"] == 100
        assert parsed["cache_write_tokens"] == 50
        assert parsed["duration_ms"] == 8500
        assert parsed["session_id"] == "abc-123"

    def test_result_text_only(self) -> None:
        line = json.dumps({"type": "result", "result": "analysis complete"})
        parsed = parse_stream_json_line(line)
        assert parsed is not None
        assert parsed["type"] == "text"
        assert parsed["content"] == "analysis complete"

    def test_result_empty_text_no_cost(self) -> None:
        line = json.dumps({"type": "result", "result": ""})
        assert parse_stream_json_line(line) is None


class TestParseAssistant:
    def test_text_block(self) -> None:
        line = json.dumps(
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "analyzing..."}]},
            }
        )
        parsed = parse_stream_json_line(line)
        assert parsed is not None
        assert parsed["type"] == "text"
        assert parsed["content"] == "analyzing..."

    def test_multiple_text_blocks(self) -> None:
        line = json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "text", "text": "line1"},
                        {"type": "text", "text": "line2"},
                    ]
                },
            }
        )
        parsed = parse_stream_json_line(line)
        assert parsed is not None
        assert parsed["content"] == "line1\nline2"

    def test_string_content(self) -> None:
        line = json.dumps(
            {
                "type": "assistant",
                "message": {"content": ["hello"]},
            }
        )
        parsed = parse_stream_json_line(line)
        assert parsed is not None
        assert parsed["content"] == "hello"

    def test_empty_content(self) -> None:
        line = json.dumps(
            {
                "type": "assistant",
                "message": {"content": []},
            }
        )
        assert parse_stream_json_line(line) is None


class TestParseSystemRetry:
    def test_rate_limit(self) -> None:
        line = json.dumps(
            {
                "type": "system",
                "subtype": "api_retry",
                "error": "rate_limit",
                "error_status": 429,
                "attempt": 1,
                "max_retries": 3,
                "retry_delay_ms": 5000,
            }
        )
        parsed = parse_stream_json_line(line)
        assert parsed is not None
        assert parsed["type"] == "retry"
        assert parsed["error"] == "rate_limit"
        assert parsed["error_status"] == 429
        assert parsed["attempt"] == 1
        assert parsed["retry_delay_ms"] == 5000

    def test_server_error(self) -> None:
        line = json.dumps(
            {
                "type": "system",
                "subtype": "api_retry",
                "error": "server_error",
                "error_status": 500,
                "attempt": 2,
            }
        )
        parsed = parse_stream_json_line(line)
        assert parsed is not None
        assert parsed["type"] == "retry"
        assert parsed["error"] == "server_error"


class TestFatalErrors:
    def test_billing_error_raises(self) -> None:
        line = json.dumps(
            {
                "type": "system",
                "subtype": "api_retry",
                "error": "billing_error",
                "error_status": 402,
            }
        )
        with pytest.raises(BillingError, match="402"):
            parse_stream_json_line(line)

    def test_billing_error_by_status(self) -> None:
        line = json.dumps(
            {
                "type": "system",
                "subtype": "api_retry",
                "error": "unknown",
                "error_status": 402,
            }
        )
        with pytest.raises(BillingError):
            parse_stream_json_line(line)

    def test_auth_error_raises(self) -> None:
        line = json.dumps(
            {
                "type": "system",
                "subtype": "api_retry",
                "error": "authentication_failed",
                "error_status": 401,
            }
        )
        with pytest.raises(ClaudeAuthError, match="401"):
            parse_stream_json_line(line)

    def test_auth_error_by_status(self) -> None:
        line = json.dumps(
            {
                "type": "system",
                "subtype": "api_retry",
                "error": "unknown",
                "error_status": 401,
            }
        )
        with pytest.raises(ClaudeAuthError):
            parse_stream_json_line(line)


class TestEdgeCases:
    def test_empty_line(self) -> None:
        assert parse_stream_json_line("") is None

    def test_whitespace_only(self) -> None:
        assert parse_stream_json_line("   ") is None

    def test_invalid_json(self) -> None:
        assert parse_stream_json_line("not json") is None

    def test_json_array(self) -> None:
        assert parse_stream_json_line("[1, 2, 3]") is None

    def test_unknown_type(self) -> None:
        line = json.dumps({"type": "unknown_event", "data": "something"})
        assert parse_stream_json_line(line) is None

    def test_system_non_retry_subtype(self) -> None:
        line = json.dumps({"type": "system", "subtype": "init", "session_id": "abc"})
        assert parse_stream_json_line(line) is None

    def test_ansi_in_text(self) -> None:
        line = json.dumps({"type": "result", "result": "clean text"})
        ansi_line = f"\x1b[32m{line}\x1b[0m"
        parsed = parse_stream_json_line(ansi_line)
        assert parsed is not None
        assert parsed["type"] == "text"
        assert parsed["content"] == "clean text"
