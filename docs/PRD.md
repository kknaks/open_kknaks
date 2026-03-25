# open_kknaks — PRD (Product Requirements Document)

> **Version:** 1.0
> **Created:** 2026-03-25
> **Author:** 기획자 에이전트
> **Status:** Draft

---

## 1. 개요

### 1.1 한 줄 정의

Claude Code CLI를 실행하는 **전용 태스크 큐 라이브러리** — Dramatiq 구조를 차용하되, 워커의 작업이 Claude Code CLI 호출로 고정된 Python async 패키지.

### 1.2 문제 정의

| 문제 | 설명 |
|---|---|
| 반복 보일러플레이트 | Claude Code CLI를 subprocess/pty로 호출하는 코드를 매번 작성해야 함 |
| 큐잉/동시성 부재 | 여러 작업을 순차/병렬 처리하려면 자체 큐 로직 필요 |
| 스트리밍 어려움 | CLI 출력을 실시간 청크로 전달하려면 pty 파싱 + pub/sub 구현 필요 |
| 재시도/우선순위 없음 | 실패 복구, 우선순위 큐, 지연 실행 등 운영 기능이 없음 |
| 통합 불편 | MCP 클라이언트(Claude Desktop 등)에서 Claude Code를 원격 실행할 표준 인터페이스 없음 |

### 1.3 해결 방향

- **라이브러리 레벨** — 프레임워크가 아닌, `pip install`로 끝나는 라이브러리
- **단일 책임** — Claude Code CLI 실행 + 큐 관리 + 결과 반환만 담당. 트리거(webhook, cron 등)는 유저 코드에서 붙임
- **Dramatiq 패턴** — Broker ↔ Worker ↔ Runner 분리, 브로커 추상화, 미들웨어 파이프라인
- **PyPI 배포** — `open-kknaks`로 배포, Python 3.10+

### 1.4 타겟 유저

| 유저 | 시나리오 |
|---|---|
| **백엔드 개발자** | 서버 이벤트(에러 로그, Jira 이슈) → Claude Code 자동 분석 파이프라인 구축 |
| **DevOps 엔지니어** | CI 실패 → Claude Code 원인 분석 → PR 코멘트 자동화 |
| **AI 도구 빌더** | MCP 서버로 노출하여 Claude Desktop에서 원격 Claude Code 호출 |
| **솔로 개발자** | 로컬에서 여러 작업을 큐에 넣고 병렬 처리 |

---

## 2. Dramatiq 구조 매핑

Dramatiq의 핵심 개념을 open_kknaks에 1:1 대응시킨다.

| Dramatiq | open_kknaks | 차이점 |
|---|---|---|
| `@dramatiq.actor` | (없음 — 고정 actor) | 실행 함수가 Claude Code CLI 호출로 고정. 유저가 actor를 정의하지 않음 |
| `Broker` | `AbstractBroker` | 동일한 역할. InMemory / Redis 기본 제공 |
| `Worker` | `Worker` | 큐에서 작업을 꺼내 Claude Code CLI 실행. 동시성(concurrency) 제어 포함 |
| `Message` | `Task` | 작업 단위. prompt + context + 메타데이터 |
| `Middleware` | `Middleware` | 동일 패턴. 작업 전/후 훅 (로깅, 비용 추적 등) |
| `send()` / `send_with_options()` | `runner.submit()` | 작업 등록 진입점 |
| `message.get_result()` | `runner.result()` | 결과 조회 |
| `GroupCallback` | `batch_submit()` | 배치 작업 |
| Result Backend | Broker에 통합 | 별도 result backend 없이 브로커가 결과도 저장 |

### 2.1 Dramatiq에 없는 추가 기능

| 기능 | 설명 |
|---|---|
| **실시간 스트리밍** | pty 기반 청크 스트리밍 (async generator) |
| **MCP 서버** | 라이브러리 자체를 MCP 서버로 노출 |
| **Claude Code 전용 옵션** | `--model`, `--allowedTools`, `--append-system-prompt` 등 CLI 플래그 매핑 |
| **세션 관리** | `--continue`, `--resume` 기반 대화 이어가기 |
| **비용 추적** | stream-json 이벤트에서 토큰 사용량 파싱 |

---

## 3. 아키텍처

### 3.1 전체 흐름

```
유저 코드
  │
  ├─ runner.submit(prompt, context, ...)
  │
  ▼
Middleware Pipeline (before_enqueue)
  │
  ▼
Broker (InMemory / Redis)
  │  enqueue → 우선순위 큐
  │
  ▼
Worker (N개 동시 실행)
  │  dequeue → Middleware(before_process)
  │
  ▼
ClaudeCodeExecutor
  │  claude -p "prompt" --output-format stream-json ...
  │  (asyncio subprocess, pty spawn)
  │
  ├─ 청크 → broker.publish_chunk(task_id, chunk)
  │         → 유저: runner.stream(task_id)
  │
  ▼
Middleware Pipeline (after_process / after_skip)
  │
  ▼
결과 저장 (broker 내장)
  │
  ▼
유저: runner.result(task_id) / runner.status(task_id)
```

### 3.2 컴포넌트 상세

#### 3.2.1 ClaudeRunner (client.py) — 유저 진입점

유저가 직접 다루는 유일한 객체. 내부적으로 Broker + Worker를 조립한다.

```python
from open_kknaks import ClaudeRunner
from open_kknaks.broker import RedisBroker

runner = ClaudeRunner(
    work_dir="/my/project",              # Claude Code 작업 디렉토리
    claude_bin=None,                      # None이면 PATH 자동 탐색
    model=None,                           # 기본 모델 (--model)
    allowed_tools=None,                   # 기본 허용 도구 (--allowedTools)
    append_system_prompt=None,            # 추가 시스템 프롬프트
    max_turns=None,                       # 최대 에이전트 턴 수
    permission_mode="default",            # default / plan / bypassPermissions
    bare=True,                            # --bare 모드 (기본 활성화, 스크립트 권장)
    timeout=600,                          # 기본 타임아웃 (초)
    max_retries=0,                        # 기본 재시도 횟수
    broker=RedisBroker("redis://localhost:6379"),
    middlewares=[],                        # 미들웨어 리스트
    concurrency=4,                        # 워커 동시 실행 수
)
```

**메서드:**

| 메서드 | 설명 |
|---|---|
| `submit(prompt, *, context, priority, delay_seconds, timeout, max_retries, session_id, continue_session, model, allowed_tools, append_system_prompt, max_turns, permission_mode, bare, metadata) → str` | 작업 등록 → task_id 반환 |
| `stream(task_id) → AsyncIterator[StreamEvent]` | 실시간 청크 스트리밍 |
| `status(task_id) → TaskStatus` | 작업 상태 조회 |
| `result(task_id, *, timeout) → TaskResult` | 완료 대기 + 결과 반환 |
| `cancel(task_id) → bool` | 실행 중 작업 취소 (SIGTERM → SIGKILL) |
| `retry(task_id) → str` | 실패 작업 재시도 → 새 task_id 반환 |
| `batch_submit(tasks, *, mode) → str` | 배치 작업 등록 → batch_id 반환 |
| `batch_status(batch_id) → BatchStatus` | 배치 상태 조회 |
| `batch_stream(batch_id) → AsyncIterator[StreamEvent]` | 배치 내 모든 작업 스트리밍 |
| `batch_wait(batch_id, *, timeout) → list[TaskResult]` | 배치 완료 대기 |
| `batch_cancel(batch_id) → bool` | 배치 전체 취소 |
| `start() → None` | 워커 시작 (submit 시 자동 호출, 명시적 호출 가능) |
| `stop() → None` | 워커 그레이스풀 종료 |
| `__aenter__ / __aexit__` | async context manager 지원 |

#### 3.2.2 Task (task.py) — 작업 단위

```python
from enum import Enum
from dataclasses import dataclass, field
from datetime import datetime

class TaskStatus(Enum):
    PENDING = "pending"          # 큐 대기
    RUNNING = "running"          # 실행 중
    DONE = "done"                # 성공 완료
    FAILED = "failed"            # 실패
    CANCELLED = "cancelled"      # 취소됨
    RETRYING = "retrying"        # 재시도 대기

class Priority(Enum):
    HIGH = 1
    NORMAL = 5
    LOW = 9

@dataclass
class Task:
    id: str                                # UUID
    prompt: str                            # Claude Code에 보낼 프롬프트
    context: str | None = None             # 추가 컨텍스트 (stdin 파이프)
    status: TaskStatus = TaskStatus.PENDING
    priority: Priority = Priority.NORMAL
    result: str | None = None              # 완료 시 결과 텍스트
    error: str | None = None               # 실패 시 에러 메시지
    exit_code: int | None = None           # CLI 종료 코드
    session_id: str | None = None          # Claude Code 세션 ID (이어가기용)
    batch_id: str | None = None            # 배치 소속 시
    
    # 실행 옵션 (Task별 오버라이드)
    work_dir: str | None = None
    model: str | None = None
    allowed_tools: list[str] | None = None
    append_system_prompt: str | None = None
    max_turns: int | None = None
    permission_mode: str | None = None
    bare: bool | None = None
    timeout: int | None = None
    max_retries: int = 0
    retry_count: int = 0
    delay_until: datetime | None = None    # 지연 실행
    metadata: dict = field(default_factory=dict)  # 유저 커스텀 메타

    # 타임스탬프
    created_at: datetime = field(default_factory=datetime.utcnow)
    started_at: datetime | None = None
    finished_at: datetime | None = None

    # 비용/토큰 (stream-json 파싱)
    usage: TokenUsage | None = None

@dataclass
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float | None = None          # 계산 가능 시

@dataclass
class TaskResult:
    task_id: str
    status: TaskStatus
    result: str | None
    error: str | None
    exit_code: int | None
    session_id: str | None
    usage: TokenUsage | None
    duration_seconds: float | None
    metadata: dict
```

#### 3.2.3 Broker (broker/) — 큐 + 결과 저장

**AbstractBroker 인터페이스:**

```python
class AbstractBroker(ABC):
    # 큐 관리
    async def enqueue(self, task: Task) -> None: ...
    async def dequeue(self, timeout: float = 0) -> Task | None: ...
    async def acknowledge(self, task_id: str) -> None: ...
    
    # 상태/결과
    async def get_task(self, task_id: str) -> Task | None: ...
    async def update_task(self, task: Task) -> None: ...
    
    # 스트리밍
    async def publish_chunk(self, task_id: str, chunk: StreamEvent) -> None: ...
    async def subscribe_chunks(self, task_id: str) -> AsyncIterator[StreamEvent]: ...
    
    # 라이프사이클
    async def connect(self) -> None: ...
    async def close(self) -> None: ...
    async def flush(self) -> None: ...    # 테스트용 전체 삭제
```

**InMemoryBroker** — 개발/테스트용, asyncio.PriorityQueue + dict 기반. 단일 프로세스 한정.

**RedisBroker** — 운영용.

| 기능 | Redis 구조 |
|---|---|
| 큐 | Sorted Set (`{prefix}:queue`) — score = priority + timestamp |
| 작업 상태/결과 | Hash (`{prefix}:task:{id}`) + TTL |
| 스트리밍 | Redis Streams (`{prefix}:stream:{id}`) |
| 배치 | Set (`{prefix}:batch:{id}`) — 소속 task_id 목록 |
| 딜레이 큐 | Sorted Set (`{prefix}:delayed`) — score = delay_until timestamp |
| 락 | `{prefix}:lock:{id}` — 중복 실행 방지 |

```python
class RedisBroker(AbstractBroker):
    def __init__(
        self,
        url: str = "redis://localhost:6379",
        password: str | None = None,
        key_prefix: str = "open_kknaks",  # 네임스페이스 분리
        result_ttl: int = 3600,            # 결과 보관 시간 (초)
        stream_maxlen: int = 1000,         # 스트림 최대 길이
    ): ...
```

#### 3.2.4 Worker (worker.py) — 큐 소비 + CLI 실행

```python
class Worker:
    def __init__(
        self,
        broker: AbstractBroker,
        executor: ClaudeCodeExecutor,
        concurrency: int = 4,             # 동시 실행 수
        poll_interval: float = 0.1,       # 큐 폴링 간격 (초)
        middlewares: list[Middleware] = [],
    ): ...

    async def start(self) -> None: ...     # 워커 루프 시작
    async def stop(self) -> None: ...      # 그레이스풀 종료
    async def _process_task(self, task: Task) -> None: ...
```

- `concurrency`개의 asyncio Task를 동시 실행
- 각 task를 dequeue → middleware(before_process) → executor 실행 → middleware(after_process) → 결과 저장
- 실패 시: retry_count < max_retries면 상태를 RETRYING으로 변경 후 재큐잉
- 취소 시: 실행 중인 subprocess에 SIGTERM 전송, 5초 후 SIGKILL

#### 3.2.5 ClaudeCodeExecutor (runner/claude.py) — CLI 실행 엔진

실제 Claude Code CLI를 subprocess로 호출하는 핵심 컴포넌트.

```python
class ClaudeCodeExecutor:
    def __init__(
        self,
        claude_bin: str | None = None,    # None이면 shutil.which("claude")
        default_work_dir: str = ".",
        default_model: str | None = None,
        default_allowed_tools: list[str] | None = None,
        default_append_system_prompt: str | None = None,
        default_max_turns: int | None = None,
        default_permission_mode: str = "default",
        default_bare: bool = True,
    ): ...

    async def execute(
        self,
        task: Task,
        on_chunk: Callable[[StreamEvent], Awaitable[None]] | None = None,
    ) -> TaskResult: ...
```

**CLI 빌드 로직:**

Task의 필드를 Claude Code CLI 플래그로 변환한다.

```python
def _build_command(self, task: Task) -> list[str]:
    cmd = [self.claude_bin, "-p", task.prompt]
    cmd += ["--output-format", "stream-json"]
    cmd += ["--verbose"]
    
    if task.bare or (task.bare is None and self.default_bare):
        cmd.append("--bare")
    if task.model or self.default_model:
        cmd += ["--model", task.model or self.default_model]
    if task.allowed_tools or self.default_allowed_tools:
        tools = task.allowed_tools or self.default_allowed_tools
        cmd += ["--allowedTools"] + tools
    if task.append_system_prompt or self.default_append_system_prompt:
        cmd += ["--append-system-prompt",
                task.append_system_prompt or self.default_append_system_prompt]
    if task.max_turns or self.default_max_turns:
        cmd += ["--max-turns", str(task.max_turns or self.default_max_turns)]
    if task.permission_mode == "bypassPermissions":
        cmd.append("--dangerously-skip-permissions")
    if task.session_id:
        cmd += ["--resume", task.session_id]
    
    return cmd
```

**실행 방식:**

1. `asyncio.create_subprocess_exec()` 로 Claude Code CLI 실행
2. `--output-format stream-json` 출력을 줄 단위로 파싱
3. 각 JSON 이벤트를 `StreamEvent`로 변환하여 `on_chunk` 콜백 호출
4. context가 있으면 stdin으로 파이프 (`echo context | claude -p "prompt"`)
5. 프로세스 종료 시 exit_code + 최종 result 추출
6. timeout 초과 시 SIGTERM → 5초 대기 → SIGKILL

**StreamEvent 타입:**

```python
@dataclass
class StreamEvent:
    task_id: str
    type: str               # "text_delta" | "tool_use" | "tool_result" | "result" | "error" | "system"
    data: dict              # 원본 stream-json 이벤트
    text: str | None = None # text_delta일 때 텍스트 조각
    timestamp: datetime = field(default_factory=datetime.utcnow)
```

#### 3.2.6 Middleware (middleware.py) — 작업 전/후 훅

Dramatiq의 미들웨어 패턴을 그대로 차용.

```python
class Middleware(ABC):
    async def before_enqueue(self, task: Task) -> Task | None:
        """큐에 넣기 전. None 반환 시 작업 취소."""
        return task

    async def after_enqueue(self, task: Task) -> None:
        """큐에 넣은 후."""
        pass

    async def before_process(self, task: Task) -> Task | None:
        """실행 전. None 반환 시 스킵."""
        return task

    async def after_process(self, task: Task, result: TaskResult) -> None:
        """실행 완료 후."""
        pass

    async def after_skip(self, task: Task) -> None:
        """스킵된 작업 후."""
        pass

    async def on_failure(self, task: Task, error: Exception) -> None:
        """실패 시."""
        pass
```

**기본 제공 미들웨어:**

| 미들웨어 | 설명 |
|---|---|
| `LoggingMiddleware` | 작업 시작/완료/실패 로깅 (structlog 기반) |
| `RetryMiddleware` | 실패 시 자동 재시도 (max_retries, 지수 백오프) |
| `TimeoutMiddleware` | 작업별 타임아웃 관리 |
| `CostTrackingMiddleware` | stream-json에서 토큰 사용량 파싱 + 비용 계산 |
| `RateLimitMiddleware` | 분당 최대 요청 수 제한 (API rate limit 방어) |
| `CallbackMiddleware` | 완료/실패 시 유저 정의 콜백 실행 (webhook, Slack 등) |

### 3.3 MCP 서버 (mcp/)

라이브러리 자체를 MCP 서버로 노출한다. Claude Desktop 등 MCP 클라이언트에서 Claude Code를 원격 태스크 큐로 사용할 수 있게 한다.

```
MCP Client (Claude Desktop, Cursor, etc.)
    │ MCP 프로토콜 (stdio / SSE)
    ▼
open_kknaks MCP Server
    │
    ▼
ClaudeRunner → Worker → Claude Code CLI
```

**노출 MCP Tool:**

| Tool | 파라미터 | 설명 |
|---|---|---|
| `submit_task` | prompt, context?, priority?, model?, allowed_tools?, timeout? | 작업 등록 → task_id |
| `get_status` | task_id | 상태 조회 |
| `get_result` | task_id, wait? | 결과 조회 (wait=true면 완료 대기) |
| `cancel_task` | task_id | 취소 |
| `stream_task` | task_id | 스트리밍 (SSE 지원 시) |
| `submit_batch` | tasks[], mode? | 배치 등록 → batch_id |
| `get_batch_status` | batch_id | 배치 상태 |
| `get_batch_result` | batch_id, wait? | 배치 결과 |
| `list_tasks` | status?, limit? | 작업 목록 조회 |

**MCP 서버 실행:**

```python
from open_kknaks.mcp import MCPServer

server = MCPServer(
    runner=ClaudeRunner(work_dir="/my/project"),
    transport="stdio",    # "stdio" (기본) 또는 "sse"
)

# stdio 모드 (Claude Desktop 등)
server.run()

# SSE 모드 (HTTP 서버)
server.run(host="0.0.0.0", port=3000)
```

**MCP 설정 파일 (claude_desktop_config.json 예시):**

```json
{
  "mcpServers": {
    "open_kknaks": {
      "command": "python",
      "args": ["-m", "open_kknaks.mcp", "--work-dir", "/my/project"],
      "env": {
        "REDIS_URL": "redis://localhost:6379"
      }
    }
  }
}
```

---

## 4. 패키지 구조

```
open_kknaks/
├── __init__.py              # ClaudeRunner, Task, TaskStatus 등 public export
├── client.py                # ClaudeRunner (유저 메인 진입점)
├── task.py                  # Task, TaskStatus, Priority, TaskResult, TokenUsage, StreamEvent
├── batch.py                 # BatchRunner, BatchStatus
├── worker.py                # Worker (큐 소비 + executor 호출)
├── runner/
│   ├── __init__.py
│   ├── base.py              # AbstractExecutor 인터페이스
│   └── claude.py            # ClaudeCodeExecutor (CLI 실행)
├── broker/
│   ├── __init__.py          # AbstractBroker export
│   ├── base.py              # AbstractBroker 인터페이스
│   ├── memory.py            # InMemoryBroker
│   └── redis.py             # RedisBroker
├── middleware/
│   ├── __init__.py
│   ├── base.py              # Middleware ABC
│   ├── logging.py           # LoggingMiddleware
│   ├── retry.py             # RetryMiddleware
│   ├── timeout.py           # TimeoutMiddleware
│   ├── cost.py              # CostTrackingMiddleware
│   ├── rate_limit.py        # RateLimitMiddleware
│   └── callback.py          # CallbackMiddleware
├── mcp/
│   ├── __init__.py
│   ├── server.py            # MCPServer
│   └── __main__.py          # python -m open_kknaks.mcp 실행 지원
├── exceptions.py            # 커스텀 예외 (TaskTimeout, ClaudeNotFound, BrokerError 등)
├── _utils.py                # 유틸리티 (claude 바이너리 탐색, 로깅 설정 등)
└── py.typed                 # PEP 561 타입 힌트 마커
```

**프로젝트 루트:**

```
open_kknaks/                 # 위 패키지
tests/
├── conftest.py
├── test_client.py
├── test_worker.py
├── test_executor.py
├── test_broker_memory.py
├── test_broker_redis.py
├── test_middleware.py
├── test_batch.py
├── test_mcp.py
└── test_integration.py
docs/
├── PLAN.md                  # 기획 초안 (원본)
├── PRD.md                   # 이 문서
└── EXAMPLES.md              # 유즈케이스 예시 모음
pyproject.toml
README.md
LICENSE                      # MIT
CHANGELOG.md
.github/
└── workflows/
    ├── test.yml             # pytest + coverage
    └── publish.yml          # PyPI 배포
```

---

## 5. 의존성

### 5.1 필수 의존성

| 패키지 | 버전 | 용도 |
|---|---|---|
| Python | ≥ 3.10 | async/await, match-case, `X | Y` 유니온 타입 |
| `pydantic` | ≥ 2.0 | Task/Config 데이터 검증 + 직렬화 |

### 5.2 선택적 의존성 (extras)

| extra | 패키지 | 용도 |
|---|---|---|
| `redis` | `redis[asyncio] ≥ 5.0` | RedisBroker |
| `mcp` | `mcp ≥ 1.0` | MCP 서버 |

**설치 예시:**

```bash
pip install open-kknaks                  # InMemoryBroker만 사용
pip install open-kknaks[redis]           # + RedisBroker
pip install open-kknaks[mcp]             # + MCP 서버
pip install open-kknaks[redis,mcp]       # 전부
```

### 5.3 개발 의존성

| 패키지 | 용도 |
|---|---|
| `pytest` + `pytest-asyncio` | 테스트 |
| `ruff` | 린트 + 포맷 |
| `mypy` | 정적 타입 체크 |
| `coverage` | 커버리지 |

---

## 6. 전제 조건

| 항목 | 설명 |
|---|---|
| Claude Code CLI | 사용자 환경에 설치 + 로그인 완료 (`claude` 바이너리 PATH에 존재) |
| Python ≥ 3.10 | asyncio 기반 |
| Redis (선택) | RedisBroker 사용 시만 필요. 유저가 직접 설치/실행 |

**Claude Code CLI 검증 로직:**

```python
# ClaudeRunner 초기화 시
1. claude_bin이 명시되면 해당 경로 확인
2. 없으면 shutil.which("claude") 로 탐색
3. 발견 못하면 ClaudeNotFoundError 발생
4. `claude auth status` 실행하여 로그인 상태 확인 (경고 레벨)
```

---

## 7. API 상세

### 7.1 기본 사용법

```python
import asyncio
from open_kknaks import ClaudeRunner

async def main():
    async with ClaudeRunner(work_dir="/my/project") as runner:
        # 단일 작업
        task_id = await runner.submit("이 코드의 버그를 찾아줘")
        result = await runner.result(task_id)
        print(result.result)

asyncio.run(main())
```

### 7.2 스트리밍

```python
async with ClaudeRunner(work_dir="/my/project") as runner:
    task_id = await runner.submit("에러 로그 분석해줘", context=error_log)
    
    async for event in runner.stream(task_id):
        if event.type == "text_delta":
            print(event.text, end="", flush=True)
        elif event.type == "result":
            print(f"\n완료! 토큰: {event.data.get('usage', {})}")
```

### 7.3 배치 작업

```python
async with ClaudeRunner(work_dir="/my/project", concurrency=3) as runner:
    batch_id = await runner.batch_submit(
        tasks=[
            {"prompt": "이슈 #101 분석", "context": issue1_text},
            {"prompt": "이슈 #102 분석", "context": issue2_text},
            {"prompt": "이슈 #103 분석", "context": issue3_text},
        ],
        mode="parallel",  # "parallel" (기본) 또는 "sequential"
    )
    
    results = await runner.batch_wait(batch_id, timeout=1800)
    for r in results:
        print(f"[{r.task_id}] {r.status.value}: {r.result[:100]}")
```

### 7.4 우선순위 + 지연 실행

```python
# 긴급 작업
await runner.submit("프로덕션 에러!", context=crash_log, priority="high")

# 30초 후 실행
await runner.submit("비긴급 리팩토링 제안", delay_seconds=30, priority="low")
```

### 7.5 세션 이어가기

```python
# 첫 번째 작업
result1 = await runner.result(
    await runner.submit("이 프로젝트 구조를 분석해줘")
)
session = result1.session_id

# 같은 세션에서 이어서 질문
result2 = await runner.result(
    await runner.submit("아까 분석한 내용 기반으로 리팩토링 해줘", session_id=session)
)
```

### 7.6 커스텀 미들웨어

```python
from open_kknaks.middleware import Middleware, CallbackMiddleware

class SlackNotifyMiddleware(Middleware):
    async def after_process(self, task, result):
        if result.status == TaskStatus.DONE:
            await send_slack(f"✅ 작업 완료: {task.prompt[:50]}")

    async def on_failure(self, task, error):
        await send_slack(f"❌ 작업 실패: {task.prompt[:50]} - {error}")

runner = ClaudeRunner(
    work_dir="/my/project",
    middlewares=[
        SlackNotifyMiddleware(),
        CallbackMiddleware(on_done=my_webhook),
    ],
)
```

### 7.7 RedisBroker 멀티 프로세스

```python
# === producer.py (작업 등록만) ===
from open_kknaks import ClaudeRunner
from open_kknaks.broker import RedisBroker

broker = RedisBroker(url="redis://myserver:6379", key_prefix="myapp")
runner = ClaudeRunner(work_dir="/my/project", broker=broker)

# 워커 시작하지 않고 작업만 등록
task_id = await runner.submit("PR 리뷰해줘", context=pr_diff)

# === worker.py (워커만 실행) ===
from open_kknaks import ClaudeRunner
from open_kknaks.broker import RedisBroker

broker = RedisBroker(url="redis://myserver:6379", key_prefix="myapp")
runner = ClaudeRunner(work_dir="/my/project", broker=broker, concurrency=2)

await runner.start()   # 워커 루프 시작 (블로킹)
```

### 7.8 MCP 서버

```python
from open_kknaks.mcp import MCPServer
from open_kknaks import ClaudeRunner

server = MCPServer(
    runner=ClaudeRunner(work_dir="/my/project"),
    transport="stdio",
)
server.run()
```

---

## 8. 에러 처리

### 8.1 커스텀 예외 계층

```
OpenKnaksError (base)
├── ClaudeNotFoundError          # claude 바이너리 없음
├── ClaudeAuthError              # 로그인 안 됨
├── TaskError
│   ├── TaskNotFoundError        # task_id 없음
│   ├── TaskTimeoutError         # 타임아웃 초과
│   ├── TaskCancelledError       # 취소됨
│   └── TaskFailedError          # CLI 비정상 종료 (exit_code ≠ 0)
├── BatchError
│   ├── BatchNotFoundError
│   └── BatchPartialFailureError # 배치 내 일부 실패
├── BrokerError
│   ├── BrokerConnectionError    # Redis 연결 실패
│   └── BrokerTimeoutError       # 브로커 응답 타임아웃
└── ConfigError                  # 설정 오류
```

### 8.2 재시도 정책

```python
# Task별 설정
await runner.submit(
    "불안정한 작업",
    max_retries=3,         # 최대 3번 재시도
    timeout=300,           # 5분 타임아웃
)

# RetryMiddleware 기본 동작
# - 지수 백오프: 2^retry_count 초 (2s → 4s → 8s)
# - 최대 대기: 60초
# - 재시도 조건: exit_code ≠ 0 and retry_count < max_retries
# - 재시도 제외: TaskCancelledError, ClaudeAuthError
```

---

## 9. 유즈케이스

### 9.1 서버 에러 → Claude Code 분석 → Slack 알림

```python
from open_kknaks import ClaudeRunner
from open_kknaks.middleware import CallbackMiddleware

async def on_done(task, result):
    await slack_webhook.send({
        "text": f"🔍 에러 분석 완료\n```{result.result[:1000]}```"
    })

runner = ClaudeRunner(
    work_dir="/my/server/repo",
    append_system_prompt="You are a backend error analyst. Be concise.",
    middlewares=[CallbackMiddleware(on_done=on_done)],
)

# FastAPI 에러 핸들러에서
@app.exception_handler(Exception)
async def handle_error(request, exc):
    await runner.submit(
        "이 에러를 분석하고 수정 방안을 제시해줘",
        context=f"Error: {exc}\nTraceback: {traceback.format_exc()}",
        priority="high",
    )
    return JSONResponse(status_code=500, content={"error": "Internal Server Error"})
```

### 9.2 Jira 이슈 → 코드 분석 → 리포트

```python
# Jira webhook 수신 시
async def handle_jira_webhook(payload):
    issue = payload["issue"]
    task_id = await runner.submit(
        f"Jira 이슈 분석: {issue['summary']}",
        context=f"Description: {issue['description']}\nPriority: {issue['priority']}",
        metadata={"jira_key": issue["key"]},
    )
    result = await runner.result(task_id, timeout=600)
    await jira_client.add_comment(issue["key"], result.result)
```

### 9.3 CI 실패 → 원인 분석 → PR 코멘트

```python
# GitHub Actions에서
async def analyze_ci_failure(pr_number, test_output):
    runner = ClaudeRunner(
        work_dir="/workspace",
        model="sonnet",
        bare=True,
        allowed_tools=["Read", "Bash(git log *)", "Bash(git diff *)"],
    )
    async with runner:
        result = await runner.result(
            await runner.submit(
                "CI 테스트 실패 원인을 분석하고 수정 코드를 제안해줘",
                context=test_output,
            )
        )
        await github.create_pr_comment(pr_number, result.result)
```

### 9.4 문서 비교 → 진행률 분석

```python
async def compare_docs(confluence_doc, github_commits):
    task_id = await runner.submit(
        "Confluence 기획 문서와 최근 커밋을 비교해서 구현 진행률을 분석해줘",
        context=f"## 기획 문서\n{confluence_doc}\n\n## 최근 커밋\n{github_commits}",
    )
    async for event in runner.stream(task_id):
        if event.type == "text_delta":
            yield event.text  # SSE로 프론트에 전달
```

---

## 10. 테스트 전략

### 10.1 테스트 레이어

| 레이어 | 범위 | 방법 |
|---|---|---|
| **Unit** | Task, Broker, Middleware 각 클래스 | mock executor, InMemoryBroker |
| **Integration** | Worker + Executor + Broker 연동 | InMemoryBroker + mock subprocess |
| **E2E** | 실제 Claude Code CLI 호출 | `claude -p` 실행 (CI에서는 skip 마크) |
| **Redis** | RedisBroker | testcontainers 또는 mock redis |

### 10.2 mock 전략

```python
# Claude Code CLI를 mock하는 테스트용 executor
class MockExecutor(AbstractExecutor):
    def __init__(self, response="mock response", delay=0.1):
        self.response = response
        self.delay = delay

    async def execute(self, task, on_chunk=None):
        await asyncio.sleep(self.delay)
        if on_chunk:
            await on_chunk(StreamEvent(task_id=task.id, type="text_delta", text=self.response, data={}))
        return TaskResult(
            task_id=task.id,
            status=TaskStatus.DONE,
            result=self.response,
            error=None,
            exit_code=0,
            session_id="mock-session",
            usage=TokenUsage(input_tokens=100, output_tokens=50),
            duration_seconds=self.delay,
            metadata={},
        )
```

### 10.3 CI 설정

```yaml
# .github/workflows/test.yml
- pytest -x --timeout=60 -m "not e2e"     # 기본: e2e 제외
- pytest -x --timeout=300 -m "e2e"         # 수동 트리거: e2e 포함
```

---

## 11. PyPI 배포

### 11.1 패키지 메타데이터

```toml
# pyproject.toml
[project]
name = "open-kknaks"
version = "0.1.0"
description = "Task queue library for Claude Code CLI"
readme = "README.md"
license = "MIT"
requires-python = ">=3.10"
authors = [{ name = "kknaks" }]
keywords = ["claude", "claude-code", "task-queue", "ai", "automation"]
classifiers = [
    "Development Status :: 3 - Alpha",
    "Intended Audience :: Developers",
    "License :: OSI Approved :: MIT License",
    "Programming Language :: Python :: 3.10",
    "Programming Language :: Python :: 3.11",
    "Programming Language :: Python :: 3.12",
    "Programming Language :: Python :: 3.13",
    "Topic :: Software Development :: Libraries",
    "Framework :: AsyncIO",
]

dependencies = [
    "pydantic>=2.0",
]

[project.optional-dependencies]
redis = ["redis[asyncio]>=5.0"]
mcp = ["mcp>=1.0"]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.24",
    "ruff>=0.8",
    "mypy>=1.13",
    "coverage>=7.0",
]

[project.urls]
Homepage = "https://github.com/kknaks/open_kknaks"
Repository = "https://github.com/kknaks/open_kknaks"
Issues = "https://github.com/kknaks/open_kknaks/issues"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```

### 11.2 버전 전략

- **0.1.0** — MVP (InMemoryBroker + 단일/배치 작업 + 스트리밍)
- **0.2.0** — RedisBroker + 미들웨어
- **0.3.0** — MCP 서버
- **1.0.0** — API 안정화 + 전체 테스트 커버리지 ≥ 80%

---

## 12. 마일스톤

### Sprint 1 — 코어 (1.5주)

| 작업 | 설명 | 산출물 |
|---|---|---|
| S1-1 | 프로젝트 세팅 (pyproject.toml, ruff, mypy, CI) | 빌드/린트/테스트 통과 |
| S1-2 | Task, TaskStatus, Priority, TaskResult 모델 | `task.py` |
| S1-3 | AbstractBroker + InMemoryBroker | `broker/` |
| S1-4 | ClaudeCodeExecutor (subprocess + stream-json 파싱) | `runner/claude.py` |
| S1-5 | Worker (큐 소비 + executor 호출 + concurrency) | `worker.py` |
| S1-6 | ClaudeRunner (client 조립 + submit/stream/result/cancel) | `client.py` |
| S1-7 | Unit + Integration 테스트 | `tests/` |

**완료 기준:** `ClaudeRunner(work_dir=X)` → `submit()` → `stream()` / `result()` 동작 확인

### Sprint 2 — 배치 + 미들웨어 (1주)

| 작업 | 설명 | 산출물 |
|---|---|---|
| S2-1 | BatchRunner (parallel / sequential) | `batch.py` |
| S2-2 | Middleware ABC + 파이프라인 | `middleware/base.py` |
| S2-3 | LoggingMiddleware, RetryMiddleware, TimeoutMiddleware | `middleware/` |
| S2-4 | CostTrackingMiddleware (stream-json usage 파싱) | `middleware/cost.py` |
| S2-5 | RateLimitMiddleware, CallbackMiddleware | `middleware/` |
| S2-6 | 세션 이어가기 (--continue, --resume) | `runner/claude.py` 확장 |
| S2-7 | 배치 + 미들웨어 테스트 | `tests/` |

**완료 기준:** 배치 작업 + 미들웨어 체인 동작, 비용 추적 확인

### Sprint 3 — RedisBroker (1주)

| 작업 | 설명 | 산출물 |
|---|---|---|
| S3-1 | RedisBroker 구현 (큐 + 상태 + 스트리밍) | `broker/redis.py` |
| S3-2 | 딜레이 큐 (Sorted Set 기반) | `broker/redis.py` |
| S3-3 | 멀티 프로세스 테스트 (producer/worker 분리) | `tests/test_broker_redis.py` |
| S3-4 | key_prefix 네임스페이스 격리 검증 | 테스트 |

**완료 기준:** 별도 프로세스에서 producer/worker 분리 실행 확인

### Sprint 4 — MCP + 배포 (1주)

| 작업 | 설명 | 산출물 |
|---|---|---|
| S4-1 | MCPServer (stdio transport) | `mcp/server.py` |
| S4-2 | MCPServer (SSE transport) | `mcp/server.py` |
| S4-3 | `python -m open_kknaks.mcp` 실행 지원 | `mcp/__main__.py` |
| S4-4 | README.md 작성 (Quick Start, API Reference, 예시) | `README.md` |
| S4-5 | CHANGELOG.md | `CHANGELOG.md` |
| S4-6 | PyPI 배포 (GitHub Actions) | `.github/workflows/publish.yml` |
| S4-7 | E2E 테스트 + 커버리지 ≥ 70% | CI |

**완료 기준:** `pip install open-kknaks` → 즉시 사용 가능, MCP 서버 연동 확인

### 전체 일정: 4.5주

```
S1 (1.5주) ─── S2 (1주) ─── S3 (1주) ─── S4 (1주)
   코어          배치+MW       Redis        MCP+배포
```

---

## 13. 제외 범위 (Non-Goals)

| 항목 | 이유 |
|---|---|
| 트리거 시스템 (webhook 서버, cron 스케줄러) | 유저 코드에서 붙이는 영역. 라이브러리 책임 아님 |
| 웹 대시보드 UI | 라이브러리 범위 초과. 별도 프로젝트로 가능 |
| Claude Code 이외의 LLM 실행 | 단일 책임 유지. 범용 태스크 큐는 Dramatiq/Celery 사용 |
| Claude Code CLI 설치/로그인 | 유저 환경에 이미 설치되어 있어야 함 (전제 조건) |
| RabbitMQ / SQS 등 추가 브로커 | MVP 이후 커뮤니티 기여로 확장 가능 |
| 영속적 작업 히스토리 (DB) | 브로커 TTL로 관리. 히스토리가 필요하면 미들웨어로 DB 적재 |

---

## 14. 리스크

| 리스크 | 확률 | 영향 | 대응 |
|---|---|---|---|
| Claude Code CLI 인터페이스 변경 | 중 | 높음 | stream-json 포맷 파싱 로직을 분리하여 교체 용이하게 설계. CLI 버전 체크 추가 |
| API rate limit | 중 | 중 | RateLimitMiddleware 기본 제공, 지수 백오프 |
| pty spawn 플랫폼 이슈 (Windows) | 높음 | 중 | MVP는 Unix 전용 (macOS/Linux). Windows는 0.2.0 이후 `--output-format stream-json`이 pty 없이도 동작하므로 subprocess fallback |
| Redis 의존성 복잡도 | 낮음 | 낮음 | InMemoryBroker를 기본값으로 제공하여 Redis 없이도 동작 |
| MCP SDK 변경 | 중 | 중 | `mcp` 패키지를 optional으로 분리, 버전 고정 |

---

## 부록 A: Dramatiq 개념 매핑 상세

| Dramatiq 개념 | open_kknaks 대응 | 비고 |
|---|---|---|
| `dramatiq.actor` (데코레이터) | 없음 | 실행 함수 고정 (Claude Code CLI) |
| `actor.send()` | `runner.submit()` | |
| `actor.send_with_options(delay=)` | `runner.submit(delay_seconds=)` | |
| `message.get_result()` | `runner.result(task_id)` | |
| `dramatiq.group()` | `runner.batch_submit(mode="parallel")` | |
| `dramatiq.pipeline()` | `runner.batch_submit(mode="sequential")` | |
| `Broker.add_middleware()` | `ClaudeRunner(middlewares=[...])` | 생성자 주입 |
| `Results` middleware | 브로커에 통합 | 별도 result backend 불필요 |
| `Retries` middleware | `RetryMiddleware` | 기본 제공 |
| `TimeLimit` middleware | `TimeoutMiddleware` | 기본 제공 |
| `dramatiq.Worker` | `Worker` | 내장, 자동 시작 |

## 부록 B: Claude Code CLI 플래그 매핑

| Task 필드 | CLI 플래그 | 비고 |
|---|---|---|
| `prompt` | `-p "prompt"` | 필수 |
| `context` | stdin 파이프 | `echo context \| claude -p` |
| `work_dir` | `--cwd` 또는 subprocess cwd | |
| `model` | `--model` | |
| `allowed_tools` | `--allowedTools` | 공백 구분 리스트 |
| `append_system_prompt` | `--append-system-prompt` | |
| `max_turns` | `--max-turns` | |
| `permission_mode` | `--permission-mode` / `--dangerously-skip-permissions` | |
| `bare` | `--bare` | 기본 활성화 |
| `session_id` (이어가기) | `--resume <session_id>` | |
| `timeout` | `--max-budget-usd` (간접) + subprocess timeout | |
| (항상) | `--output-format stream-json` | 파싱용 고정 |
| (항상) | `--verbose` | 스트리밍 이벤트 포함 |
