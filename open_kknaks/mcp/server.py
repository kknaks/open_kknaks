"""MCP server for open_kknaks task queue."""

from mcp.server import Server
from mcp.types import TextContent, Tool

from open_kknaks.broker.base import AbstractBroker
from open_kknaks.client import ClaudeClient


def create_server(broker: AbstractBroker) -> Server:
    """Create an MCP server with open_kknaks tools."""
    server = Server("open_kknaks")

    @server.list_tools()  # type: ignore[no-untyped-call,untyped-decorator]
    async def list_tools() -> list[Tool]:
        return [
            Tool(
                name="submit_task",
                description="Submit a task to the Claude Code queue",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "prompt": {"type": "string", "description": "Task prompt"},
                        "queue": {"type": "string", "description": "Queue name (default: 'default')"},
                        "model": {"type": "string", "description": "Claude model override"},
                        "timeout": {"type": "integer", "description": "Timeout in seconds"},
                    },
                    "required": ["prompt"],
                },
            ),
            Tool(
                name="get_status",
                description="Get task status",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "task_id": {"type": "string", "description": "Task ID"},
                    },
                    "required": ["task_id"],
                },
            ),
            Tool(
                name="get_result",
                description="Get task result (waits for completion)",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "task_id": {"type": "string", "description": "Task ID"},
                        "timeout": {"type": "integer", "description": "Wait timeout in seconds"},
                    },
                    "required": ["task_id"],
                },
            ),
            Tool(
                name="cancel_task",
                description="Cancel a running task",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "task_id": {"type": "string", "description": "Task ID"},
                    },
                    "required": ["task_id"],
                },
            ),
        ]

    @server.call_tool()  # type: ignore[untyped-decorator]
    async def call_tool(name: str, arguments: dict[str, object]) -> list[TextContent]:
        client = ClaudeClient(broker=broker)

        if name == "submit_task":
            task_id = await client.submit(
                prompt=str(arguments["prompt"]),
                queue=str(arguments.get("queue", "default")),
                model=str(arguments["model"]) if arguments.get("model") else None,
                timeout=int(str(arguments["timeout"])) if arguments.get("timeout") else None,
            )
            return [TextContent(type="text", text=f"Task submitted: {task_id}")]

        if name == "get_status":
            status = await client.status(str(arguments["task_id"]))
            return [TextContent(type="text", text=f"Status: {status or 'not found'}")]

        if name == "get_result":
            timeout = float(str(arguments.get("timeout", 600)))
            task = await client.result(str(arguments["task_id"]), timeout=timeout)
            if task and task.result:
                return [TextContent(type="text", text=task.result)]
            return [TextContent(type="text", text="No result available")]

        if name == "cancel_task":
            success = await client.cancel(str(arguments["task_id"]))
            text = "Cancelled" if success else "Task not found"
            return [TextContent(type="text", text=text)]

        return [TextContent(type="text", text=f"Unknown tool: {name}")]

    return server
