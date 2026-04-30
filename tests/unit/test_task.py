"""Tests for Task and related data models."""

from datetime import datetime, timezone

from open_kknaks.task import Priority, StreamEvent, Task, TaskResult, TaskStatus, TokenUsage


class TestTaskStatus:
    def test_values(self) -> None:
        assert TaskStatus.PENDING == "pending"
        assert TaskStatus.RUNNING == "running"
        assert TaskStatus.DONE == "done"
        assert TaskStatus.FAILED == "failed"
        assert TaskStatus.CANCELLED == "cancelled"
        assert TaskStatus.RETRYING == "retrying"

    def test_is_str(self) -> None:
        assert isinstance(TaskStatus.PENDING, str)


class TestPriority:
    def test_values(self) -> None:
        assert Priority.HIGH == 1
        assert Priority.NORMAL == 5
        assert Priority.LOW == 9

    def test_ordering(self) -> None:
        assert Priority.HIGH < Priority.NORMAL < Priority.LOW

    def test_is_int(self) -> None:
        assert isinstance(Priority.HIGH, int)


class TestTokenUsage:
    def test_defaults(self) -> None:
        usage = TokenUsage()
        assert usage.cost_usd == 0.0
        assert usage.input_tokens == 0
        assert usage.output_tokens == 0
        assert usage.cache_read_tokens == 0
        assert usage.cache_write_tokens == 0
        assert usage.duration_ms == 0

    def test_with_values(self) -> None:
        usage = TokenUsage(cost_usd=0.05, input_tokens=100, output_tokens=200, duration_ms=1500)
        assert usage.cost_usd == 0.05
        assert usage.input_tokens == 100
        assert usage.output_tokens == 200
        assert usage.duration_ms == 1500


class TestStreamEvent:
    def test_text_event(self) -> None:
        event = StreamEvent(type="text", text="hello")
        assert event.type == "text"
        assert event.text == "hello"
        assert event.cost_usd is None

    def test_cost_event(self) -> None:
        event = StreamEvent(type="cost", cost_usd=0.01)
        assert event.type == "cost"
        assert event.cost_usd == 0.01
        assert event.text is None

    def test_retry_event(self) -> None:
        event = StreamEvent(type="retry", retry_info="rate limited")
        assert event.type == "retry"
        assert event.retry_info == "rate limited"

    def test_tool_use_event(self) -> None:
        event = StreamEvent(type="tool_use", tool_name="Bash", tool_input={"command": "ls"})
        assert event.type == "tool_use"
        assert event.tool_name == "Bash"
        assert event.tool_input == {"command": "ls"}

    def test_tool_result_event(self) -> None:
        event = StreamEvent(type="tool_result", tool_result="file1.txt", tool_is_error=False)
        assert event.type == "tool_result"
        assert event.tool_result == "file1.txt"
        assert event.tool_is_error is False

    def test_thinking_event(self) -> None:
        event = StreamEvent(type="thinking", text="Let me analyze...")
        assert event.type == "thinking"
        assert event.text == "Let me analyze..."

    def test_init_event(self) -> None:
        event = StreamEvent(type="init", model="claude-sonnet-4-20250514", session_id="sess-123")
        assert event.type == "init"
        assert event.model == "claude-sonnet-4-20250514"
        assert event.session_id == "sess-123"

    def test_progress_event(self) -> None:
        event = StreamEvent(
            type="progress",
            total_tokens=50594,
            tool_uses=42,
            duration_ms=46332,
            description="Reading ~/file.py",
            last_tool_name="Read",
        )
        assert event.type == "progress"
        assert event.total_tokens == 50594
        assert event.tool_uses == 42
        assert event.duration_ms == 46332
        assert event.description == "Reading ~/file.py"
        assert event.last_tool_name == "Read"


class TestTaskResult:
    def test_defaults(self) -> None:
        result = TaskResult()
        assert result.result == ""
        assert result.stream == ""
        assert result.exit_code == 0
        assert result.session_id is None
        assert result.usage is None

    def test_with_usage(self) -> None:
        usage = TokenUsage(cost_usd=0.1, input_tokens=500)
        result = TaskResult(result="done", stream="done", exit_code=0, session_id="sess-1", usage=usage)
        assert result.usage is not None
        assert result.usage.cost_usd == 0.1
        assert result.result == "done"
        assert result.stream == "done"


class TestTask:
    def test_minimal_creation(self) -> None:
        task = Task(prompt="do something")
        assert task.prompt == "do something"
        assert task.status == "pending"
        assert task.priority == 5
        assert task.queue == "default"
        assert task.id  # uuid generated

    def test_enum_values_serialized(self) -> None:
        task = Task(prompt="test", status=TaskStatus.RUNNING, priority=Priority.HIGH)
        assert task.status == "running"
        assert task.priority == 1

    def test_datetime_uses_utc(self) -> None:
        task = Task(prompt="test")
        assert task.created_at.tzinfo is not None
        assert task.created_at.tzinfo == timezone.utc

    def test_json_roundtrip(self) -> None:
        task = Task(
            prompt="test prompt",
            status=TaskStatus.RUNNING,
            priority=Priority.HIGH,
            model="opus",
            max_retries=3,
            metadata={"key": "value", "count": 42},
        )
        json_str = task.model_dump_json()
        restored = Task.model_validate_json(json_str)
        assert restored.prompt == task.prompt
        assert restored.status == "running"
        assert restored.priority == 1
        assert restored.model == "opus"
        assert restored.max_retries == 3
        assert restored.metadata == {"key": "value", "count": 42}
        assert restored.created_at == task.created_at

    def test_json_roundtrip_with_usage(self) -> None:
        usage = TokenUsage(cost_usd=0.05, input_tokens=100)
        task = Task(prompt="test", usage=usage)
        json_str = task.model_dump_json()
        restored = Task.model_validate_json(json_str)
        assert restored.usage is not None
        assert restored.usage.cost_usd == 0.05

    def test_json_roundtrip_with_datetime(self) -> None:
        now = datetime.now(timezone.utc)
        task = Task(prompt="test", started_at=now, finished_at=now)
        json_str = task.model_dump_json()
        restored = Task.model_validate_json(json_str)
        assert restored.started_at == now
        assert restored.finished_at == now

    def test_exception_type_field(self) -> None:
        task = Task(prompt="test", exception_type="BillingError")
        assert task.exception_type == "BillingError"
        json_str = task.model_dump_json()
        restored = Task.model_validate_json(json_str)
        assert restored.exception_type == "BillingError"

    def test_optional_fields_none(self) -> None:
        task = Task(prompt="test")
        assert task.context is None
        assert task.model is None
        assert task.delay_until is None
        assert task.allowed_tools is None
        assert task.result is None
        assert task.usage is None

    def test_list_fields(self) -> None:
        task = Task(prompt="test", allowed_tools=["bash", "read"], add_dirs=["/tmp"])
        assert task.allowed_tools == ["bash", "read"]
        assert task.add_dirs == ["/tmp"]

    def test_metadata_default_empty(self) -> None:
        task = Task(prompt="test")
        assert task.metadata == {}

    def test_unique_ids(self) -> None:
        t1 = Task(prompt="a")
        t2 = Task(prompt="b")
        assert t1.id != t2.id
