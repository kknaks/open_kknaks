# open_kknaks — Architecture v2 (상용 설계)

> v1 PRD를 폐기하고 상용 레벨로 재설계한다.
> Dramatiq 분석 결과를 반영하되, Claude Code CLI 전용 특성에 맞게 변형한다.
> **불필요한 추상화를 제거하고 실제 구현에 필요한 것만 남긴다.**

---

## 1. 설계 원칙

1. **프로듀서/워커 완전 분리** — submit하는 코드와 실행하는 코드는 별개 프로세스
2. **멀티 큐 라우팅** — 워커가 특정 큐만 소비. 큐 = 작업 유형/환경 단위
3. **at-least-once delivery** — 작업은 ack 전까지 유실되지 않음
4. **수평 확장** — 같은 큐에 워커 N대 붙이면 처리량 N배
5. **Redis 직접 구현** — 브로커 추상화 없음. Redis 하나만 제대로 만든다

---

## 2. 컴포넌트 (5개만)

```
ClaudeClient ──enqueue──▶ RedisBroker ◀──dequeue── ClaudeWorker
                              │                        │
                              │                   Executor
                              │                   (claude -p)
                         Middleware
                     (Logging, Retries,
                         Timeout)
```

전체 구조:

```
┌─────────────────────────────────────────────────────────┐
│                     유저 코드 (프로듀서)                    │
│                                                         │
│  client = ClaudeClient(broker=RedisBroker(...))         │
│  await client.submit("분석해줘", queue="error-analysis") │
│  await client.submit("리뷰해줘", queue="pr-review")      │
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
│ model: sonnet    │    │ model: opus      │
│ concurrency: 4   │    │ concurrency: 2   │
│                  │    │                  │
│ ┌──────────────┐ │    │ ┌──────────────┐ │
│ │ Executor     │ │    │ │ Executor     │ │
│ │ claude -p .. │ │    │ │ claude -p .. │ │
│ └──────────────┘ │    │ └──────────────┘ │
└──────────────────┘    └──────────────────┘
```

---

## 3. ClaudeClient (프로듀서)

작업을 큐에 넣기만 한다. 워커를 실행하지 않는다.

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

**ClaudeClient는 Claude Code CLI와 무관.** Broker에 Task를 넣고, 상태/결과를 조회하는 얇은 클라이언트.

---

## 4. ClaudeWorker (소비자)

큐에서 Task를 꺼내 Claude Code CLI를 실행한다.

```python
from open_kknaks.worker import ClaudeWorker
from open_kknaks.broker import RedisBroker

worker = ClaudeWorker(
    broker=RedisBroker(url="redis://localhost:6379", namespace="myapp"),
    
    # 어떤 큐를 소비할지
    queues=["error-analysis", "general"],
    
    # Claude Code CLI 실행 환경 (워커 기본값)
    work_dir="/my/backend",
    claude_bin=None,                      # PATH 자동 탐색
    model="sonnet",
    allowed_tools=["Read", "Bash(git log *)", "Bash(git diff *)"],
    append_system_prompt="You are a backend error analyst. Be concise.",
    max_turns=10,
    permission_mode="default",
    bare=True,
    
    # 워커 설정
    concurrency=4,                        # 동시 Claude Code 프로세스 수
    poll_interval=0.5,                    # 큐 폴링 간격 (초)
    heartbeat_interval=30,                # 헬스체크 간격 (초)
    shutdown_timeout=300,                 # 그레이스풀 셧다운 대기 (초)
)

# 워커 실행 (블로킹)
await worker.run()
```

**워커 기본값 vs Task 오버라이드:**
```
최종 실행 설정 = Worker 기본값 ← Task 오버라이드 (Task에 명시된 것만 덮어씀)

예: Worker(model="sonnet") + Task(model=None)  → sonnet
    Worker(model="sonnet") + Task(model="opus") → opus
```

### 4.1 Worker 내부 구조

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
  │   │  executor.execute(task)
  │   │  emit_after("process", result | exception)
  │   │  ack 또는 nack
  │   └─ 실패 시: Retries 미들웨어가 delay 재큐잉 또는 DLQ
  │
  ├─ HeartbeatLoop (asyncio.Task × 1)
  │   └─ broker.heartbeat(worker_id) 주기적 호출
  │
  └─ SignalHandler
      ├─ SIGTERM → graceful shutdown
      └─ SIGINT  → graceful shutdown (2번 누르면 즉시 종료)
```

### 4.2 _process_task 흐름

```python
async def _process_task(self, task: Task):
    try:
        # 상태: RUNNING
        task.status = TaskStatus.RUNNING
        task.started_at = datetime.utcnow()
        await self.broker.update_task(task)
        
        # 미들웨어: before_process
        await self.broker.emit_before("process", task)
        
        # 실행 설정 병합 (Worker 기본값 + Task 오버라이드)
        config = self._merge_config(task)
        
        # Claude Code CLI 실행
        result = await self.executor.execute(
            task=task,
            config=config,
            on_chunk=lambda chunk: self.broker.publish_chunk(task.id, chunk),
        )
        
        # 성공
        task.status = TaskStatus.DONE
        task.result = result.output
        task.exit_code = result.exit_code
        task.session_id = result.session_id
        task.usage = result.usage
        task.finished_at = datetime.utcnow()
        await self.broker.update_task(task)
        await self.broker.emit_after("process", task, result=result)
        await self.broker.ack(task.queue, task.id)
        
    except TaskCancelledError:
        task.status = TaskStatus.CANCELLED
        task.finished_at = datetime.utcnow()
        await self.broker.update_task(task)
        await self.broker.ack(task.queue, task.id)
        
    except Exception as e:
        task.status = TaskStatus.FAILED
        task.error = str(e)
        task.finished_at = datetime.utcnow()
        await self.broker.update_task(task)
        
        # 미들웨어가 재시도 판단
        await self.broker.emit_after("process", task, exception=e)
        
        # 재시도 안 됐으면 → DLQ
        if task.status == TaskStatus.FAILED:
            await self.broker.nack(task.queue, task.id)
```

### 4.3 그레이스풀 셧다운

```
stop() 호출
  │
  ├─ 1) _running = False → dequeue 루프 정지
  │
  ├─ 2) 실행 중 작업 완료 대기 (shutdown_timeout)
  │     ├─ timeout 내 완료 → 정상 ack
  │     └─ timeout 초과 → Claude Code 프로세스 SIGTERM → 5초 → SIGKILL
  │
  ├─ 3) internal queue에 남은 미처리 Task → broker.requeue()
  │
  └─ 4) broker.close()
```

---

## 5. RedisBroker

추상 클래스 없이 Redis 직접 구현. 나중에 다른 브로커가 필요하면 그때 인터페이스를 추출한다.

```python
class RedisBroker:
    def __init__(
        self,
        url: str = "redis://localhost:6379",
        namespace: str = "open_kknaks",
        result_ttl: int = 3600,
        stream_maxlen: int = 1000,
    ): ...
    
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
    async def nack(self, queue_name: str, task_id: str) -> None: ...  # → DLQ 이동
    
    # 워커 관리
    async def register_worker(self, worker_id: str, queues: list[str]) -> None: ...
    async def heartbeat(self, worker_id: str) -> None: ...
    
    # 미들웨어 시그널
    async def emit_before(self, signal: str, *args, **kwargs) -> None: ...
    async def emit_after(self, signal: str, *args, **kwargs) -> None: ...
    
    # 라이프사이클
    async def connect(self) -> None: ...
    async def close(self) -> None: ...
```

### 5.1 Redis 데이터 구조

```
{ns} = namespace (기본: "open_kknaks")

# 큐
{ns}:queue:{queue_name}            # Sorted Set (score = priority * 1e12 + timestamp)
{ns}:queue:{queue_name}.delayed    # Sorted Set (score = delay_until timestamp)
{ns}:queue:{queue_name}.active     # Set (현재 처리 중인 task_id)
{ns}:queue:{queue_name}.dlq        # List (Dead Letter Queue)

# 작업
{ns}:task:{task_id}                # Hash → JSON (pydantic model_dump_json)

# 스트리밍
{ns}:stream:{task_id}              # Redis Stream (청크 이벤트)

# 배치
{ns}:batch:{batch_id}              # Set (소속 task_id 목록)
{ns}:batch:{batch_id}:meta         # Hash (mode, total, done, failed)

# 워커
{ns}:workers                       # Hash (worker_id → JSON{queues, last_heartbeat})
```

### 5.2 핵심 Lua 스크립트

**enqueue:**
```lua
-- score = priority * 1e12 + timestamp
ZADD {ns}:queue:{queue} score task_id
HSET {ns}:task:{task_id} data (task JSON)
```

**dequeue:**
```lua
local task_id = ZPOPMIN {ns}:queue:{queue}
SADD {ns}:queue:{queue}.active task_id
RETURN task_id
```

**ack:**
```lua
SREM {ns}:queue:{queue}.active task_id
EXPIRE {ns}:task:{task_id} result_ttl
```

**nack → DLQ:**
```lua
SREM {ns}:queue:{queue}.active task_id
RPUSH {ns}:queue:{queue}.dlq task_id
```

**requeue (셧다운 시):**
```lua
SREM {ns}:queue:{queue}.active task_id
ZADD {ns}:queue:{queue} original_score task_id
```

**좀비 워커 감지 (maintenance):**
```lua
-- heartbeat_timeout 초과한 워커의 active task → requeue
FOR worker IN HGETALL {ns}:workers:
    IF now - worker.last_heartbeat > timeout:
        tasks = SMEMBERS {ns}:worker:{id}:active
        requeue all tasks
        cleanup worker
```

---

## 6. Task 모델

```python
class Task(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    prompt: str
    context: str | None = None
    
    # 라우팅
    queue: str = "default"
    
    # 상태
    status: TaskStatus = TaskStatus.PENDING
    priority: Priority = Priority.NORMAL
    delay_until: datetime | None = None
    
    # 실행 옵션 (None이면 Worker 기본값)
    work_dir: str | None = None
    model: str | None = None
    allowed_tools: list[str] | None = None
    append_system_prompt: str | None = None
    max_turns: int | None = None
    permission_mode: str | None = None
    bare: bool | None = None
    timeout: int | None = None
    
    # 재시도
    max_retries: int = 0
    retry_count: int = 0
    
    # 결과
    result: str | None = None
    error: str | None = None
    exit_code: int | None = None
    session_id: str | None = None
    usage: TokenUsage | None = None
    
    # 배치
    batch_id: str | None = None
    
    # 유저 메타
    metadata: dict[str, str | int | float | bool | None] = Field(default_factory=dict)
    
    # 타임스탬프
    created_at: datetime = Field(default_factory=datetime.utcnow)
    started_at: datetime | None = None
    finished_at: datetime | None = None
```

---

## 7. 미들웨어

시그널 6개. 기본 제공 3개.

### 7.1 시그널

```python
class Middleware:
    async def before_enqueue(self, broker, task: Task) -> Task | None:
        """큐 등록 전. None 반환 시 취소."""
        return task
    
    async def after_enqueue(self, broker, task: Task) -> None:
        """큐 등록 후."""
    
    async def before_process(self, broker, task: Task) -> Task | None:
        """실행 전. None 반환 시 skip."""
        return task
    
    async def after_process(self, broker, task: Task, *,
                            result=None, exception=None) -> None:
        """실행 후. 성공이면 result, 실패면 exception."""
    
    async def before_worker_boot(self, broker, worker) -> None: ...
    async def after_worker_shutdown(self, broker, worker) -> None: ...
```

### 7.2 기본 제공 미들웨어 (3개)

**LoggingMiddleware** — 작업 시작/완료/실패 structlog 로깅

**RetriesMiddleware** — 지수 백오프 재시도
```python
class RetriesMiddleware(Middleware):
    def __init__(
        self,
        max_retries: int = 3,
        min_backoff: float = 5.0,       # 초
        max_backoff: float = 300.0,     # 초
        backoff_factor: float = 2.0,
        no_retry_on: tuple = (TaskCancelledError, ClaudeAuthError),
    ): ...
    
    async def after_process(self, broker, task, *, result=None, exception=None):
        if exception is None:
            return
        if isinstance(exception, self.no_retry_on):
            return
        if task.retry_count >= (task.max_retries or self.max_retries):
            return
        
        delay = min(self.min_backoff * (self.backoff_factor ** task.retry_count), self.max_backoff)
        task.retry_count += 1
        task.status = TaskStatus.RETRYING
        await broker.update_task(task)
        await broker.enqueue(task, delay=int(delay))
```

**TimeoutMiddleware** — subprocess SIGTERM → 5초 → SIGKILL

나머지 (RateLimit, Callback, CostTracking 등)는 유저가 필요시 직접 구현.

---

## 8. CLI

`open-kknaks worker`만 제공. 나머지는 Python API로.

```bash
# 워커 실행
open-kknaks worker \
    --broker redis://localhost:6379 \
    --namespace myapp \
    --queues error-analysis,general \
    --work-dir /my/backend \
    --model sonnet \
    --concurrency 4

# 환경변수로도 가능
OPEN_KKNAKS_BROKER_URL=redis://localhost:6379 \
OPEN_KKNAKS_NAMESPACE=myapp \
OPEN_KKNAKS_QUEUES=error-analysis,general \
open-kknaks worker
```

큐/DLQ/Task 관리는 Python API:
```python
# 큐 사이즈
size = await broker.queue_size("error-analysis")

# DLQ 조회
dlq_tasks = await broker.list_dlq("error-analysis")

# DLQ에서 재시도
await broker.retry_from_dlq("error-analysis", task_id)

# Task 취소
await client.cancel(task_id)
```

---

## 9. 패키지 구조

```
open_kknaks/
├── __init__.py              # ClaudeClient, Task, TaskStatus export
├── client.py                # ClaudeClient (프로듀서)
├── task.py                  # Task, TaskStatus, Priority, TaskResult, TokenUsage, StreamEvent
├── batch.py                 # BatchRunner, BatchStatus
├── broker.py                # RedisBroker (단일 파일)
├── worker/
│   ├── __init__.py
│   ├── worker.py            # ClaudeWorker
│   └── executor.py          # ClaudeCodeExecutor (CLI 실행)
├── middleware/
│   ├── __init__.py
│   ├── base.py              # Middleware base class
│   ├── logging.py           # LoggingMiddleware
│   ├── retries.py           # RetriesMiddleware
│   └── timeout.py           # TimeoutMiddleware
├── lua/                     # Redis Lua 스크립트
│   ├── enqueue.lua
│   ├── dequeue.lua
│   ├── ack.lua
│   ├── nack.lua
│   ├── requeue.lua
│   └── maintenance.lua
├── mcp/
│   ├── __init__.py
│   ├── server.py            # MCPServer
│   └── __main__.py          # python -m open_kknaks.mcp
├── cli.py                   # CLI (worker 서브커맨드만)
├── exceptions.py            # 예외 계층
└── py.typed
```

**v1 대비 제거된 것:**
- `broker/base.py` (AbstractBroker) → 없음. RedisBroker 직접
- `broker/memory.py` (InMemoryBroker) → 없음. 테스트는 mock
- `config.py` (ExecutionConfig) → Worker에서 직접 병합
- `middleware/cost.py`, `rate_limit.py`, `callback.py`, `age_limit.py` → 유저 구현
- `cli/` 디렉토리 (4개 서브커맨드) → `cli.py` 단일 파일 (worker만)
- `worker/process_manager.py` → executor.py에 통합

---

## 10. 변경 요약 (v1 → v2 slim)

| 항목 | v1 PRD | v2 slim |
|---|---|---|
| 진입점 | `ClaudeRunner` 일체형 | `ClaudeClient` + `ClaudeWorker` 분리 |
| 큐 | 단일 | 멀티 큐 라우팅 |
| 브로커 | AbstractBroker + InMemory + Redis | **RedisBroker만** |
| DLQ | 없음 | 큐별 DLQ |
| ack/nack | ack만 | ack + nack + requeue |
| 셧다운 | SIGTERM만 | requeue + 실행 중 대기 |
| 헬스체크 | 없음 | heartbeat + 좀비 감지 |
| 미들웨어 시그널 | 14개 | **6개** |
| 기본 미들웨어 | 7개 | **3개** (Logging, Retries, Timeout) |
| CLI | 4개 서브커맨드 | **worker만** |
| 설정 | TOML + 환경변수 + Python | **Python + 환경변수** |
| 추상화 | AbstractBroker, AbstractExecutor, ExecutionConfig | **없음. 구체 클래스 직접** |
