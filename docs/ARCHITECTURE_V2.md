# open_kknaks — Architecture v2 (상용 설계)

> v1 PRD를 폐기하고 상용 레벨로 재설계한다.
> Dramatiq 분석 결과를 반영하되, Claude Code CLI 전용 특성에 맞게 변형한다.
> **과도한 추상화는 제거하되, 확장 가능한 인터페이스는 유지한다.**

---

## 1. 설계 원칙

1. **프로듀서/워커 완전 분리** — submit하는 코드와 실행하는 코드는 별개 프로세스
2. **멀티 큐 라우팅** — 워커가 특정 큐만 소비. 큐 = 작업 유형/환경 단위
3. **at-least-once delivery** — 작업은 ack 전까지 유실되지 않음
4. **수평 확장** — 같은 큐에 워커 N대 붙이면 처리량 N배
5. **브로커 추상화** — AbstractBroker 인터페이스 제공. 기본 구현은 Redis. InMemory는 제공하지 않음 (테스트는 mock)

---

## 2. 핵심 컴포넌트

```
ClaudeClient ──enqueue──▶ RedisBroker ◀──dequeue── ClaudeWorker
                              │                        │
                              │                   ClaudeConfig
                              │                        │
                              │                   Executor
                              │                   (claude -p)
                         Middleware
                   (Logging, Retries, Timeout,
                    Cost, RateLimit, Callback)
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
    
    # Claude Code 설정 (분리된 객체)
    claude=ClaudeConfig(
        work_dir="/my/backend",
        claude_bin=None,                      # PATH 자동 탐색
        model="sonnet",
        system_prompt=None,                   # 전체 교체 (--system-prompt)
        append_system_prompt="You are a backend error analyst. Be concise.",
        max_turns=10,
        max_budget_usd=1.0,
        effort="high",                        # low/medium/high/max
        json_schema=None,
        allowed_tools=["Read", "Bash(git log *)", "Bash(git diff *)"],
        disallowed_tools=None,
        permission_mode="default",
        mcp_config=None,
        add_dirs=None,
        bare=True,
    ),
    
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
        task.result_session_id = result.session_id
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

### 4.4 Executor — CLI 플래그 빌드

Worker 기본값과 Task 오버라이드를 병합한 뒤, Claude Code CLI 플래그로 변환한다.

```python
def _build_command(self, task: Task, config: MergedConfig) -> list[str]:
    cmd = [self.claude_bin, "-p", task.prompt]
    cmd += ["--output-format", "stream-json"]
    
    # 필수
    if config.bare:
        cmd.append("--bare")
    
    # LLM / 프롬프트
    if config.model:
        cmd += ["--model", config.model]
    if config.system_prompt:
        cmd += ["--system-prompt", config.system_prompt]
    if config.append_system_prompt:
        cmd += ["--append-system-prompt", config.append_system_prompt]
    if config.max_turns:
        cmd += ["--max-turns", str(config.max_turns)]
    if config.max_budget_usd:
        cmd += ["--max-budget-usd", str(config.max_budget_usd)]
    if config.effort:
        cmd += ["--effort", config.effort]
    if config.json_schema:
        cmd += ["--json-schema", config.json_schema]
    
    # 도구 / 권한
    if config.allowed_tools:
        cmd += ["--allowedTools"] + config.allowed_tools
    if config.disallowed_tools:
        cmd += ["--disallowedTools"] + config.disallowed_tools
    if config.permission_mode == "bypassPermissions":
        cmd.append("--dangerously-skip-permissions")
    elif config.permission_mode and config.permission_mode != "default":
        cmd += ["--permission-mode", config.permission_mode]
    
    # 세션 / 환경
    if task.session_id:
        cmd += ["--resume", task.session_id]
    if config.mcp_config:
        cmd += ["--mcp-config", config.mcp_config]
    if config.add_dirs:
        cmd += ["--add-dir"] + config.add_dirs
    
    return cmd
```

**CLI 플래그 전체 매핑:**

| 설정 필드 | CLI 플래그 | 비고 |
|---|---|---|
| `model` | `--model` | |
| `system_prompt` | `--system-prompt` | 전체 교체 |
| `append_system_prompt` | `--append-system-prompt` | 기본 프롬프트에 추가 |
| `max_turns` | `--max-turns` | 없으면 무제한 |
| `max_budget_usd` | `--max-budget-usd` | 비용 상한 |
| `effort` | `--effort` | low/medium/high/max |
| `json_schema` | `--json-schema` | 구조화 출력 |
| `allowed_tools` | `--allowedTools` | |
| `disallowed_tools` | `--disallowedTools` | |
| `permission_mode` | `--permission-mode` / `--dangerously-skip-permissions` | |
| `session_id` | `--resume` | 세션 이어가기 |
| `mcp_config` | `--mcp-config` | MCP 서버 연결 |
| `add_dirs` | `--add-dir` | 추가 접근 디렉토리 |
| `bare` | `--bare` | 최소 모드 |
| `context` | stdin 파이프 | `echo context \| claude -p` |
| (항상) | `--output-format stream-json` | 파싱용 고정 |
| (항상) | `-p` | 비대화형 모드 |

---

## 5. Broker

### 5.1 AbstractBroker (인터페이스)

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

### 5.2 RedisBroker (기본 구현)

```python
class RedisBroker(AbstractBroker):
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

# 비용
{ns}:cost:total                    # Float — 전체 누적 비용 (INCRBYFLOAT)
{ns}:cost:worker:{worker_id}       # Float — 워커별 누적 비용
{ns}:cost:daily:{YYYY-MM-DD}       # Float — 일별 비용 (모니터링)
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

## 6. ClaudeConfig

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
    max_budget_usd: float | None = None   # --max-budget-usd
    effort: str | None = None             # --effort (low/medium/high/max)
    json_schema: str | None = None        # --json-schema
    
    # 도구 / 권한
    allowed_tools: list[str] | None = None      # --allowedTools
    disallowed_tools: list[str] | None = None   # --disallowedTools
    permission_mode: str = "default"             # --permission-mode
    
    # 세션 / 환경
    mcp_config: str | None = None         # --mcp-config
    add_dirs: list[str] | None = None     # --add-dir
    bare: bool = True                     # --bare
```

**재사용 예시:**
```python
config = ClaudeConfig(model="sonnet", work_dir="/my/project", effort="high")

worker_a = ClaudeWorker(broker=broker, queues=["queue-a"], claude=config, concurrency=4)
worker_b = ClaudeWorker(broker=broker, queues=["queue-b"], claude=config, concurrency=2)
```

---

## 7. Task 모델

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
    
    # 실행 옵션 (None이면 Worker 기본값 사용)
    work_dir: str | None = None
    
    # LLM / 프롬프트
    model: str | None = None                        # --model
    system_prompt: str | None = None                # --system-prompt (전체 교체)
    append_system_prompt: str | None = None         # --append-system-prompt (추가)
    max_turns: int | None = None                    # --max-turns
    max_budget_usd: float | None = None             # --max-budget-usd
    effort: str | None = None                       # --effort (low/medium/high/max)
    json_schema: str | None = None                  # --json-schema (구조화 출력)
    
    # 도구 / 권한
    allowed_tools: list[str] | None = None          # --allowedTools
    disallowed_tools: list[str] | None = None       # --disallowedTools
    permission_mode: str | None = None              # --permission-mode
    
    # 세션 / 환경
    session_id: str | None = None                   # --resume (세션 이어가기)
    mcp_config: str | None = None                   # --mcp-config
    add_dirs: list[str] | None = None               # --add-dir
    bare: bool | None = None
    timeout: int | None = None
    
    # 재시도
    max_retries: int = 0
    retry_count: int = 0
    
    # 결과
    result: str | None = None
    error: str | None = None
    exit_code: int | None = None
    result_session_id: str | None = None            # 실행 후 반환된 세션 ID (이어가기용)
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

## 8. 미들웨어

시그널 6개. 기본 제공 3개.

### 8.1 시그널

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

### 8.2 기본 제공 미들웨어 (6개)

| 미들웨어 | 설명 | 기본 활성화 |
|---|---|---|
| `LoggingMiddleware` | 작업 시작/완료/실패 structlog 로깅 | ✅ |
| `RetriesMiddleware` | 지수 백오프 재시도 + DLQ | ✅ |
| `TimeoutMiddleware` | subprocess SIGTERM → 5초 → SIGKILL | ✅ |
| `CostMiddleware` | 비용 추적 + 한도 관리 (Task/Worker/전체 3단계) + 알림 | ✅ |
| `RateLimitMiddleware` | 분당 최대 요청 수 제한 (API rate limit 방어) | ❌ (옵션) |
| `CallbackMiddleware` | 완료/실패 시 webhook 또는 함수 콜백 | ❌ (옵션) |

**RetriesMiddleware 상세:**
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

**CostMiddleware 상세:**
```python
class CostMiddleware(Middleware):
    """비용 추적 + 한도 관리.
    
    3단계 비용 제어:
    1. Task 단위: task.max_budget_usd → CLI가 자체 중단 (--max-budget-usd)
    2. Worker 단위: worker_budget_usd → 워커 누적 비용 한도
    3. 전체 단위: global_budget_usd → namespace 전체 비용 한도 (Redis에 저장)
    """
    
    def __init__(
        self,
        worker_budget_usd: float | None = None,    # 워커 누적 한도
        global_budget_usd: float | None = None,     # 전체 한도
        on_budget_alert: Callable | str | None = None,  # 한도 도달 시 콜백/webhook
        alert_threshold: float = 0.8,               # 80%에서 경고
    ): ...
    
    async def after_process(self, broker, task, *, result=None, exception=None):
        if result and result.usage:
            # 1) task에 비용 기록
            task.usage = result.usage
            await broker.update_task(task)
            
            # 2) 워커 누적 비용 갱신
            self._worker_spent += result.usage.cost_usd or 0
            
            # 3) 전체 누적 비용 갱신 (Redis INCRBYFLOAT)
            await broker.incr_cost(result.usage.cost_usd or 0)
            
            # 4) 한도 체크
            await self._check_limits(broker, task)
    
    async def before_process(self, broker, task):
        """한도 초과 시 작업 거부 → nack → DLQ"""
        if await self._is_over_budget(broker):
            task.error = "Budget limit exceeded"
            return None  # skip
        return task
    
    async def _check_limits(self, broker, task):
        # 경고 (threshold 도달)
        if self.on_budget_alert:
            if self._worker_spent >= (self.worker_budget_usd or float('inf')) * self.alert_threshold:
                await self._alert(f"Worker budget {self.alert_threshold*100}% reached: ${self._worker_spent:.2f}")
            
            global_spent = await broker.get_total_cost()
            if global_spent >= (self.global_budget_usd or float('inf')) * self.alert_threshold:
                await self._alert(f"Global budget {self.alert_threshold*100}% reached: ${global_spent:.2f}")
```

**Redis 비용 저장:**
```
{ns}:cost:total           # INCRBYFLOAT — 전체 누적 비용
{ns}:cost:worker:{id}     # INCRBYFLOAT — 워커별 누적 비용
{ns}:cost:daily:{date}    # INCRBYFLOAT — 일별 비용 (모니터링용)
```

**CallbackMiddleware 상세:**
```python
class CallbackMiddleware(Middleware):
    def __init__(
        self,
        on_done: str | Callable | None = None,     # webhook URL 또는 async 함수
        on_failure: str | Callable | None = None,
    ): ...
    
    async def after_process(self, broker, task, *, result=None, exception=None):
        if exception is None and self.on_done:
            await self._call(self.on_done, task, result)
        elif exception and self.on_failure:
            await self._call(self.on_failure, task, exception)
```

**RateLimitMiddleware 상세:**
```python
class RateLimitMiddleware(Middleware):
    def __init__(self, max_per_minute: int = 30): ...
    
    async def before_process(self, broker, task):
        """분당 요청 수 초과 시 지연."""
        await self._wait_if_needed()
        return task
```

---

## 9. CLI

```bash
# === 워커 실행 ===
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

# === 큐 관리 ===
open-kknaks queue list                         # 선언된 큐 목록 + 사이즈
open-kknaks queue size error-analysis          # 특정 큐 대기 작업 수
open-kknaks queue purge error-analysis         # 큐 비우기

# === DLQ 관리 ===
open-kknaks dlq list error-analysis            # 실패 작업 목록
open-kknaks dlq retry error-analysis --task-id abc123   # 재시도
open-kknaks dlq retry error-analysis --all     # 전부 재시도
open-kknaks dlq purge error-analysis           # DLQ 비우기

# === 작업 조회 ===
open-kknaks task status abc123                 # 상태 조회
open-kknaks task result abc123                 # 결과 조회
open-kknaks task cancel abc123                 # 취소

# === 워커 상태 ===
open-kknaks worker list                        # 활성 워커 목록
```

---

## 10. 패키지 구조

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
│   ├── redis.py             # RedisBroker (기본 구현)
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
│   └── executor.py          # ClaudeCodeExecutor (CLI 실행)
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

**v1 대비 변경:**
- `broker/memory.py` (InMemoryBroker) → **제거** (테스트는 mock)
- `config.py` → **ExecutionConfig 제거**, **ClaudeConfig 신규** (Claude CLI 설정 분리)
- `middleware/age_limit.py` → **제거** (유저 구현)
- `worker/process_manager.py` → executor.py에 통합

---

## 11. 변경 요약 (v1 → v2)

| 항목 | v1 PRD | v2 slim |
|---|---|---|
| 진입점 | `ClaudeRunner` 일체형 | `ClaudeClient` + `ClaudeWorker` 분리 |
| 큐 | 단일 | 멀티 큐 라우팅 |
| 브로커 | AbstractBroker + InMemory + Redis | **AbstractBroker + RedisBroker** (InMemory 제거) |
| DLQ | 없음 | 큐별 DLQ |
| ack/nack | ack만 | ack + nack + requeue |
| 셧다운 | SIGTERM만 | requeue + 실행 중 대기 |
| 헬스체크 | 없음 | heartbeat + 좀비 감지 |
| 미들웨어 시그널 | 14개 | **6개** |
| 기본 미들웨어 | 7개 | **6개** (Logging, Retries, Timeout, Cost, RateLimit, Callback) |
| CLI | 없음 | **4개 서브커맨드** (worker/queue/dlq/task) |
| 설정 | TOML + 환경변수 + Python | **Python + 환경변수** |
| 설정 | Worker에 파라미터 직접 | **ClaudeConfig 분리** (재사용 가능) |
| 추상화 | AbstractBroker, AbstractExecutor, ExecutionConfig | **AbstractBroker 유지**, ClaudeConfig 분리, AbstractExecutor 제거 |
