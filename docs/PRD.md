# open_kknaks — PRD (Product Requirements Document)

> **Version:** 2.0
> **Created:** 2026-03-25
> **Updated:** 2026-03-26
> **Status:** Draft
> **기반 문서:** ARCHITECTURE_V2.md, CLAUDE_CODE_ANALYSIS.md

---

## 1. 개요

### 1.1 한 줄 정의

Claude Code CLI를 **PTY 기반으로 안정적으로 실행**하는 프로듀서/워커 분리형 태스크 큐 라이브러리.

### 1.2 문제 정의

| 문제 | 설명 |
|---|---|
| 반복 보일러플레이트 | Claude Code CLI를 subprocess로 호출하는 코드를 매번 작성해야 함 |
| 프로세스 누수 | Pipe 방식은 Claude Code 내부 자식 프로세스(Node.js 등)를 정리하지 못함 → 고아/좀비 누적 |
| 버퍼 데드락 | stdout/stderr 동시 PIPE 시 OS 버퍼(64KB) 포화로 프로세스 블록 |
| 큐잉/동시성 부재 | 여러 작업을 순차/병렬 처리하려면 자체 큐 로직 필요 |
| 스트리밍 지연 | Pipe 블록 버퍼링(~4KB)으로 실시간 청크 전달 지연 |
| 행(Hang) 감지 불가 | Pipe readline()이 블록되면 전체 타임아웃(600s)까지 대기 |
| 재시도/우선순위 없음 | 실패 복구, 우선순위 큐, DLQ, 지연 실행 등 운영 기능이 없음 |

### 1.3 해결 방향

- **PTY 기반 Executor** — `os.fork()` + `os.setsid()`로 세션 리더 생성, 프로세스 트리 전체 관리
- **프로듀서/워커 완전 분리** — `ClaudeClient`(등록)와 `ClaudeWorker`(실행)는 별개 프로세스
- **Redis 브로커** — 멀티 큐 라우팅, DLQ, at-least-once delivery
- **라이브러리 레벨** — `pip install open-kknaks[redis]`로 끝나는 패키지
- **PyPI 배포** — `open-kknaks`, Python 3.10+, Linux/macOS

### 1.4 타겟 유저

| 유저 | 시나리오 |
|---|---|
| **백엔드 개발자** | 서버 이벤트(에러 로그, Jira 이슈) → Claude Code 자동 분석 파이프라인 구축 |
| **DevOps 엔지니어** | CI 실패 → Claude Code 원인 분석 → PR 코멘트 자동화 |
| **AI 도구 빌더** | MCP 서버로 노출하여 Claude Desktop에서 원격 Claude Code 호출 |
| **플랫폼 빌더** | 다수의 Claude Code 에이전트를 동시에 안정적으로 운영 |

---

## 2. 핵심 설계 결정

### 2.1 PTY를 선택한 이유

기존 프로젝트(app_builder_local, persona_counselor)는 모두 `asyncio.create_subprocess_exec` + PIPE를 사용.
라이브러리 수준의 안정성을 위해 PTY로 전환한다.

| 항목 | Pipe 방식 (기존) | PTY 방식 (open_kknaks) |
|---|---|---|
| 프로세스 그룹 | 없음 (직접 자식만) | `os.setsid()` → 세션 리더 |
| 고아 프로세스 | Claude 내부 자식 누수 | SIGHUP 전파로 전체 정리 |
| 버퍼 데드락 | stdout/stderr 동시 PIPE 위험 | 단일 master_fd — 불가능 |
| 출력 버퍼링 | 블록 버퍼(~4KB 뭉침) | 라인 즉시 전달 |
| 행 감지 | 라인 타임아웃 → continue → 600s 대기 | idle_timeout → 즉시 예외 |
| 종료 | SIGTERM → SIGKILL (2단계) | SIGHUP → SIGTERM → SIGKILL (3단계) |
| concurrency | 좀비 누적 위험 | 세션별 격리 — 안전 |
| 플랫폼 | Linux/macOS/Windows | **Linux/macOS** (Windows 미지원) |

### 2.2 프로듀서/워커 분리

v1의 `ClaudeRunner` 일체형을 `ClaudeClient` + `ClaudeWorker`로 분리.

```
ClaudeClient ──enqueue──▶ RedisBroker ◀──dequeue── ClaudeWorker
                              │                        │
                              │                   ClaudeConfig
                              │                        │
                         Middleware                 PTY Executor
```

- `ClaudeClient`: 작업 등록 + 상태/결과 조회. Claude Code CLI와 무관.
- `ClaudeWorker`: 큐에서 작업을 꺼내 PTY로 Claude Code CLI 실행.
- 같은 프로세스에서 실행할 수도 있고, 별도 프로세스/머신에서 실행할 수도 있음.

### 2.3 InMemoryBroker 제거

- Redis가 유일한 브로커 구현
- 테스트는 mock/fixture 사용
- InMemoryBroker의 "단일 프로세스 한정" 제약이 프로듀서/워커 분리 원칙에 위배

---

## 3. 아키텍처

### 3.1 전체 흐름

```
┌─────────────────────────────────────────────────────────┐
│                     유저 코드 (프로듀서)                    │
│                                                         │
│  client = ClaudeClient(broker=RedisBroker(...))         │
│  await client.submit("분석해줘", queue="error-analysis") │
└──────────────────────┬──────────────────────────────────┘
                       │ enqueue
                       ▼
┌─────────────────────────────────────────────────────────┐
│                   RedisBroker                            │
│                                                         │
│  큐: error-analysis, pr-review, default, ...            │
│  DLQ: {queue}.dlq                                       │
│  스트림: stream:{task_id}                                │
│  상태: task:{task_id}                                    │
└──────┬──────────────────────────────┬───────────────────┘
       │ consume("error-analysis")    │ consume("pr-review")
       ▼                              ▼
┌──────────────────┐    ┌──────────────────┐
│   Worker A       │    │   Worker B       │
│                  │    │                  │
│ queues:          │    │ queues:          │
│  - error-analysis│    │  - pr-review     │
│ work_dir:        │    │ work_dir:        │
│  /my/backend     │    │  /my/frontend    │
│ concurrency: 4   │    │ concurrency: 2   │
│                  │    │                  │
│ ┌──────────────┐ │    │ ┌──────────────┐ │
│ │ PTY Executor │ │    │ │ PTY Executor │ │
│ │ os.fork()    │ │    │ │ os.fork()    │ │
│ │ os.setsid()  │ │    │ │ os.setsid()  │ │
│ │ claude -p .. │ │    │ │ claude -p .. │ │
│ └──────────────┘ │    │ └──────────────┘ │
└──────────────────┘    └──────────────────┘
```

### 3.2 컴포넌트 상세

#### 3.2.1 ClaudeClient (프로듀서)

작업을 큐에 넣고, 상태/결과를 조회하는 얇은 클라이언트. **워커를 실행하지 않는다.**

```python
from open_kknaks import ClaudeClient
from open_kknaks.broker import RedisBroker

client = ClaudeClient(
    broker=RedisBroker(url="redis://localhost:6379", namespace="myapp"),
)

# 작업 등록
task_id = await client.submit(
    prompt="이 에러 분석해줘",
    context=error_log,
    queue="error-analysis",
    priority="high",
    timeout=600,
    max_retries=3,
    metadata={"source": "sentry", "issue_id": "PROJ-123"},
)

# 결과 조회
status = await client.status(task_id)
result = await client.result(task_id, timeout=600)

# 스트리밍
async for event in client.stream(task_id):
    print(event.text, end="")

# 배치
batch_id = await client.batch_submit(
    tasks=[
        {"prompt": "이슈 1", "context": ctx1},
        {"prompt": "이슈 2", "context": ctx2},
    ],
    queue="error-analysis",
    mode="parallel",
)
```

**메서드:**

| 메서드 | 설명 |
|---|---|
| `submit(prompt, *, context, queue, priority, delay_seconds, timeout, max_retries, model, system_prompt, append_system_prompt, max_turns, effort, json_schema, allowed_tools, disallowed_tools, permission_mode, session_id, mcp_config, add_dirs, metadata) → str` | 작업 등록 → task_id 반환. work_dir/claude_bin은 보안상 Task에서 오버라이드 불가 |
| `stream(task_id) → AsyncIterator[StreamEvent]` | 실시간 청크 스트리밍 |
| `status(task_id) → TaskStatus` | 작업 상태 조회 |
| `result(task_id, *, timeout) → TaskResult` | 완료 대기 + 결과 반환 |
| `cancel(task_id) → bool` | 실행 중 작업 취소 |
| `batch_submit(tasks, *, queue, mode) → str` | 배치 작업 등록 → batch_id 반환 |
| `batch_status(batch_id) → BatchStatus` | 배치 상태 조회 |
| `batch_wait(batch_id, *, timeout) → list[TaskResult]` | 배치 완료 대기 |

**`result()` / `stream()` 구현 방식:**

둘 다 **XREAD BLOCK** 기반이며 `broker.subscribe_chunks(task_id)`를 공유한다. 폴링 안 씀.

| 메서드 | 동작 |
|---|---|
| `result(task_id, *, timeout)` | `subscribe_chunks`로 청크는 무시하고 완료 신호만 대기 → `broker.get_task()` 1회 호출로 최종 결과 반환 |
| `stream(task_id)` | `subscribe_chunks`로 청크를 `AsyncIterator[StreamEvent]`로 yield |

#### 3.2.2 ClaudeWorker (소비자)

큐에서 Task를 꺼내 PTY 기반으로 Claude Code CLI를 실행한다.

```python
from open_kknaks.worker import ClaudeWorker
from open_kknaks.broker import RedisBroker

worker = ClaudeWorker(
    broker=RedisBroker(url="redis://localhost:6379", namespace="myapp"),

    # 어떤 큐를 소비할지
    queues=["error-analysis", "general"],

    # Claude Code 설정 (분리된 객체)
    claude=ClaudeConfig(
        work_dir="/my/backend",
        model="sonnet",
        append_system_prompt="You are a backend error analyst.",
        allowed_tools=["Read", "Bash(git log *)", "Bash(git diff *)"],
    ),

    # 워커 설정
    concurrency=4,
    poll_interval=0.5,
    heartbeat_interval=30,
    shutdown_timeout=300,
)

await worker.run()  # 블로킹
```

**Worker 기본값 vs Task 오버라이드:**

병합 위치: `Worker._merge_config(task)` — `ClaudeConfig.model_copy(update={})` 사용 (MergedConfig 별도 클래스 없음).

```
최종 실행 설정 = Worker 기본값(ClaudeConfig) ← Task 오버라이드 (Task에 명시된 것만 덮어씀)

예: Worker(model="sonnet") + Task(model=None)  → sonnet
    Worker(model="sonnet") + Task(model="opus") → opus
```

**오버라이드 가능 필드 (화이트리스트):**
- `model`, `system_prompt`, `append_system_prompt`, `max_turns`, `effort`, `json_schema`
- `allowed_tools`, `disallowed_tools`, `permission_mode`
- `mcp_config`, `add_dirs`

**오버라이드 불가 (보안):**
- `work_dir`, `claude_bin` — Task에서 지정 불가. Worker의 ClaudeConfig 값만 사용.

**Worker 내부 구조:**

```
ClaudeWorker
  │
  ├─ DequeueLoop (asyncio.Task × 1)
  │   │  여러 큐를 라운드로빈으로 폴링
  │   │  dequeue → internal PriorityQueue에 넣기
  │   └─ delayed task 체크 (eta 지난 것 → 메인 큐로 이동)
  │
  ├─ ProcessorLoop (asyncio.Task × concurrency)
  │   │  internal queue에서 꺼내기
  │   │  emit_before("process")
  │   │  executor.execute(task)  ← PTY 기반 Executor
  │   │  emit_after("process", result | exception)
  │   │  ack 또는 nack
  │   └─ 실패 시: Retries 미들웨어가 delay 재큐잉 또는 DLQ
  │
  ├─ HeartbeatLoop (asyncio.Task × 1)
  │   └─ broker.heartbeat(worker_id) 주기적 호출
  │
  └─ SignalHandler
      ├─ SIGTERM → graceful shutdown (PTY 세션 전체 SIGHUP)
      └─ SIGINT  → graceful shutdown (2번 누르면 즉시 종료)
```

**그레이스풀 셧다운:**

```
stop() 호출
  │
  ├─ 1) _running = False → dequeue 루프 정지
  │
  ├─ 2) 실행 중 작업 완료 대기 (shutdown_timeout)
  │     ├─ timeout 내 완료 → 정상 ack
  │     └─ timeout 초과:
  │           ├─ SIGHUP → PTY 세션 전체 (프로세스 그룹) 종료 시도
  │           ├─ 5초 대기
  │           ├─ SIGTERM → 개별 프로세스
  │           ├─ 5초 대기
  │           └─ SIGKILL → 강제 종료 + master_fd close
  │
  ├─ 3) internal queue에 남은 미처리 Task → broker.requeue()
  │
  └─ 4) broker.close()
```

#### 3.2.3 ClaudeConfig (Claude Code CLI 설정)

Worker의 Claude Code CLI 실행 환경을 담는 설정 객체. 여러 Worker에서 재사용 가능.

```python
class ClaudeConfig(BaseModel):
    # 환경
    work_dir: str = "."
    claude_bin: str | None = None         # None이면 PATH 자동 탐색

    # LLM / 프롬프트
    model: str | None = None              # --model
    system_prompt: str | None = None      # --system-prompt (전체 교체)
    append_system_prompt: str | None = None  # --append-system-prompt (추가)
    max_turns: int | None = None          # --max-turns
    effort: str | None = None             # --effort (low/medium/high/max)
    json_schema: str | None = None        # --json-schema

    # 도구 / 권한
    allowed_tools: list[str] | None = None      # --allowedTools
    disallowed_tools: list[str] | None = None   # --disallowedTools
    permission_mode: str = "default"             # --permission-mode

    # 세션 / 환경
    mcp_config: str | None = None         # --mcp-config
    add_dirs: list[str] | None = None     # --add-dir
```

#### 3.2.4 PTY Executor — 핵심 실행 엔진

PTY 기반으로 Claude Code CLI를 실행하는 핵심 컴포넌트.

```
Executor.execute(task, config, on_chunk)
  │
  ├─ 1) _build_command(task, config) → cmd: list[str]
  │
  ├─ 2) PTY 생성 + 프로세스 스폰
  │     │  master_fd, slave_fd = pty.openpty()
  │     │  pid = os.fork()
  │     │  자식: os.setsid() → 새 세션 리더 (핵심!)
  │     │  부모: master_fd → asyncio 이벤트 루프에 등록
  │     └─ PTYProcess(pid, master_fd, pgid=pid) 생성
  │
  ├─ 3) 출력 읽기 루프 (asyncio)
  │     │  loop.add_reader(master_fd, _on_data)
  │     │  os.read(master_fd, 4096) → LineBuffer → parse_stream_json_line()
  │     │  text chunk → on_chunk() 콜백 (Redis Stream)
  │     │  전체 타임아웃 + idle 타임아웃 동시 감시
  │     └─ EIO/EOF → 프로세스 종료 감지
  │
  ├─ 4) 프로세스 종료 대기 + 좀비 수거
  │
  └─ 5) TaskResult 반환
```

**PTYProcess 3단계 종료:**

```
SIGHUP  → os.killpg(pgid, SIGHUP)   → PTY 세션 전체 (프로세스 그룹)
    5초 대기
SIGTERM → os.kill(pid, SIGTERM)      → 직접 프로세스
    5초 대기
SIGKILL → os.killpg(pgid, SIGKILL)  → 강제 종료
```

**CLI 플래그 매핑:**

| 설정 필드 | CLI 플래그 | 비고 |
|---|---|---|
| `model` | `--model` | |
| `system_prompt` | `--system-prompt` | 전체 교체 |
| `append_system_prompt` | `--append-system-prompt` | 기본 프롬프트에 추가 |
| `max_turns` | `--max-turns` | 없으면 무제한 |
| `effort` | `--effort` | low/medium/high/max |
| `json_schema` | `--json-schema` | 구조화 출력 |
| `allowed_tools` | `--allowedTools` | |
| `disallowed_tools` | `--disallowedTools` | |
| `permission_mode` | `--permission-mode` / `--dangerously-skip-permissions` | |
| `session_id` | `--resume` | 세션 이어가기 |
| `mcp_config` | `--mcp-config` | MCP 서버 연결 |
| `add_dirs` | `--add-dir` | 추가 접근 디렉토리 |
| `context` | stdin 파이프 | |
| (항상) | `--output-format stream-json` | 파싱용 고정 |
| (항상) | `-p` | 비대화형 모드 |

#### 3.2.5 Task 모델

```python
class Task(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    id: str = Field(default_factory=lambda: str(uuid4()))
    prompt: str
    context: str | None = None

    # 라우팅
    queue: str = "default"

    # 상태
    status: TaskStatus = TaskStatus.PENDING
    priority: Priority = Priority.NORMAL
    delay_until: datetime | None = None

    # 실행 옵션 (None이면 Worker 기본값 사용)
    model: str | None = None
    system_prompt: str | None = None
    append_system_prompt: str | None = None
    max_turns: int | None = None
    effort: str | None = None
    json_schema: str | None = None
    allowed_tools: list[str] | None = None
    disallowed_tools: list[str] | None = None
    permission_mode: str | None = None
    session_id: str | None = None
    mcp_config: str | None = None
    add_dirs: list[str] | None = None
    timeout: int | None = None

    # 재시도
    max_retries: int = 0
    retry_count: int = 0
    exception_type: str | None = None       # 마지막 실패 예외 클래스명 (예: "BillingError")

    # 결과
    result: str | None = None
    error: str | None = None
    exit_code: int | None = None
    result_session_id: str | None = None
    usage: TokenUsage | None = None

    # 배치
    batch_id: str | None = None

    # 유저 메타
    metadata: dict[str, str | int | float | bool | None] = Field(default_factory=dict)

    # 타임스탬프 — datetime.now(timezone.utc) 사용
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: datetime | None = None
    finished_at: datetime | None = None


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"
    RETRYING = "retrying"


class Priority(int, Enum):
    HIGH = 1
    NORMAL = 5
    LOW = 9
```

#### 3.2.6 Broker

**AbstractBroker 인터페이스:**

```python
class AbstractBroker(ABC):
    # 큐
    async def enqueue(self, task: Task, *, delay: int | None = None) -> None: ...
    async def dequeue(self, queue_names: list[str], timeout: float = 1.0) -> Task | None: ...
    async def ack(self, queue_name: str, task_id: str) -> None: ...
    async def nack(self, queue_name: str, task_id: str) -> None: ...
    async def requeue(self, queue_name: str, task_ids: list[str]) -> None: ...

    # 상태/결과
    async def get_task(self, task_id: str) -> Task | None: ...
    async def update_task(self, task: Task) -> None: ...

    # 스트리밍
    async def publish_chunk(self, task_id: str, chunk: StreamEvent) -> None: ...
    async def subscribe_chunks(self, task_id: str) -> AsyncIterator[StreamEvent]: ...

    # DLQ
    async def list_dlq(self, queue_name: str, limit: int = 100) -> list[Task]: ...
    async def retry_from_dlq(self, queue_name: str, task_id: str) -> None: ...
    async def purge_dlq(self, queue_name: str) -> None: ...

    # 워커 관리
    async def register_worker(self, worker_id: str, queues: list[str]) -> None: ...
    async def heartbeat(self, worker_id: str) -> None: ...
    async def queue_size(self, queue_name: str) -> int: ...

    # 비용
    async def incr_cost(self, amount: float, worker_id: str | None = None) -> None: ...
    async def get_total_cost(self) -> float: ...
    async def get_worker_cost(self, worker_id: str) -> float: ...

    # 미들웨어 시그널
    async def emit_before(self, signal: str, *args, **kwargs) -> None: ...
    async def emit_after(self, signal: str, *args, **kwargs) -> None: ...

    # 라이프사이클
    async def connect(self) -> None: ...
    async def close(self) -> None: ...
```

**RedisBroker — 유일한 구현:**

```python
class RedisBroker(AbstractBroker):
    def __init__(
        self,
        url: str = "redis://localhost:6379",
        namespace: str = "open_kknaks",
        result_ttl: int = 3600,
        stream_maxlen: int = 1000,
    ): ...
```

**Redis 데이터 구조:**

```
{ns} = namespace (기본: "open_kknaks")

# 큐
{ns}:queue:{queue_name}            # Sorted Set (score = priority * 1e12 + timestamp)
{ns}:queue:{queue_name}.delayed    # Sorted Set (score = delay_until timestamp)
{ns}:queue:{queue_name}.active     # Set (현재 처리 중인 task_id)
{ns}:queue:{queue_name}.dlq        # List (Dead Letter Queue)

# 작업
{ns}:task:{task_id}                # Hash → JSON

# 스트리밍
{ns}:stream:{task_id}              # Redis Stream (청크 이벤트)

# 배치
{ns}:batch:{batch_id}              # Set (소속 task_id 목록)
{ns}:batch:{batch_id}:meta         # Hash (mode, total, done, failed)

# 워커
{ns}:workers                       # Hash (worker_id → JSON{queues, last_heartbeat})

# 비용
{ns}:cost:total                    # Float — 전체 누적 비용 (INCRBYFLOAT)
{ns}:cost:worker:{worker_id}       # Float — 워커별 누적 비용
{ns}:cost:daily:{YYYY-MM-DD}       # Float — 일별 비용
```

#### 3.2.7 Middleware

시그널 6개. 기본 제공 6개. 시그널 메서드에 **broker 인자를 직접 전달**한다.
미들웨어 생성자는 설정값만 받음 (broker를 생성자에서 받지 않음).

```python
class Middleware:
    async def before_enqueue(self, broker, task: Task) -> Task | None: ...
    async def after_enqueue(self, broker, task: Task) -> None: ...
    async def before_process(self, broker, task: Task) -> Task | None: ...
    async def after_process(self, broker, task: Task, *, result=None, exception=None) -> None: ...
    async def before_worker_boot(self, broker, worker) -> None: ...
    async def after_worker_shutdown(self, broker, worker) -> None: ...
```

**체인 동작 규칙:**

| 단계 | 실행 순서 | 중단 조건 |
|---|---|---|
| `before_*` | 등록 순서 (sequential) | 예외 발생 시 break — 이후 MW의 before 호출 안 함 |
| `after_*` | **역순** (스택) | 중단 없음 — 예외 시에도 **모든** MW의 after 호출 보장 |

- `RetriesMiddleware`는 `after_process`에서 재시도 판단 후 `broker.enqueue(delay=...)` 직접 호출
- 작업 상태 변경은 `StreamEvent` 타입 확장 없이 `Task.status`로 관장 (StreamEvent 타입은 text/cost/retry 유지)

| 미들웨어 | 설명 | 기본 활성화 |
|---|---|---|
| `LoggingMiddleware` | 작업 시작/완료/실패 structlog 로깅 | O |
| `RetriesMiddleware` | 지수 백오프 재시도 (min 5s → max 300s) + DLQ 이동 | O |
| `TimeoutMiddleware` | PTY 프로세스 SIGHUP → SIGTERM → SIGKILL | O |
| `CostMiddleware` | 3단계 비용 제어 (Task/Worker/전체) + 알림 | O |
| `RateLimitMiddleware` | 분당 최대 요청 수 제한 | 옵션 |
| `CallbackMiddleware` | 완료/실패 시 webhook 또는 함수 콜백 | 옵션 |

**CostMiddleware 3단계 비용 제어:**

```
1. Worker 단위: worker_budget_usd → 워커 누적 사용량 한도
2. Worker 단위: worker_budget_usd → 워커 누적 비용 한도
3. 전체 단위: global_budget_usd → namespace 전체 비용 한도 (Redis에 저장)
```

#### 3.2.8 MCP 서버

라이브러리 자체를 MCP 서버로 노출한다.

```
MCP Client (Claude Desktop, Cursor, etc.)
    │ MCP 프로토콜 (stdio / SSE)
    ▼
open_kknaks MCP Server
    │
    ▼
ClaudeClient → RedisBroker → ClaudeWorker → PTY Executor
```

**노출 MCP Tool:**

| Tool | 파라미터 | 설명 |
|---|---|---|
| `submit_task` | prompt, context?, queue?, priority?, model?, timeout? | 작업 등록 → task_id |
| `get_status` | task_id | 상태 조회 |
| `get_result` | task_id, wait? | 결과 조회 |
| `cancel_task` | task_id | 취소 |
| `stream_task` | task_id | 스트리밍 |
| `submit_batch` | tasks[], mode? | 배치 등록 |
| `list_tasks` | status?, limit? | 작업 목록 조회 |

---

## 4. CLI

```bash
# === 워커 실행 ===
open-kknaks worker \
    --broker redis://localhost:6379 \
    --namespace myapp \
    --queues error-analysis,general \
    --work-dir /my/backend \
    --model sonnet \
    --concurrency 4

# === 큐 관리 ===
open-kknaks queue list
open-kknaks queue size error-analysis
open-kknaks queue purge error-analysis

# === DLQ 관리 ===
open-kknaks dlq list error-analysis
open-kknaks dlq retry error-analysis --task-id abc123
open-kknaks dlq retry error-analysis --all
open-kknaks dlq purge error-analysis

# === 작업 조회 ===
open-kknaks task status abc123
open-kknaks task result abc123
open-kknaks task cancel abc123

# === 워커 상태 ===
open-kknaks worker list
```

---

## 5. 패키지 구조

```
open_kknaks/
├── __init__.py              # ClaudeClient, Task, ClaudeConfig export
├── client.py                # ClaudeClient (프로듀서)
├── config.py                # ClaudeConfig (Claude Code CLI 설정)
├── task.py                  # Task, TaskStatus, Priority, TaskResult, TokenUsage, StreamEvent
├── batch.py                 # BatchRunner, BatchStatus
├── broker/
│   ├── __init__.py          # AbstractBroker export
│   ├── base.py              # AbstractBroker (인터페이스)
│   ├── redis.py             # RedisBroker (유일한 구현)
│   └── lua/                 # Redis Lua 스크립트
│       ├── enqueue.lua
│       ├── dequeue.lua
│       ├── ack.lua
│       ├── nack.lua
│       ├── requeue.lua
│       └── maintenance.lua
├── worker/
│   ├── __init__.py
│   ├── worker.py            # ClaudeWorker
│   ├── executor.py          # ClaudeCodeExecutor (PTY 기반 CLI 실행)
│   ├── pty_process.py       # PTYProcess (단일 프로세스 래퍼 + 3단계 종료)
│   └── line_buffer.py       # LineBuffer (바이트 스트림 → 줄 단위 조립)
├── middleware/
│   ├── __init__.py
│   ├── base.py              # Middleware base class
│   ├── logging.py           # LoggingMiddleware
│   ├── retries.py           # RetriesMiddleware
│   ├── timeout.py           # TimeoutMiddleware
│   ├── cost.py              # CostMiddleware
│   ├── rate_limit.py        # RateLimitMiddleware
│   └── callback.py          # CallbackMiddleware
├── mcp/
│   ├── __init__.py
│   ├── server.py            # MCPServer
│   └── __main__.py          # python -m open_kknaks.mcp
├── cli/
│   ├── __init__.py
│   ├── main.py              # CLI 진입점 (typer)
│   ├── worker_cmd.py        # worker 서브커맨드
│   ├── queue_cmd.py         # queue 서브커맨드
│   ├── dlq_cmd.py           # dlq 서브커맨드
│   └── task_cmd.py          # task 서브커맨드
├── exceptions.py            # 예외 계층
└── py.typed
```

**프로젝트 루트:**

```
open_kknaks/                 # 위 패키지
tests/
├── conftest.py
├── test_client.py
├── test_worker.py
├── test_executor.py         # PTY Executor 테스트
├── test_pty_process.py      # PTYProcess 생명주기 테스트
├── test_line_buffer.py      # LineBuffer 테스트
├── test_broker_redis.py
├── test_middleware.py
├── test_batch.py
├── test_mcp.py
└── test_integration.py
docs/
├── PLAN.md
├── PRD.md                   # 이 문서
├── ARCHITECTURE_V2.md       # 상세 설계
├── CLAUDE_CODE_ANALYSIS.md  # Claude Code CLI 분석
└── DRAMATIQ_ANALYSIS.md     # Dramatiq 분석
pyproject.toml
README.md
LICENSE                      # MIT
CHANGELOG.md
.github/
└── workflows/
    ├── test.yml
    └── publish.yml
```

---

## 6. 의존성

### 6.1 필수 의존성

| 패키지 | 버전 | 용도 |
|---|---|---|
| Python | >= 3.10 | async/await, `X \| Y` 유니온 타입 |
| `pydantic` | >= 2.0 | Task/Config 데이터 검증 + 직렬화 |

### 6.2 선택적 의존성 (extras)

| extra | 패키지 | 용도 |
|---|---|---|
| `redis` | `redis[asyncio] >= 5.0` | RedisBroker |
| `mcp` | `mcp >= 1.0` | MCP 서버 |
| `cli` | `typer >= 0.12` | CLI 도구 |

```bash
pip install open-kknaks[redis]           # 기본 사용
pip install open-kknaks[redis,mcp]       # + MCP 서버
pip install open-kknaks[redis,cli]       # + CLI 도구
pip install open-kknaks[redis,mcp,cli]   # 전부
```

### 6.3 개발 의존성

| 패키지 | 용도 |
|---|---|
| `pytest` + `pytest-asyncio` | 테스트 |
| `ruff` | 린트 + 포맷 |
| `mypy` | 정적 타입 체크 |
| `coverage` | 커버리지 |

---

## 7. 전제 조건

| 항목 | 설명 |
|---|---|
| Claude Code CLI | 사용자 환경에 설치 + `claude login` 완료 (OAuth 인증) |
| Claude 구독 | Pro 또는 Max 플랜 (Claude Code 사용량 포함) |
| Python >= 3.10 | asyncio 기반 |
| Linux / macOS | PTY 필수 — **Windows 미지원** |
| Redis | RedisBroker 사용 시 필요 |

> **인증:** OAuth(`claude login`) 전용. API Key(`ANTHROPIC_API_KEY`)는 사용하지 않음.
> 이미 로컬에서 Claude Code를 쓰고 있다면 추가 로그인 없이 바로 사용 가능.

---

## 8. API 상세

### 8.1 기본 사용법

```python
import asyncio
from open_kknaks import ClaudeClient
from open_kknaks.broker import RedisBroker

async def main():
    client = ClaudeClient(
        broker=RedisBroker(url="redis://localhost:6379"),
    )

    task_id = await client.submit("이 코드의 버그를 찾아줘")
    result = await client.result(task_id)
    print(result.result)

asyncio.run(main())
```

### 8.2 스트리밍

```python
task_id = await client.submit("에러 로그 분석해줘", context=error_log)

async for event in client.stream(task_id):
    if event.text:
        print(event.text, end="", flush=True)
```

### 8.3 멀티 큐 + 워커 분리

```python
# === producer.py ===
client = ClaudeClient(broker=RedisBroker(url="redis://myserver:6379"))
await client.submit("PR 리뷰", queue="pr-review", context=pr_diff)
await client.submit("에러 분석", queue="error-analysis", context=error_log)

# === worker_review.py ===
worker = ClaudeWorker(
    broker=RedisBroker(url="redis://myserver:6379"),
    queues=["pr-review"],
    claude=ClaudeConfig(work_dir="/my/frontend", model="opus"),
    concurrency=2,
)
await worker.run()

# === worker_error.py ===
worker = ClaudeWorker(
    broker=RedisBroker(url="redis://myserver:6379"),
    queues=["error-analysis"],
    claude=ClaudeConfig(work_dir="/my/backend", model="sonnet"),
    concurrency=4,
)
await worker.run()
```

### 8.4 배치 작업

```python
batch_id = await client.batch_submit(
    tasks=[
        {"prompt": "이슈 #101 분석", "context": ctx1},
        {"prompt": "이슈 #102 분석", "context": ctx2},
        {"prompt": "이슈 #103 분석", "context": ctx3},
    ],
    queue="error-analysis",
    mode="parallel",
)

results = await client.batch_wait(batch_id, timeout=1800)
```

### 8.5 우선순위 + 지연 실행

```python
await client.submit("프로덕션 에러!", context=crash_log, priority="high")
await client.submit("비긴급 리팩토링", delay_seconds=30, priority="low")
```

### 8.6 세션 이어가기

```python
result1 = await client.result(
    await client.submit("프로젝트 구조 분석해줘")
)
session = result1.session_id

result2 = await client.result(
    await client.submit("아까 분석 기반으로 리팩토링", session_id=session)
)
```

### 8.7 커스텀 미들웨어

```python
from open_kknaks.middleware import Middleware

class SlackNotifyMiddleware(Middleware):
    async def after_process(self, broker, task, *, result=None, exception=None):
        if exception is None:
            await send_slack(f"작업 완료: {task.prompt[:50]}")
        else:
            await send_slack(f"작업 실패: {task.prompt[:50]} - {exception}")

worker = ClaudeWorker(
    broker=broker,
    queues=["general"],
    claude=ClaudeConfig(work_dir="/my/project"),
    middlewares=[SlackNotifyMiddleware()],
)
```

### 8.8 MCP 서버

```python
from open_kknaks.mcp import MCPServer

server = MCPServer(
    broker=RedisBroker(url="redis://localhost:6379"),
    transport="stdio",
)
server.run()
```

```json
{
  "mcpServers": {
    "open_kknaks": {
      "command": "python",
      "args": ["-m", "open_kknaks.mcp", "--broker", "redis://localhost:6379"]
    }
  }
}
```

---

## 9. 에러 처리

### 9.1 예외 계층

```
OpenKnaksError (base)
├── ClaudeNotFoundError          # claude 바이너리 없음
├── ClaudeAuthError              # 로그인 안 됨 (API 401)
├── BillingError                 # API 결제/한도 문제 (API 402) — 워커 즉시 중단
├── TaskError
│   ├── TaskNotFoundError        # task_id 없음
│   ├── TaskTimeoutError         # 전체 타임아웃 초과
│   ├── TaskCancelledError       # 취소됨
│   ├── TaskFailedError          # CLI 비정상 종료
│   └── IdleTimeoutError         # PTY 무응답 (행 감지)
├── BatchError
│   ├── BatchNotFoundError
│   └── BatchPartialFailureError
├── BrokerError
│   ├── BrokerConnectionError    # Redis 연결 실패
│   └── BrokerTimeoutError
├── PTYError                     # PTY 생성/fork 실패
└── ConfigError
```

**API 에러 → 예외 매핑:**

| stream-json `error` | HTTP 상태 | 예외 | 재시도 | 대응 |
|---|---|---|---|---|
| `rate_limit` | 429 | (예외 없음) | CLI 자동 재시도 | RateLimitMiddleware 감속 |
| `billing_error` | 402 | `BillingError` | 재시도 안 함 | 워커 중단 + 알림 |
| `authentication_failed` | 401 | `ClaudeAuthError` | 재시도 안 함 | 워커 중단 + 알림 |
| `server_error` | 500/529 | (예외 없음) | CLI 자동 재시도 | 로그 기록 |
| `max_output_tokens` | — | (예외 없음) | 해당 없음 | 로그 기록 (제어 불가) |

### 9.2 재시도 정책

```python
# RetriesMiddleware 기본 동작:
# - 지수 백오프: min_backoff * (backoff_factor ^ retry_count)
# - 범위: 5초 → 10초 → 20초 → ... → 최대 300초
# - 재시도 제외: TaskCancelledError, ClaudeAuthError
# - max_retries 초과 시 → DLQ 이동
```

---

## 10. 유즈케이스

### 10.1 서버 에러 → Claude Code 분석 → Slack 알림

```python
from open_kknaks import ClaudeClient
from open_kknaks.broker import RedisBroker

client = ClaudeClient(broker=RedisBroker())

@app.exception_handler(Exception)
async def handle_error(request, exc):
    await client.submit(
        "이 에러를 분석하고 수정 방안을 제시해줘",
        context=f"Error: {exc}\nTraceback: {traceback.format_exc()}",
        queue="error-analysis",
        priority="high",
    )
    return JSONResponse(status_code=500, content={"error": "Internal Server Error"})
```

### 10.2 CI 실패 → 원인 분석 → PR 코멘트

```python
client = ClaudeClient(broker=RedisBroker())

task_id = await client.submit(
    "CI 테스트 실패 원인을 분석하고 수정 코드를 제안해줘",
    context=test_output,
    queue="ci-analysis",
    model="sonnet",
    allowed_tools=["Read", "Bash(git log *)", "Bash(git diff *)"],
)
result = await client.result(task_id)
await github.create_pr_comment(pr_number, result.result)
```

### 10.3 다중 에이전트 팀 (app_builder_local 패턴)

```python
# 기획 에이전트
await client.submit(
    "PRD 작성해줘",
    context=idea_text,
    queue="planner",
    append_system_prompt="You are a planner agent.",
)

# 백엔드/프론트엔드 병렬 리뷰
batch_id = await client.batch_submit(
    tasks=[
        {"prompt": "백엔드 관점에서 리뷰", "queue": "backend-review"},
        {"prompt": "프론트엔드 관점에서 리뷰", "queue": "frontend-review"},
    ],
    mode="parallel",
)
```

---

## 11. 테스트 전략

### 11.1 테스트 레이어

| 레이어 | 범위 | 방법 |
|---|---|---|
| **Unit** | PTYProcess, LineBuffer, Task, Middleware | mock, 직접 PTY 테스트 |
| **Unit** | Broker | mock redis (fakeredis) |
| **Integration** | Worker + PTY Executor + Broker | fakeredis + mock subprocess |
| **E2E** | 실제 Claude Code CLI 호출 | `claude -p` 실행 (CI에서는 skip) |
| **Redis** | RedisBroker | testcontainers 또는 실제 Redis |

### 11.2 PTY 테스트

```python
# PTYProcess 생명주기 테스트
async def test_pty_process_terminate():
    """fork된 자식 + 손자 프로세스가 SIGHUP으로 모두 정리되는지 검증."""
    master_fd, slave_fd = pty.openpty()
    pid = os.fork()
    if pid == 0:
        os.setsid()
        # 손자 프로세스 생성
        os.fork()
        time.sleep(60)
        os._exit(0)

    os.close(slave_fd)
    process = PTYProcess(pid=pid, master_fd=master_fd, pgid=pid, task_id="test")
    exit_code = await process.terminate()

    # 자식 + 손자 모두 종료 확인
    assert not process.is_alive()
```

### 11.3 CI 설정

```yaml
- pytest -x --timeout=60 -m "not e2e"     # 기본: e2e 제외
- pytest -x --timeout=300 -m "e2e"         # 수동 트리거: e2e 포함
```

---

## 12. PyPI 배포

### 12.1 패키지 메타데이터

```toml
[project]
name = "open-kknaks"
version = "0.1.0"
description = "PTY-based task queue library for Claude Code CLI"
readme = "README.md"
license = "MIT"
requires-python = ">=3.10"
authors = [{ name = "kknaks" }]
keywords = ["claude", "claude-code", "task-queue", "pty", "ai", "automation"]
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
    "Operating System :: POSIX",
]

dependencies = [
    "pydantic>=2.0",
]

[project.optional-dependencies]
redis = ["redis[asyncio]>=5.0"]
mcp = ["mcp>=1.0"]
cli = ["typer>=0.12"]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.24",
    "fakeredis[aioredis]>=2.0",
    "ruff>=0.8",
    "mypy>=1.13",
    "coverage>=7.0",
]

[project.scripts]
open-kknaks = "open_kknaks.cli.main:app"

[project.urls]
Homepage = "https://github.com/kknaks/open_kknaks"
Repository = "https://github.com/kknaks/open_kknaks"
Issues = "https://github.com/kknaks/open_kknaks/issues"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```

### 12.2 버전 전략

- **0.1.0** — MVP (PTY Executor + RedisBroker + 단일/배치 작업 + 스트리밍)
- **0.2.0** — 미들웨어 6종 + CLI 도구
- **0.3.0** — MCP 서버
- **1.0.0** — API 안정화 + 커버리지 >= 80%

---

## 13. 마일스톤

### Sprint 1 — PTY Executor + 코어 (1.5주)

| 작업 | 설명 | 산출물 |
|---|---|---|
| S1-1 | 프로젝트 세팅 (pyproject.toml, ruff, mypy, CI) | 빌드/린트/테스트 통과 |
| S1-2 | Task, TaskStatus, Priority, TaskResult, TokenUsage 모델 | `task.py` |
| S1-3 | ClaudeConfig 모델 | `config.py` |
| S1-4 | LineBuffer (바이트 → 줄 단위 조립) | `worker/line_buffer.py` |
| S1-5 | PTYProcess (fork + setsid + 3단계 종료) | `worker/pty_process.py` |
| S1-6 | ClaudeCodeExecutor (PTY 기반 실행 + stream-json 파싱) | `worker/executor.py` |
| S1-7 | AbstractBroker + RedisBroker | `broker/` |
| S1-8 | ClaudeWorker (큐 소비 + PTY executor + concurrency) | `worker/worker.py` |
| S1-9 | ClaudeClient (submit/stream/result/cancel) | `client.py` |
| S1-10 | Unit + Integration 테스트 | `tests/` |

**완료 기준:** PTY로 Claude Code 스폰 → stream-json 파싱 → Redis 경유 결과 반환 동작 확인. PTYProcess 3단계 종료 + 고아 프로세스 정리 검증.

### Sprint 2 — 미들웨어 + 배치 (1주)

| 작업 | 설명 | 산출물 |
|---|---|---|
| S2-1 | Middleware ABC + 파이프라인 (6개 시그널) | `middleware/base.py` |
| S2-2 | LoggingMiddleware, RetriesMiddleware, TimeoutMiddleware | `middleware/` |
| S2-3 | CostMiddleware (3단계 비용 제어) | `middleware/cost.py` |
| S2-4 | RateLimitMiddleware, CallbackMiddleware | `middleware/` |
| S2-5 | BatchRunner (parallel / sequential) | `batch.py` |
| S2-6 | DLQ 관리 (nack → DLQ, retry, purge) | `broker/redis.py` |
| S2-7 | Lua 스크립트 (enqueue/dequeue/ack/nack/requeue/maintenance) | `broker/lua/` |
| S2-8 | 미들웨어 + 배치 테스트 | `tests/` |

**완료 기준:** 미들웨어 체인 동작, 3단계 비용 추적, DLQ 이동/재시도 확인.

### Sprint 3 — CLI + MCP + 배포 (1주)

| 작업 | 설명 | 산출물 |
|---|---|---|
| S3-1 | CLI (worker/queue/dlq/task 서브커맨드) | `cli/` |
| S3-2 | MCPServer (stdio + SSE) | `mcp/` |
| S3-3 | `python -m open_kknaks.mcp` 실행 지원 | `mcp/__main__.py` |
| S3-4 | README.md (Quick Start, API Reference) | `README.md` |
| S3-5 | PyPI 배포 (GitHub Actions) | `.github/workflows/publish.yml` |
| S3-6 | E2E 테스트 + 커버리지 >= 70% | CI |

**완료 기준:** `pip install open-kknaks[redis]` → 즉시 사용 가능, MCP 연동 확인.

### 전체 일정: 3.5주

```
S1 (1.5주) ──── S2 (1주) ──── S3 (1주)
 PTY+코어       MW+배치+DLQ    CLI+MCP+배포
```

---

## 14. 제외 범위 (Non-Goals)

| 항목 | 이유 |
|---|---|
| Windows 지원 | PTY는 POSIX 전용. Windows는 향후 ConPTY 또는 subprocess fallback으로 검토 |
| InMemoryBroker | 프로듀서/워커 분리 원칙에 위배. 테스트는 mock/fakeredis 사용 |
| 트리거 시스템 (webhook, cron) | 유저 코드에서 붙이는 영역 |
| 웹 대시보드 UI | 라이브러리 범위 초과 |
| Claude Code 이외의 LLM 실행 | 단일 책임 유지 |
| Claude Code CLI 설치/로그인 | 전제 조건 |
| RabbitMQ / SQS 등 추가 브로커 | MVP 이후 커뮤니티 기여로 확장 |

---

## 15. 리스크

| 리스크 | 확률 | 영향 | 대응 |
|---|---|---|---|
| Claude Code CLI 인터페이스 변경 | 중 | 높음 | stream-json 파싱을 `stream_parser` 모듈로 분리하여 교체 용이하게 설계 |
| PTY 플랫폼 이슈 (macOS vs Linux) | 중 | 중 | CI에서 양쪽 OS 테스트. `os.openpty()` 대신 `pty.openpty()` 사용 |
| PTY fork 안정성 (asyncio 이벤트 루프 내) | 중 | 높음 | fork 전에 이벤트 루프 상태 정리. 전용 스레드에서 fork 검토 |
| API rate limit | 중 | 중 | RateLimitMiddleware 기본 제공 |
| Redis 의존성 | 낮음 | 낮음 | Redis는 운영 환경에서 사실상 표준 |
| MCP SDK 변경 | 중 | 중 | optional 분리, 버전 고정 |

---

## 부록 A: v1 → v2 변경 요약

| 항목 | v1 PRD | v2 PRD |
|---|---|---|
| 진입점 | `ClaudeRunner` 일체형 | `ClaudeClient` + `ClaudeWorker` 분리 |
| Executor | subprocess PIPE | **PTY 기반** (fork + setsid) |
| 프로세스 종료 | SIGTERM → SIGKILL (2단계) | **SIGHUP → SIGTERM → SIGKILL** (3단계) |
| 큐 | 단일 | **멀티 큐 라우팅** |
| 브로커 | AbstractBroker + InMemory + Redis | **AbstractBroker + RedisBroker** (InMemory 제거) |
| DLQ | 없음 | **큐별 DLQ** |
| ack/nack | ack만 | **ack + nack + requeue** |
| 헬스체크 | 없음 | **heartbeat + 좀비 워커 감지** |
| 미들웨어 시그널 | 6+2개 | **6개** (통합) |
| 비용 제어 | 단순 추적 | **3단계** (Task/Worker/전체) |
| 설정 | Runner 파라미터 | **ClaudeConfig 분리** (재사용 가능) |
| CLI | 없음 | **4개 서브커맨드** (worker/queue/dlq/task) |
| 행 감지 | 없음 | **idle_timeout** (PTY 무응답 감지) |
| 플랫폼 | Linux/macOS/Windows | **Linux/macOS** (PTY 필수) |
| 스프린트 | 4.5주 (4 sprint) | **3.5주** (3 sprint) |

## 부록 B: 근거 문서

| 문서 | 내용 |
|---|---|
| `ARCHITECTURE_V2.md` | 상세 기술 설계 (PTY Executor, Broker, Middleware, Worker 내부 구조) |
| `CLAUDE_CODE_ANALYSIS.md` | Claude Code CLI 프로그래밍 제어 분석 + app_builder_local / persona_counselor 구현 사례 |
| `DRAMATIQ_ANALYSIS.md` | Dramatiq 구조 분석 및 차용 근거 |
