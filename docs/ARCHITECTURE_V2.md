# open_kknaks — Architecture v2 (상용 설계)

> v1 PRD를 폐기하고 상용 레벨로 재설계한다.
> Dramatiq 분석 결과를 반영하되, Claude Code CLI 전용 특성에 맞게 변형한다.

---

## 1. 설계 원칙

1. **프로듀서/워커 완전 분리** — submit하는 코드와 실행하는 코드는 별개 프로세스
2. **멀티 큐 라우팅** — 워커가 특정 큐만 소비. 큐 = 작업 유형/환경 단위
3. **at-least-once delivery** — 작업은 ack 전까지 유실되지 않음
4. **수평 확장** — 같은 큐에 워커 N대 붙이면 처리량 N배
5. **미들웨어 확장** — 핵심 로직은 미들웨어로 분리

---

## 2. 핵심 컴포넌트

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
│                   Broker (Redis)                         │
│                                                         │
│  큐: error-analysis, pr-review, default, ...            │
│  DLQ: {queue}.dlq                                       │
│  스트림: stream:{task_id}                                │
│  상태: task:{task_id}                                    │
│  워커 등록: workers:{worker_id}                          │
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

### 2.1 ClaudeClient (프로듀서)

작업을 큐에 넣기만 한다. 워커를 실행하지 않는다.

```python
from open_kknaks import ClaudeClient
from open_kknaks.broker import RedisBroker

client = ClaudeClient(
    broker=RedisBroker(url="redis://localhost:6379", namespace="myapp"),
)

# 작업 등록 — 어떤 큐에 넣을지 지정
task_id = await client.submit(
    prompt="이 에러 분석해줘",
    context=error_log,
    queue="error-analysis",       # 필수: 어떤 큐에 넣을지
    priority="high",
    timeout=600,
    max_retries=3,
    metadata={"source": "sentry", "issue_id": "PROJ-123"},
)

# 결과 조회 (폴링)
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

**ClaudeClient는 Claude Code CLI와 무관.** 그냥 Broker에 Task를 넣고, 상태/결과를 조회하는 얇은 클라이언트.

### 2.2 ClaudeWorker (소비자)

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
    
    # 미들웨어
    middlewares=[...],
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

### 2.3 Broker

```python
class AbstractBroker(ABC):
    # === 큐 관리 ===
    async def declare_queue(self, queue_name: str) -> None: ...
    async def get_declared_queues(self) -> set[str]: ...
    
    # === 메시지 ===
    async def enqueue(self, task: Task, *, delay: int | None = None) -> None: ...
    async def dequeue(self, queue_names: list[str], timeout: float = 1.0) -> Task | None: ...
    async def ack(self, queue_name: str, task_id: str) -> None: ...
    async def nack(self, queue_name: str, task_id: str) -> None: ...
    async def requeue(self, queue_name: str, task_ids: list[str]) -> None: ...
    
    # === 상태/결과 ===
    async def get_task(self, task_id: str) -> Task | None: ...
    async def update_task(self, task: Task) -> None: ...
    
    # === 스트리밍 ===
    async def publish_chunk(self, task_id: str, chunk: StreamEvent) -> None: ...
    async def subscribe_chunks(self, task_id: str) -> AsyncIterator[StreamEvent]: ...
    
    # === DLQ ===
    async def move_to_dlq(self, queue_name: str, task_id: str) -> None: ...
    async def list_dlq(self, queue_name: str, limit: int = 100) -> list[Task]: ...
    async def retry_from_dlq(self, queue_name: str, task_id: str) -> None: ...
    async def purge_dlq(self, queue_name: str) -> None: ...
    
    # === 모니터링 ===
    async def queue_size(self, queue_name: str) -> int: ...
    async def register_worker(self, worker_id: str, queues: list[str]) -> None: ...
    async def heartbeat(self, worker_id: str) -> None: ...
    async def get_workers(self) -> list[WorkerInfo]: ...
    
    # === 미들웨어 시그널 ===
    async def emit_before(self, signal: str, *args, **kwargs) -> None: ...
    async def emit_after(self, signal: str, *args, **kwargs) -> None: ...
    
    # === 라이프사이클 ===
    async def connect(self) -> None: ...
    async def close(self) -> None: ...
    async def flush(self, queue_name: str) -> None: ...
    async def flush_all(self) -> None: ...
```

### 2.4 Worker 내부 구조

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

**async 구현 상세:**

```python
class ClaudeWorker:
    async def run(self):
        """워커 메인 루프. 블로킹."""
        self._running = True
        self._worker_id = str(uuid4())
        self._internal_queue = asyncio.PriorityQueue(maxsize=self.concurrency * 2)
        
        # 시그널 핸들러 등록
        loop = asyncio.get_event_loop()
        loop.add_signal_handler(signal.SIGTERM, self._request_shutdown)
        loop.add_signal_handler(signal.SIGINT, self._request_shutdown)
        
        await self.broker.connect()
        for q in self.queues:
            await self.broker.declare_queue(q)
        await self.broker.register_worker(self._worker_id, self.queues)
        await self.broker.emit_before("worker_boot", self)
        
        # 루프 시작
        tasks = [
            asyncio.create_task(self._dequeue_loop()),
            asyncio.create_task(self._heartbeat_loop()),
        ]
        for _ in range(self.concurrency):
            tasks.append(asyncio.create_task(self._processor_loop()))
        
        await self.broker.emit_after("worker_boot", self)
        
        # 종료 시그널 대기
        await self._shutdown_event.wait()
        await self._graceful_shutdown(tasks)
    
    async def _dequeue_loop(self):
        """브로커에서 작업을 꺼내 internal queue에 넣는 루프."""
        while self._running:
            # internal queue에 여유가 있을 때만 dequeue
            if self._internal_queue.full():
                await asyncio.sleep(self.poll_interval)
                continue
            
            task = await self.broker.dequeue(self.queues, timeout=self.poll_interval)
            if task:
                await self._internal_queue.put((task.priority.value, task))
    
    async def _processor_loop(self):
        """internal queue에서 작업을 꺼내 실행하는 루프."""
        while self._running:
            try:
                _, task = await asyncio.wait_for(
                    self._internal_queue.get(),
                    timeout=self.poll_interval,
                )
            except asyncio.TimeoutError:
                continue
            
            await self._process_task(task)
            self._internal_queue.task_done()
    
    async def _process_task(self, task: Task):
        """단일 작업 실행."""
        try:
            # 상태 업데이트: RUNNING
            task.status = TaskStatus.RUNNING
            task.started_at = datetime.utcnow()
            await self.broker.update_task(task)
            
            # 미들웨어: before_process
            await self.broker.emit_before("process", task)
            
            # 실행 설정 병합 (Worker 기본값 + Task 오버라이드)
            exec_config = self._merge_config(task)
            
            # Claude Code CLI 실행
            result = await self.executor.execute(
                task=task,
                config=exec_config,
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
            
            # 미들웨어: after_process (성공)
            await self.broker.emit_after("process", task, result=result)
            
            # ack
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
            
            # 미들웨어: after_process (실패) — Retries 미들웨어가 재큐잉 판단
            await self.broker.emit_after("process", task, exception=e)
            
            # Retries 미들웨어가 재큐잉하지 않았으면 nack → DLQ
            if task.status == TaskStatus.FAILED:
                await self.broker.nack(task.queue, task.id)
    
    async def _heartbeat_loop(self):
        """워커 생존 신호."""
        while self._running:
            await self.broker.heartbeat(self._worker_id)
            await asyncio.sleep(self.heartbeat_interval)
    
    async def _graceful_shutdown(self, tasks: list[asyncio.Task]):
        """그레이스풀 셧다운."""
        await self.broker.emit_before("worker_shutdown", self)
        
        # 1) dequeue 중지
        self._running = False
        
        # 2) 실행 중 작업 완료 대기
        try:
            await asyncio.wait_for(
                self._internal_queue.join(),
                timeout=self.shutdown_timeout,
            )
        except asyncio.TimeoutError:
            # 타임아웃 → 실행 중 Claude 프로세스 강제 종료
            await self.executor.kill_all()
        
        # 3) internal queue에 남은 미처리 Task → requeue
        remaining = []
        while not self._internal_queue.empty():
            _, task = self._internal_queue.get_nowait()
            remaining.append(task.id)
        if remaining:
            await self.broker.requeue(self.queues[0], remaining)
        
        # 4) 모든 루프 취소
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        
        # 5) 브로커 정리
        await self.broker.emit_after("worker_shutdown", self)
        await self.broker.close()
    
    def _merge_config(self, task: Task) -> ExecutionConfig:
        """Worker 기본값 + Task 오버라이드 병합."""
        return ExecutionConfig(
            work_dir=task.work_dir or self.work_dir,
            claude_bin=self.claude_bin,
            model=task.model or self.model,
            allowed_tools=task.allowed_tools or self.allowed_tools,
            append_system_prompt=task.append_system_prompt or self.append_system_prompt,
            max_turns=task.max_turns or self.max_turns,
            permission_mode=task.permission_mode or self.permission_mode,
            bare=task.bare if task.bare is not None else self.bare,
            timeout=task.timeout or self.default_timeout,
        )
```

---

## 3. Task 모델 (v2)

```python
class Task(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=False)
    
    id: str = Field(default_factory=lambda: str(uuid4()))
    prompt: str
    context: str | None = None
    
    # 라우팅
    queue: str = "default"                     # ← NEW: 어떤 큐에 넣을지
    
    # 상태
    status: TaskStatus = TaskStatus.PENDING
    
    # 우선순위 / 스케줄링
    priority: Priority = Priority.NORMAL
    delay_until: datetime | None = None        # 지연 실행
    
    # 실행 옵션 (Task별 오버라이드 — None이면 Worker 기본값 사용)
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
    
    # 내부 (브로커용)
    _broker_message_id: str | None = None      # Redis 내부 메시지 ID (재시도 시 변경)
    _worker_id: str | None = None              # 처리 중인 워커 ID
```

---

## 4. Redis 데이터 구조

```
{ns} = namespace (기본: "open_kknaks")

# === 큐 ===
{ns}:queue:{queue_name}            # Sorted Set (score = priority * 1e12 + timestamp)
{ns}:queue:{queue_name}.delayed    # Sorted Set (score = delay_until timestamp)
{ns}:queue:{queue_name}.active     # Set (현재 처리 중인 task_id 목록)
{ns}:queue:{queue_name}.dlq        # List (Dead Letter Queue)

# === 작업 ===
{ns}:task:{task_id}                # Hash (Task 전체 데이터, JSON)
{ns}:task:{task_id}:ttl            # TTL 설정 (완료 후 result_ttl 시간 뒤 자동 삭제)

# === 스트리밍 ===
{ns}:stream:{task_id}              # Redis Stream (청크 이벤트)

# === 배치 ===
{ns}:batch:{batch_id}              # Set (소속 task_id 목록)
{ns}:batch:{batch_id}:meta         # Hash (mode, total, done, failed 등)

# === 워커 ===
{ns}:workers                       # Hash (worker_id → JSON{queues, started_at, last_heartbeat})
{ns}:worker:{worker_id}:active     # Set (현재 처리 중인 task_id)

# === 모니터링 ===
{ns}:stats:{queue_name}            # Hash (enqueued, processed, failed, avg_duration 등)
```

### 4.1 핵심 Redis 연산 (Lua 스크립트)

**enqueue:**
```lua
-- 우선순위 큐에 삽입
-- score = priority * 1e12 + timestamp (우선순위 높을수록 score 작음)
ZADD {ns}:queue:{queue} score task_id
HSET {ns}:task:{task_id} (task JSON)
```

**dequeue:**
```lua
-- 여러 큐를 순회하며 score가 가장 작은 것 꺼냄
-- 꺼낸 task_id를 active set에 이동 (processing 표시)
local task_id = ZPOPMIN {ns}:queue:{queue}
SADD {ns}:queue:{queue}.active task_id
RETURN task_id
```

**ack:**
```lua
-- 처리 완료. active에서 제거. TTL 설정.
SREM {ns}:queue:{queue}.active task_id
EXPIRE {ns}:task:{task_id} result_ttl
```

**nack:**
```lua
-- 처리 실패. active에서 제거. DLQ로 이동.
SREM {ns}:queue:{queue}.active task_id
RPUSH {ns}:queue:{queue}.dlq task_id
```

**requeue:**
```lua
-- 워커 셧다운 시. active에서 큐로 복귀.
SREM {ns}:queue:{queue}.active task_id
ZADD {ns}:queue:{queue} original_score task_id
```

**heartbeat + 좀비 감지:**
```lua
-- 워커 헬스체크
HSET {ns}:workers worker_id JSON{last_heartbeat: now}

-- 좀비 워커 감지 (다른 워커가 주기적으로 실행)
-- heartbeat_timeout 초과한 워커의 active task → requeue
FOR worker IN HGETALL {ns}:workers:
    IF now - worker.last_heartbeat > heartbeat_timeout:
        tasks = SMEMBERS {ns}:worker:{worker_id}:active
        FOR task_id IN tasks:
            ZADD {ns}:queue:{queue} score task_id  -- requeue
        DEL {ns}:worker:{worker_id}:active
        HDEL {ns}:workers worker_id
```

---

## 5. 미들웨어 시그널

```python
class Middleware(ABC):
    # === 큐 관련 ===
    async def before_enqueue(self, broker, task: Task) -> Task | None:
        """큐 등록 전. None 반환 시 등록 취소."""
        return task
    
    async def after_enqueue(self, broker, task: Task) -> None:
        """큐 등록 후."""
    
    # === 처리 관련 ===
    async def before_process(self, broker, task: Task) -> Task | None:
        """실행 전. None 반환 시 skip (→ after_skip)."""
        return task
    
    async def after_process(self, broker, task: Task, *,
                            result: TaskResult | None = None,
                            exception: Exception | None = None) -> None:
        """실행 후. 성공이면 result, 실패면 exception."""
    
    async def after_skip(self, broker, task: Task) -> None:
        """before_process에서 skip된 후."""
    
    # === ack/nack ===
    async def before_ack(self, broker, task: Task) -> None: ...
    async def after_ack(self, broker, task: Task) -> None: ...
    async def before_nack(self, broker, task: Task) -> None: ...
    async def after_nack(self, broker, task: Task) -> None: ...
    
    # === 스트리밍 ===
    async def on_chunk(self, broker, task: Task, chunk: StreamEvent) -> None:
        """Claude Code CLI에서 청크 수신 시."""
    
    # === 워커 라이프사이클 ===
    async def before_worker_boot(self, broker, worker) -> None: ...
    async def after_worker_boot(self, broker, worker) -> None: ...
    async def before_worker_shutdown(self, broker, worker) -> None: ...
    async def after_worker_shutdown(self, broker, worker) -> None: ...
```

### 5.1 기본 제공 미들웨어

| 미들웨어 | 설명 | 기본 활성화 |
|---|---|---|
| `LoggingMiddleware` | 구조화 로깅 (structlog) | ✅ |
| `RetriesMiddleware` | 지수 백오프 재시도 + DLQ | ✅ |
| `TimeoutMiddleware` | 작업별 타임아웃 (subprocess SIGTERM/SIGKILL) | ✅ |
| `CostTrackingMiddleware` | stream-json 토큰/비용 파싱 | ✅ |
| `RateLimitMiddleware` | 분당 최대 요청 수 제한 | ❌ |
| `CallbackMiddleware` | 완료/실패 시 webhook/함수 콜백 | ❌ |
| `AgeLimit` | TTL 초과한 작업 자동 skip | ❌ |

### 5.2 RetriesMiddleware 상세

```python
class RetriesMiddleware(Middleware):
    def __init__(
        self,
        max_retries: int = 3,
        min_backoff: float = 5.0,          # 초
        max_backoff: float = 300.0,        # 초
        backoff_factor: float = 2.0,       # 지수
        retry_on: tuple[type[Exception], ...] | None = None,   # None이면 모든 예외
        no_retry_on: tuple[type[Exception], ...] = (
            TaskCancelledError,
            ClaudeAuthError,
        ),
    ): ...
    
    async def after_process(self, broker, task, *, result=None, exception=None):
        if exception is None:
            return
        
        if isinstance(exception, self.no_retry_on):
            return  # → nack → DLQ
        
        if self.retry_on and not isinstance(exception, self.retry_on):
            return  # → nack → DLQ
        
        if task.retry_count >= (task.max_retries or self.max_retries):
            return  # → nack → DLQ
        
        # 재시도
        delay = min(
            self.min_backoff * (self.backoff_factor ** task.retry_count),
            self.max_backoff,
        )
        task.retry_count += 1
        task.status = TaskStatus.RETRYING
        await broker.update_task(task)
        await broker.enqueue(task, delay=int(delay))
```

---

## 6. CLI

```bash
# 워커 실행
open-kknaks worker \
    --broker redis://localhost:6379 \
    --namespace myapp \
    --queues error-analysis,general \
    --work-dir /my/backend \
    --model sonnet \
    --concurrency 4 \
    --config /etc/open-kknaks/worker.toml

# 큐 상태 확인
open-kknaks queue list
open-kknaks queue size error-analysis
open-kknaks queue purge error-analysis

# DLQ 관리
open-kknaks dlq list error-analysis
open-kknaks dlq retry error-analysis --task-id abc123
open-kknaks dlq purge error-analysis

# 워커 상태
open-kknaks worker list
open-kknaks worker info worker-abc123

# 작업 조회
open-kknaks task status abc123
open-kknaks task result abc123
open-kknaks task cancel abc123
```

---

## 7. 설정 파일

```toml
# worker.toml

[broker]
type = "redis"
url = "redis://localhost:6379"
namespace = "myapp"
result_ttl = 3600
stream_maxlen = 1000

[worker]
queues = ["error-analysis", "general"]
concurrency = 4
poll_interval = 0.5
heartbeat_interval = 30
shutdown_timeout = 300

[claude]
work_dir = "/my/backend"
model = "sonnet"
allowed_tools = ["Read", "Bash(git log *)", "Bash(git diff *)"]
append_system_prompt = "You are a backend error analyst."
max_turns = 10
permission_mode = "default"
bare = true
timeout = 600

[middleware.retries]
max_retries = 3
min_backoff = 5
max_backoff = 300

[middleware.rate_limit]
enabled = true
max_per_minute = 30

[middleware.callback]
enabled = true
on_done = "https://my-server.com/webhook/task-done"
on_failure = "https://my-server.com/webhook/task-failed"

[logging]
level = "INFO"
format = "json"
```

---

## 8. 패키지 구조 (v2)

```
open_kknaks/
├── __init__.py              # ClaudeClient, Task, TaskStatus 등 public export
├── client.py                # ClaudeClient (프로듀서 전용)
├── task.py                  # Task, TaskStatus, Priority, TaskResult, TokenUsage, StreamEvent
├── batch.py                 # BatchRunner, BatchStatus
├── config.py                # ExecutionConfig, WorkerConfig, 설정 로딩
├── worker/
│   ├── __init__.py
│   ├── worker.py            # ClaudeWorker (소비자)
│   ├── executor.py          # ClaudeCodeExecutor (CLI 실행 엔진)
│   └── process_manager.py   # 실행 중 subprocess 관리 (kill, signal)
├── broker/
│   ├── __init__.py          # AbstractBroker export
│   ├── base.py              # AbstractBroker 인터페이스
│   ├── redis.py             # RedisBroker
│   └── lua/                 # Redis Lua 스크립트
│       ├── enqueue.lua
│       ├── dequeue.lua
│       ├── ack.lua
│       ├── nack.lua
│       ├── requeue.lua
│       ├── heartbeat.lua
│       └── maintenance.lua  # 좀비 워커 감지 + delayed 큐 처리
├── middleware/
│   ├── __init__.py
│   ├── base.py              # Middleware ABC
│   ├── logging.py           # LoggingMiddleware
│   ├── retries.py           # RetriesMiddleware
│   ├── timeout.py           # TimeoutMiddleware
│   ├── cost.py              # CostTrackingMiddleware
│   ├── rate_limit.py        # RateLimitMiddleware
│   ├── callback.py          # CallbackMiddleware
│   └── age_limit.py         # AgeLimitMiddleware
├── mcp/
│   ├── __init__.py
│   ├── server.py            # MCPServer
│   └── __main__.py          # python -m open_kknaks.mcp
├── cli/
│   ├── __init__.py
│   ├── main.py              # CLI 진입점 (click/typer)
│   ├── worker_cmd.py        # worker 서브커맨드
│   ├── queue_cmd.py         # queue 서브커맨드
│   ├── dlq_cmd.py           # dlq 서브커맨드
│   └── task_cmd.py          # task 서브커맨드
├── exceptions.py            # 커스텀 예외 계층
├── _utils.py                # 유틸리티
└── py.typed                 # PEP 561
```

---

## 9. v1 → v2 변경 요약

| 항목 | v1 (PRD) | v2 (상용) |
|---|---|---|
| 진입점 | `ClaudeRunner` (일체형) | `ClaudeClient` (프로듀서) + `ClaudeWorker` (소비자) |
| 큐 | 단일 큐 + 우선순위 | 멀티 큐 + 우선순위 |
| 기본 브로커 | InMemoryBroker | RedisBroker (InMemory 제거 or 테스트 전용) |
| DLQ | 없음 | 큐별 DLQ |
| ack/nack | ack만 | ack + nack + requeue |
| 그레이스풀 셧다운 | SIGTERM/SIGKILL만 | 미처리 requeue + 실행 중 대기 |
| 워커 헬스체크 | 없음 | heartbeat + 좀비 감지 |
| CLI | 없음 | `open-kknaks worker/queue/dlq/task` |
| 설정 | Python 코드 only | TOML + 환경변수 + Python |
| 모니터링 | 없음 | queue size, worker list, stats |
| InMemoryBroker | 기본값 | 테스트 전용 (상용에서 사용 불가 경고) |
| Task.queue | 없음 | 필수 필드 |
| Worker 환경 | Task별 오버라이드만 | Worker 기본값 + Task 오버라이드 |
| 좀비 워커 | 미고려 | heartbeat_timeout 기반 자동 requeue |
