"""MCP server for open_kknaks task queue."""

import json

from mcp.server import Server
from mcp.types import TextContent, Tool

from open_kknaks.batch import BatchRunner
from open_kknaks.broker.base import AbstractBroker
from open_kknaks.client import ClaudeClient


def _task_to_json(task: "open_kknaks.task.Task") -> str:  # type: ignore[name-defined]  # noqa: F821
    """Serialize a Task to a JSON string with all relevant fields."""
    data: dict[str, object] = {
        "id": task.id,
        "prompt": task.prompt,
        "queue": task.queue,
        "status": task.status,
        "priority": task.priority,
        "result": task.result,
        "error": task.error,
        "exit_code": task.exit_code,
        "retry_count": task.retry_count,
        "max_retries": task.max_retries,
        "created_at": task.created_at.isoformat(),
    }
    if task.context:
        data["context"] = task.context
    if task.model:
        data["model"] = task.model
    if task.batch_id:
        data["batch_id"] = task.batch_id
    if task.metadata:
        data["metadata"] = task.metadata
    if task.result_session_id:
        data["session_id"] = task.result_session_id
    if task.started_at:
        data["started_at"] = task.started_at.isoformat()
    if task.finished_at:
        data["finished_at"] = task.finished_at.isoformat()
    if task.usage:
        data["usage"] = task.usage.model_dump()
    return json.dumps(data, ensure_ascii=False)


# ─── Tool definitions ───


def _submit_task_tool() -> Tool:
    return Tool(
        name="submit_task",
        description=(
            "Submit a task to the open_kknaks Claude Code task queue. "
            "The task is enqueued and executed asynchronously by a Worker process "
            "running Claude Code CLI via PTY. Returns a task_id (UUID) for tracking. "
            "Use get_task, get_status, or get_result to monitor progress."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": (
                        "The prompt to send to Claude Code CLI. "
                        "This is the main instruction for the task — equivalent to "
                        "running `claude -p '<prompt>'` on the command line."
                    ),
                },
                "context": {
                    "type": "string",
                    "description": (
                        "Additional context prepended before the prompt. "
                        "Useful for providing background information, file contents, "
                        "or prior conversation context without mixing it into the prompt itself."
                    ),
                },
                "queue": {
                    "type": "string",
                    "description": (
                        "Queue name to submit to. Workers subscribe to specific queues, "
                        "allowing task routing (e.g., 'default', 'high-priority', 'gpu'). "
                        "Default: 'default'."
                    ),
                },
                "priority": {
                    "type": "integer",
                    "description": (
                        "Task priority. Lower value = higher priority. "
                        "1=HIGH (processed first), 5=NORMAL (default), 9=LOW (processed last). "
                        "Tasks in the same queue are dequeued in priority order."
                    ),
                    "enum": [1, 5, 9],
                },
                "delay_seconds": {
                    "type": "integer",
                    "description": (
                        "Delay execution by this many seconds. The task enters a delayed set "
                        "and is automatically moved to the main queue after the delay expires. "
                        "Useful for scheduling tasks in the future."
                    ),
                },
                "timeout": {
                    "type": "integer",
                    "description": (
                        "Maximum execution time in seconds. If the Claude Code process "
                        "exceeds this duration, it is terminated via SIGHUP→SIGTERM→SIGKILL. "
                        "Default is determined by the Worker configuration."
                    ),
                },
                "max_retries": {
                    "type": "integer",
                    "description": (
                        "Maximum number of retry attempts on failure. "
                        "When a task fails and retry_count < max_retries, it is re-enqueued. "
                        "After exhausting retries, the task moves to the Dead Letter Queue (DLQ). "
                        "Default: 0 (no retries)."
                    ),
                },
                "model": {
                    "type": "string",
                    "description": (
                        "Claude model to use (overrides Worker default). "
                        "Maps to `claude --model <model>`. "
                        "Examples: 'claude-sonnet-4-5-20250514', 'claude-opus-4-0-20250514'. "
                        "If not set, the Worker's ClaudeConfig.model is used."
                    ),
                },
                "system_prompt": {
                    "type": "string",
                    "description": (
                        "Custom system prompt that replaces the default Claude Code system prompt. "
                        "Maps to `claude --system-prompt '<text>'`. "
                        "Use this when you need full control over the system instruction."
                    ),
                },
                "append_system_prompt": {
                    "type": "string",
                    "description": (
                        "Text appended to the default system prompt (does not replace it). "
                        "Maps to `claude --append-system-prompt '<text>'`. "
                        "Useful for adding project-specific instructions while keeping "
                        "Claude Code's built-in capabilities."
                    ),
                },
                "max_turns": {
                    "type": "integer",
                    "description": (
                        "Maximum number of agentic turns (tool-use rounds) allowed. "
                        "Maps to `claude --max-turns <n>`. "
                        "Limits how many times Claude can invoke tools before returning. "
                        "Useful for controlling cost and execution time."
                    ),
                },
                "effort": {
                    "type": "string",
                    "description": (
                        "Thinking effort level. Controls depth of reasoning. "
                        "Maps to `claude --effort <level>`. "
                        "Values: 'low', 'medium', 'high'. Higher effort = more tokens = better results."
                    ),
                    "enum": ["low", "medium", "high"],
                },
                "json_schema": {
                    "type": "string",
                    "description": (
                        "JSON Schema string for structured output. "
                        "Maps to `claude --output-format json --json-schema '<schema>'`. "
                        "When provided, Claude's response is constrained to match this schema. "
                        "Must be a valid JSON Schema as a string."
                    ),
                },
                "allowed_tools": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Explicit list of tools Claude is allowed to use. "
                        "Maps to `claude --allowedTools '<tool1>,<tool2>,...'`. "
                        "Examples: ['Bash', 'Read', 'Write', 'Edit', 'Glob', 'Grep']. "
                        "If not set, all tools available in the Worker's environment are allowed."
                    ),
                },
                "disallowed_tools": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "List of tools Claude is NOT allowed to use. "
                        "Maps to `claude --disallowedTools '<tool1>,<tool2>,...'`. "
                        "Takes precedence over allowed_tools."
                    ),
                },
                "permission_mode": {
                    "type": "string",
                    "description": (
                        "Permission prompt mode for tool execution. "
                        "Maps to `claude --permission-mode <mode>`. "
                        "Values: 'default' (prompt user), 'plan' (allow read, block write), "
                        "'bypasstool' (auto-approve all tools). "
                        "Workers typically run with 'bypasstool' for unattended execution."
                    ),
                },
                "session_id": {
                    "type": "string",
                    "description": (
                        "Resume a previous Claude Code session by its ID. "
                        "Maps to `claude --session-id <id>`. "
                        "The new task continues in the context of the prior session, "
                        "preserving conversation history and tool state."
                    ),
                },
                "mcp_config": {
                    "type": "string",
                    "description": (
                        "Path to an MCP (Model Context Protocol) configuration JSON file. "
                        "Maps to `claude --mcp-config <path>`. "
                        "Configures additional MCP servers available to Claude during execution."
                    ),
                },
                "add_dirs": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Additional directories Claude can access beyond the Worker's work_dir. "
                        "Maps to `claude --add-dir <dir>` (repeated for each). "
                        "Useful for multi-repo tasks or accessing shared libraries."
                    ),
                },
                "metadata": {
                    "type": "object",
                    "additionalProperties": True,
                    "description": (
                        "Arbitrary key-value metadata attached to the task. "
                        "Not sent to Claude — stored alongside the task for your own tracking. "
                        "Values can be string, number, boolean, or null. "
                        'Example: {"team": "backend", "ticket": "PROJ-123"}.'
                    ),
                },
            },
            "required": ["prompt"],
        },
    )


def _get_task_tool() -> Tool:
    return Tool(
        name="get_task",
        description=(
            "Get full details of a task by its ID. Returns a JSON object with all task fields: "
            "id, prompt, queue, status, priority, result, error, exit_code, retry_count, "
            "max_retries, created_at, started_at, finished_at, model, batch_id, metadata, "
            "session_id, and usage (cost_usd, input_tokens, output_tokens, duration_ms). "
            "Unlike get_status (status only) or get_result (waits for completion), "
            "this returns the full snapshot immediately without blocking."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "UUID of the task returned by submit_task or submit_batch.",
                },
            },
            "required": ["task_id"],
        },
    )


def _get_status_tool() -> Tool:
    return Tool(
        name="get_status",
        description=(
            "Get the current status of a task by its ID. "
            "Returns one of: 'pending' (queued, waiting for a Worker), "
            "'running' (being executed by a Worker), "
            "'done' (completed successfully), "
            "'failed' (execution failed after all retries), "
            "'cancelled' (cancelled via cancel_task), "
            "'retrying' (failed but will be retried). "
            "Returns 'not found' if the task_id does not exist."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "UUID of the task returned by submit_task.",
                },
            },
            "required": ["task_id"],
        },
    )


def _get_result_tool() -> Tool:
    return Tool(
        name="get_result",
        description=(
            "Wait for a task to complete and return its full result as JSON. "
            "If the task is already done/failed/cancelled, returns immediately. "
            "Otherwise, blocks (via Redis Stream subscription, not polling) until "
            "the task finishes or the timeout expires. "
            "Response JSON includes: status, result (output text), error, exit_code, "
            "session_id (for resuming with submit_task), and usage "
            "(cost_usd, input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, duration_ms)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "UUID of the task returned by submit_task.",
                },
                "timeout": {
                    "type": "integer",
                    "description": (
                        "Maximum seconds to wait for the task to complete. "
                        "If the task is still running after this duration, returns the "
                        "current state without waiting further. Default: 600 (10 minutes)."
                    ),
                },
            },
            "required": ["task_id"],
        },
    )


def _cancel_task_tool() -> Tool:
    return Tool(
        name="cancel_task",
        description=(
            "Cancel a task. If the task is pending (not yet picked up by a Worker), "
            "it is immediately marked as cancelled. If running, the Worker will "
            "terminate the Claude Code process (SIGHUP→SIGTERM→SIGKILL) on its next check. "
            "Returns 'Cancelled' on success, 'Task not found' if the task_id does not exist. "
            "Cancelled tasks cannot be resumed — submit a new task instead."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "UUID of the task to cancel.",
                },
            },
            "required": ["task_id"],
        },
    )


def _submit_batch_tool() -> Tool:
    return Tool(
        name="submit_batch",
        description=(
            "Submit multiple tasks as a batch. All tasks are enqueued atomically and "
            "share a batch_id for group tracking. Returns a JSON object with batch_id "
            "and an ordered list of task_ids. "
            "Use get_batch_status to monitor overall progress, or get_task/get_result "
            "for individual tasks. Batch mode is 'parallel' (all enqueued at once)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "prompts": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "prompt": {
                                "type": "string",
                                "description": "The prompt for this task.",
                            },
                            "context": {
                                "type": "string",
                                "description": "Optional context for this task.",
                            },
                        },
                        "required": ["prompt"],
                    },
                    "description": (
                        "List of task definitions. Each must have a 'prompt' key, "
                        "and optionally a 'context' key. "
                        'Example: [{"prompt": "Fix bug in auth.py"}, {"prompt": "Add tests for utils.py"}].'
                    ),
                },
                "queue": {
                    "type": "string",
                    "description": "Queue name for all tasks in the batch. Default: 'default'.",
                },
            },
            "required": ["prompts"],
        },
    )


def _get_batch_status_tool() -> Tool:
    return Tool(
        name="get_batch_status",
        description=(
            "Get the aggregate status of a batch of tasks. "
            "Returns one of: 'pending' (no tasks started), "
            "'running' (some tasks in progress), "
            "'completed' (all tasks done successfully), "
            "'partial_failure' (mix of done and failed), "
            "'failed' (all tasks failed). "
            "Requires both batch_id and the list of task_ids returned by submit_batch."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "batch_id": {
                    "type": "string",
                    "description": "UUID of the batch returned by submit_batch.",
                },
                "task_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of task UUIDs returned by submit_batch.",
                },
            },
            "required": ["batch_id", "task_ids"],
        },
    )


def _wait_batch_tool() -> Tool:
    return Tool(
        name="wait_batch",
        description=(
            "Wait for all tasks in a batch to complete and return their results. "
            "Blocks until every task reaches a terminal state (done/failed/cancelled) "
            "or the timeout expires. Returns a JSON array of task objects, "
            "each with id, status, result, error, exit_code, and usage. "
            "If timeout expires, returns whatever tasks have completed plus "
            "the current state of still-running tasks."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "task_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of task UUIDs returned by submit_batch.",
                },
                "timeout": {
                    "type": "integer",
                    "description": (
                        "Maximum seconds to wait for all tasks. Default: 3600 (1 hour). "
                        "Batch tasks can take longer than individual tasks."
                    ),
                },
            },
            "required": ["task_ids"],
        },
    )


def _queue_size_tool() -> Tool:
    return Tool(
        name="queue_size",
        description=(
            "Get the number of pending tasks in a queue. "
            "Returns the count of tasks waiting to be picked up by Workers. "
            "Does not include tasks that are currently running, completed, or in the DLQ. "
            "Useful for monitoring queue backlog and deciding whether to scale Workers."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "queue_name": {
                    "type": "string",
                    "description": "Name of the queue to check (e.g., 'default').",
                },
            },
            "required": ["queue_name"],
        },
    )


def _list_dlq_tool() -> Tool:
    return Tool(
        name="list_dlq",
        description=(
            "List tasks in the Dead Letter Queue (DLQ). "
            "Tasks end up in the DLQ when they fail after exhausting all retry attempts, "
            "or when a non-retryable error occurs (e.g., BillingError, AuthError). "
            "Returns a JSON array of task objects with id, status, prompt (truncated), "
            "error, retry_count, and created_at. Use retry_from_dlq to re-enqueue specific tasks."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "queue_name": {
                    "type": "string",
                    "description": "Name of the queue whose DLQ to inspect (e.g., 'default').",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of DLQ tasks to return. Default: 100.",
                },
            },
            "required": ["queue_name"],
        },
    )


def _retry_from_dlq_tool() -> Tool:
    return Tool(
        name="retry_from_dlq",
        description=(
            "Move a task from the Dead Letter Queue back to the main queue for re-execution. "
            "The task's retry_count is preserved — it will be processed as a fresh attempt. "
            "Use list_dlq first to find task IDs. "
            "Returns 'Retried' on success."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "queue_name": {
                    "type": "string",
                    "description": "Name of the queue (e.g., 'default').",
                },
                "task_id": {
                    "type": "string",
                    "description": "UUID of the DLQ task to retry.",
                },
            },
            "required": ["queue_name", "task_id"],
        },
    )


def _purge_dlq_tool() -> Tool:
    return Tool(
        name="purge_dlq",
        description=(
            "Delete ALL tasks from a queue's Dead Letter Queue. "
            "This is irreversible — purged tasks cannot be recovered. "
            "Use list_dlq first to review what will be deleted. "
            "Returns the number of tasks purged."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "queue_name": {
                    "type": "string",
                    "description": "Name of the queue whose DLQ to purge (e.g., 'default').",
                },
            },
            "required": ["queue_name"],
        },
    )


def _get_cost_tool() -> Tool:
    return Tool(
        name="get_cost",
        description=(
            "[DEPRECATED — will be removed in a future release. "
            "Anthropic is transitioning to subscription-based billing, "
            "making per-request cost tracking unreliable.] "
            "Get cumulative cost information. Returns a JSON object with total_cost_usd "
            "(namespace-wide total across all workers) and optionally worker_cost_usd "
            "(cost for a specific worker). Costs are accumulated from Claude Code API usage "
            "tracked by the CostMiddleware. All values are in USD."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "worker_id": {
                    "type": "string",
                    "description": (
                        "Optional: specific worker ID to get per-worker cost. "
                        "If omitted, only the global total is returned."
                    ),
                },
            },
            "required": [],
        },
    )


def create_server(broker: AbstractBroker) -> Server:
    """Create an MCP server with open_kknaks tools."""
    server = Server("open_kknaks")

    @server.list_tools()  # type: ignore[no-untyped-call,untyped-decorator]
    async def list_tools() -> list[Tool]:
        return [
            _submit_task_tool(),
            _get_task_tool(),
            _get_status_tool(),
            _get_result_tool(),
            _cancel_task_tool(),
            _submit_batch_tool(),
            _get_batch_status_tool(),
            _wait_batch_tool(),
            _queue_size_tool(),
            _list_dlq_tool(),
            _retry_from_dlq_tool(),
            _purge_dlq_tool(),
            _get_cost_tool(),
        ]

    @server.call_tool()  # type: ignore[untyped-decorator]
    async def call_tool(name: str, arguments: dict[str, object]) -> list[TextContent]:
        client = ClaudeClient(broker=broker)

        # ─── Task: submit ───

        if name == "submit_task":
            task_id = await client.submit(
                prompt=str(arguments["prompt"]),
                context=str(arguments["context"]) if arguments.get("context") else None,
                queue=str(arguments.get("queue", "default")),
                priority=int(str(arguments["priority"])) if arguments.get("priority") else 5,
                delay_seconds=int(str(arguments["delay_seconds"])) if arguments.get("delay_seconds") else None,
                timeout=int(str(arguments["timeout"])) if arguments.get("timeout") else None,
                max_retries=int(str(arguments["max_retries"])) if arguments.get("max_retries") else 0,
                model=str(arguments["model"]) if arguments.get("model") else None,
                system_prompt=str(arguments["system_prompt"]) if arguments.get("system_prompt") else None,
                append_system_prompt=(
                    str(arguments["append_system_prompt"]) if arguments.get("append_system_prompt") else None
                ),
                max_turns=int(str(arguments["max_turns"])) if arguments.get("max_turns") else None,
                effort=str(arguments["effort"]) if arguments.get("effort") else None,
                json_schema=str(arguments["json_schema"]) if arguments.get("json_schema") else None,
                allowed_tools=(
                    [str(t) for t in arguments["allowed_tools"]]  # type: ignore[attr-defined]
                    if arguments.get("allowed_tools")
                    else None
                ),
                disallowed_tools=(
                    [str(t) for t in arguments["disallowed_tools"]]  # type: ignore[attr-defined]
                    if arguments.get("disallowed_tools")
                    else None
                ),
                permission_mode=str(arguments["permission_mode"]) if arguments.get("permission_mode") else None,
                session_id=str(arguments["session_id"]) if arguments.get("session_id") else None,
                mcp_config=str(arguments["mcp_config"]) if arguments.get("mcp_config") else None,
                add_dirs=(
                    [str(d) for d in arguments["add_dirs"]]  # type: ignore[attr-defined]
                    if arguments.get("add_dirs")
                    else None
                ),
                metadata=(
                    {str(k): v for k, v in arguments["metadata"].items()}  # type: ignore[attr-defined]
                    if arguments.get("metadata")
                    else None
                ),
            )
            return [TextContent(type="text", text=f"Task submitted: {task_id}")]

        # ─── Task: get full details ───

        if name == "get_task":
            task = await broker.get_task(str(arguments["task_id"]))
            if task is None:
                return [TextContent(type="text", text="Task not found")]
            return [TextContent(type="text", text=_task_to_json(task))]

        # ─── Task: status ───

        if name == "get_status":
            status = await client.status(str(arguments["task_id"]))
            return [TextContent(type="text", text=f"Status: {status or 'not found'}")]

        # ─── Task: result (blocking wait) ───

        if name == "get_result":
            timeout = float(str(arguments.get("timeout", 600)))
            task = await client.result(str(arguments["task_id"]), timeout=timeout)
            if task is None:
                return [TextContent(type="text", text="Task not found")]
            return [TextContent(type="text", text=_task_to_json(task))]

        # ─── Task: cancel ───

        if name == "cancel_task":
            success = await client.cancel(str(arguments["task_id"]))
            text = "Cancelled" if success else "Task not found"
            return [TextContent(type="text", text=text)]

        # ─── Batch: submit ───

        if name == "submit_batch":
            batch = BatchRunner(broker=broker)
            prompts_raw = arguments["prompts"]
            prompts: list[dict[str, str]] = []
            for p in prompts_raw:  # type: ignore[attr-defined]
                item: dict[str, str] = {"prompt": str(p["prompt"])}
                ctx = p.get("context")
                if ctx:
                    item["context"] = str(ctx)
                prompts.append(item)
            queue = str(arguments.get("queue", "default"))
            batch_id, task_ids = await batch.submit_batch(prompts, queue=queue)
            return [
                TextContent(
                    type="text",
                    text=json.dumps({"batch_id": batch_id, "task_ids": task_ids}),
                )
            ]

        # ─── Batch: status ───

        if name == "get_batch_status":
            batch = BatchRunner(broker=broker)
            task_ids = [str(t) for t in arguments["task_ids"]]  # type: ignore[attr-defined]
            status = await batch.get_batch_status(str(arguments["batch_id"]), task_ids)
            return [TextContent(type="text", text=f"Batch status: {status}")]

        # ─── Batch: wait ───

        if name == "wait_batch":
            batch = BatchRunner(broker=broker)
            task_ids = [str(t) for t in arguments["task_ids"]]  # type: ignore[attr-defined]
            timeout = float(str(arguments.get("timeout", 3600)))
            tasks = await batch.wait_batch(task_ids, timeout=timeout)
            results = [json.loads(_task_to_json(t)) for t in tasks]
            return [TextContent(type="text", text=json.dumps(results, ensure_ascii=False))]

        # ─── Queue: size ───

        if name == "queue_size":
            count = await broker.queue_size(str(arguments["queue_name"]))
            return [TextContent(type="text", text=json.dumps({"queue": str(arguments["queue_name"]), "size": count}))]

        # ─── DLQ: list ───

        if name == "list_dlq":
            limit = int(str(arguments.get("limit", 100)))
            tasks = await broker.list_dlq(str(arguments["queue_name"]), limit=limit)
            dlq_items = []
            for t in tasks:
                dlq_items.append(
                    {
                        "id": t.id,
                        "status": t.status,
                        "prompt": t.prompt[:200],
                        "error": t.error,
                        "retry_count": t.retry_count,
                        "created_at": t.created_at.isoformat(),
                    }
                )
            return [TextContent(type="text", text=json.dumps(dlq_items, ensure_ascii=False))]

        # ─── DLQ: retry ───

        if name == "retry_from_dlq":
            await broker.retry_from_dlq(str(arguments["queue_name"]), str(arguments["task_id"]))
            return [TextContent(type="text", text="Retried")]

        # ─── DLQ: purge ───

        if name == "purge_dlq":
            tasks_before = await broker.list_dlq(str(arguments["queue_name"]), limit=100000)
            count = len(tasks_before)
            await broker.purge_dlq(str(arguments["queue_name"]))
            return [TextContent(type="text", text=f"Purged {count} tasks from DLQ")]

        # ─── Cost ───

        if name == "get_cost":
            total = await broker.get_total_cost()
            result: dict[str, object] = {"total_cost_usd": total}
            if arguments.get("worker_id"):
                worker_cost = await broker.get_worker_cost(str(arguments["worker_id"]))
                result["worker_id"] = str(arguments["worker_id"])
                result["worker_cost_usd"] = worker_cost
            return [TextContent(type="text", text=json.dumps(result))]

        return [TextContent(type="text", text=f"Unknown tool: {name}")]

    return server
