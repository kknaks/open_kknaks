"""Tests for MCP schema-only surface."""

from open_kknaks.mcp import server as mcp_server


def test_submit_task_schema_uses_provider_contract() -> None:
    tool = mcp_server._submit_task_tool()
    props = tool.inputSchema["properties"]

    assert props["provider"]["enum"] == ["claude", "codex"]
    assert "options" in props
    assert "provider_options" in props
    assert "system_prompt" not in props
    assert "allowed_tools" not in props


def test_schema_only_tool_count() -> None:
    tools = [
        mcp_server._submit_task_tool(),
        mcp_server._get_task_tool(),
        mcp_server._get_status_tool(),
        mcp_server._get_result_tool(),
        mcp_server._cancel_task_tool(),
        mcp_server._submit_batch_tool(),
        mcp_server._get_batch_status_tool(),
        mcp_server._wait_batch_tool(),
        mcp_server._queue_size_tool(),
        mcp_server._list_dlq_tool(),
        mcp_server._retry_from_dlq_tool(),
        mcp_server._purge_dlq_tool(),
        mcp_server._get_cost_tool(),
    ]
    assert len(tools) == 13
