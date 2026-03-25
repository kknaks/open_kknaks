# Dramatiq 구조 분석 → open_kknaks 설계 결정

> Dramatiq 소스 (v1.18.0) 분석 결과, open_kknaks에 적용할 패턴과 버려야 할 패턴을 정리한다.

---

## 1. Dramatiq 핵심 아키텍처 요약

```
Broker (글로벌 싱글턴)
  ├─ actors: dict[name → Actor]       # 함수 레지스트리
  ├─ queues: set[str]                  # 선언된 큐 이름들
  ├─ middleware: list[Middleware]       # 미들웨어 체인
  ├─ emit_before(signal, ...)          # 미들웨어 순방향 실행
  ├─ emit_after(signal, ...)           # 미들웨어 역방향 실행
  ├─ enqueue(message, delay=)          # 큐에 넣기
  └─ consume(queue_name) → Consumer    # 큐에서 꺼내기 (이터레이터)

Worker
  ├─ _ConsumerThread × N큐            # 큐마다 소비 스레드
  │   ├─ broker.consume() 호출
  │   ├─ handle_message() → work_queue에 (priority, message) 넣기
  │   ├─ handle_delayed_messages() → eta 지난 것 재큐잉
  │   └─ post_process_message() → ack/nack
  │
  └─ _WorkerThread × N개              # 실제 작업 실행 스레드
      └─ process_message()
          ├─ emit_before("process_message")
          ├─ actor(*args, **kwargs)     ← 여기서 유저 함수 실행
          ├─ emit_after("process_message", result=, exception=)
          └─ consumer.post_process_message() → ack/nack

Message (frozen dataclass)
  ├─ queue_name, actor_name
  ├─ args, kwargs                      # 유저 함수 인자
  ├─ options: dict                     # delay, retries, redis_message_id 등
  ├─ encode() / decode()               # JSON 직렬화
  └─ message_id (UUID)
```

### 핵심 설계 원칙
1. **Broker가 중심** — 모든 것이 Broker를 통해 흐름 (enqueue, consume, middleware)
2. **스레드 기반** — _ConsumerThread + _WorkerThread (GIL 하에서 동작)
3. **Consumer 패턴** — `__next__`로 메시지 하나씩 꺼냄, ack/nack으로 확인/거부
4. **MessageProxy** — 원본 Message를 감싸서 fail/exception 추적
5. **미들웨어 시그널** — before_X / after_X 쌍, before는 순방향, after는 역방향
6. **재시도** — Retries 미들웨어가 exception 발생 시 `broker.enqueue(message, delay=backoff)` 호출
7. **타임아웃** — TimeLimit 미들웨어가 스레드에 비동기 예외 주입 (ctypes)
8. **Redis Broker** — Lua 스크립트로 원자적 큐 연산 (fetch, ack, nack, requeue 등)

---

## 2. open_kknaks에 적용할 것 (✅ 가져감)

### 2.1 Broker 추상화 + 미들웨어 시그널
Dramatiq의 가장 좋은 설계. Broker가 `emit_before` / `emit_after`로 미들웨어 체인을 실행하는 패턴 그대로 차용.

```python
# Dramatiq 방식
broker.emit_before("enqueue", message, delay)
broker.enqueue(message)
broker.emit_after("enqueue", message, delay)

# open_kknaks도 동일하게
broker.emit_before("enqueue", task)
await broker.enqueue(task)
broker.emit_after("enqueue", task)
```

**차이점:** Dramatiq은 동기, open_kknaks는 async.

### 2.2 Consumer/Ack/Nack 패턴
Dramatiq의 Consumer가 메시지를 꺼낸 뒤 ack/nack로 처리 완료를 알리는 패턴. 이게 있어야:
- 작업 중 워커 크래시 → 메시지 유실 방지 (nack → 재큐잉)
- 그레이스풀 셧다운 시 미처리 메시지 requeue

**현재 PRD 허점:** `acknowledge(task_id)` 만 있고, nack(실패 시 DLQ 이동)가 없음.

### 2.3 Retries 미들웨어의 지수 백오프
Dramatiq의 `compute_backoff(retries, factor=min_backoff, max_backoff=)` 패턴.
- exception 발생 → `broker.enqueue(message, delay=backoff)` 로 재큐잉
- `throws` 옵션: 특정 예외는 재시도하지 않음

### 2.4 Worker의 그레이스풀 셧다운
Dramatiq Worker.stop() 순서:
1. WorkerThread들 stop → join (실행 중 작업 완료 대기)
2. ConsumerThread들 stop → join
3. 미처리 메시지 requeue
4. Consumer close

**현재 PRD 허점:** "SIGTERM → 5초 → SIGKILL" 만 있고, 미처리 Task 재큐잉 로직 없음.

### 2.5 Redis Broker의 Lua 스크립트 원자성
Dramatiq Redis broker는 모든 큐 연산을 Lua 스크립트로 수행 (fetch, ack, nack, requeue 등).
이건 멀티 워커 환경에서 경합 조건을 방지하는 핵심.

---

## 3. open_kknaks에서 버릴 것 (❌ 불필요)

### 3.1 Actor 레지스트리 / 큐 라우팅
Dramatiq: `actors = dict[name → Actor]`, 메시지의 `actor_name`으로 어떤 함수를 실행할지 결정.
open_kknaks: 실행 함수가 Claude Code CLI 하나로 고정 → Actor 개념 자체가 불필요.

### 3.2 멀티 큐
Dramatiq: 큐를 여러 개 선언하고, actor마다 다른 큐에 배치.
open_kknaks: 단일 큐 + 우선순위로 충분. (큐가 하나이므로 ConsumerThread도 하나)

### 3.3 스레드 기반 Worker
Dramatiq: `threading.Thread` 기반 (_ConsumerThread, _WorkerThread).
open_kknaks: `asyncio.Task` 기반. Claude Code CLI가 I/O 바운드이므로 async가 더 적합.

### 3.4 TimeLimit의 ctypes 예외 주입
Dramatiq: `ctypes.pythonapi.PyThreadState_SetAsyncExc`로 스레드에 예외 주입.
open_kknaks: subprocess를 SIGTERM/SIGKILL로 죽이면 됨. 훨씬 깔끔.

### 3.5 글로벌 Broker 싱글턴
Dramatiq: `set_broker(broker)` / `get_broker()` 글로벌 상태.
open_kknaks: `ClaudeRunner`가 broker를 생성자 주입으로 받음. 글로벌 상태 불필요.

### 3.6 Message의 args/kwargs
Dramatiq: `Message(args=(1, 2), kwargs={"x": 3})` — 유저 함수 인자.
open_kknaks: `Task(prompt="...", context="...")` — 고정된 인자 구조.

---

## 4. 핵심 설계 결정 (open_kknaks에서 새로 정의해야 할 것)

### 4.1 ⚠️ async Consumer 패턴

Dramatiq의 Consumer는 `__next__`로 동기 이터레이션. open_kknaks는 async.

**방안 A: Dramatiq 방식 유지 (Consumer 객체)**
```python
class AsyncConsumer:
    async def __anext__(self) -> Task | None: ...
    async def ack(self, task: Task) -> None: ...
    async def nack(self, task: Task) -> None: ...
    async def requeue(self, tasks: list[Task]) -> None: ...
```

**방안 B: 단순화 (Broker 직접 호출)**
```python
# Consumer 없이 Broker에서 직접
task = await broker.dequeue(timeout=1.0)
# 처리 후
await broker.ack(task.id)
# 또는
await broker.nack(task.id)
```

**결정: 방안 B 추천.**
- 큐가 하나이므로 Consumer 추상화 불필요
- Broker에 ack/nack 메서드 추가하면 충분
- Consumer 객체는 멀티 큐 + 멀티 브로커일 때 의미 있음

### 4.2 ⚠️ Worker 구조: 스레드 vs asyncio Task

Dramatiq: N개 ConsumerThread + M개 WorkerThread (threading)
open_kknaks: 전부 asyncio.Task

```python
class Worker:
    async def start(self):
        # dequeue 루프 + concurrency개의 처리 슬롯
        self._semaphore = asyncio.Semaphore(self.concurrency)
        while self._running:
            async with self._semaphore:
                task = await self.broker.dequeue(timeout=self.poll_interval)
                if task:
                    asyncio.create_task(self._process_task(task))

    async def _process_task(self, task: Task):
        try:
            await self.broker.emit_before("process", task)
            result = await self.executor.execute(task, on_chunk=...)
            await self.broker.emit_after("process", task, result=result)
            await self.broker.ack(task.id)
        except Exception as e:
            await self.broker.emit_after("process", task, exception=e)
            await self.broker.nack(task.id)  # → DLQ 또는 재시도
```

**문제:** `asyncio.Semaphore` + `create_task` 조합에서, dequeue 루프가 semaphore를 선점해야 다음 dequeue를 함. 즉 "빈 슬롯이 있을 때만 dequeue"하는 흐름.

**Dramatiq은 이걸 어떻게 함?**
- `_ConsumerThread`가 prefetch 수만큼 메시지를 미리 당겨와서 `work_queue`(PriorityQueue)에 넣음
- `_WorkerThread`가 work_queue에서 꺼내서 처리
- 즉 "Consumer가 앞서서 당기고, Worker가 뒤에서 처리" — 파이프라인

**open_kknaks에서의 async 버전:**
```python
class Worker:
    async def start(self):
        self._task_queue = asyncio.PriorityQueue(maxsize=self.concurrency * 2)
        # 1) dequeue 루프: broker → internal queue
        asyncio.create_task(self._dequeue_loop())
        # 2) worker 루프: internal queue → executor
        for _ in range(self.concurrency):
            asyncio.create_task(self._worker_loop())

    async def _dequeue_loop(self):
        while self._running:
            task = await self.broker.dequeue(timeout=self.poll_interval)
            if task:
                await self._task_queue.put((task.priority, task))

    async def _worker_loop(self):
        while self._running:
            try:
                _, task = await asyncio.wait_for(
                    self._task_queue.get(), timeout=self.poll_interval
                )
                await self._process_task(task)
            except asyncio.TimeoutError:
                continue
```

**이 구조의 장점:**
- Dramatiq의 Consumer/Worker 분리 패턴을 async로 자연스럽게 번역
- `_task_queue.maxsize`로 prefetch 크기 제어
- 동시에 N개의 Claude Code CLI 프로세스 실행 가능

### 4.3 ⚠️ nack + DLQ (Dead Letter Queue)

**현재 PRD에 없는 것:** 실패한 작업을 어디에 보낼지.

Dramatiq: `consumer.nack(message)` → Redis의 DLQ (`.dramatiq.DQ.{queue_name}`)에 이동.

**open_kknaks 방안:**
- InMemoryBroker: `failed_tasks: dict[str, Task]`에 보관
- RedisBroker: `{prefix}:dlq` sorted set에 이동 (TTL 7일)
- Middleware에서 `on_failure` 훅으로 DLQ 이동 전 커스텀 처리 가능

### 4.4 ⚠️ 미들웨어 시그널 목록 확정

Dramatiq의 미들웨어 훅이 20개 이상인데, open_kknaks에는 이만큼 필요 없음.

**최소 시그널 세트:**

| 시그널 | 시점 | 용도 |
|---|---|---|
| `before_enqueue` / `after_enqueue` | 큐 등록 전/후 | 로깅, 검증, 변환 |
| `before_process` / `after_process` | 실행 전/후 | 로깅, 결과 처리, 재시도 |
| `before_ack` / `after_ack` | 확인 전/후 | 정리 |
| `before_nack` / `after_nack` | 거부 전/후 | DLQ, 알림 |
| `before_worker_boot` / `after_worker_boot` | 워커 시작 | 초기화 |
| `before_worker_shutdown` / `after_worker_shutdown` | 워커 종료 | 정리, requeue |
| `on_chunk` | 스트리밍 청크 수신 시 | 비용 추적, 로깅 |

**Dramatiq에 없는 것:** `on_chunk` — Claude Code 스트리밍 전용.

### 4.5 ⚠️ 그레이스풀 셧다운 상세

```
stop() 호출
  │
  ├─ 1) _running = False → dequeue 루프 정지
  │
  ├─ 2) 실행 중 작업 대기 (timeout)
  │     ├─ timeout 내 완료 → 정상 ack
  │     └─ timeout 초과 → Claude Code 프로세스에 SIGTERM
  │         ├─ 5초 대기
  │         └─ 아직 살아있으면 SIGKILL
  │
  ├─ 3) internal queue에 남은 미처리 Task → broker.requeue()
  │
  └─ 4) broker.close()
```

### 4.6 ⚠️ 직렬화 전략

Dramatiq: `JSONEncoder.encode(message.asdict())` → bytes
open_kknaks: Task는 pydantic BaseModel

```python
# InMemoryBroker: 객체 참조 직접 사용 (직렬화 불필요)
# RedisBroker: task.model_dump_json() → Redis HSET
#              Task.model_validate_json(data) → 역직렬화
```

**주의:** `metadata: dict`에 직렬화 불가능한 객체 넣으면 RedisBroker에서 터짐.
→ pydantic `model_config = ConfigDict(arbitrary_types_allowed=False)` 로 방어.

---

## 5. 결론: 최소한의 Dramatiq 패턴

open_kknaks가 Dramatiq에서 가져갈 것은 **3가지**:

1. **Broker 추상화 + emit_before/after 미들웨어 시그널**
2. **Consumer(ack/nack) + Worker(dequeue→process) 분리 루프**
3. **Retries 미들웨어의 지수 백오프 + delay 재큐잉**

나머지는 전부 단순화하거나 새로 설계:
- Actor/Registry → 불필요
- 멀티 큐 → 단일 큐 + 우선순위
- 스레드 → asyncio
- ctypes 타임아웃 → subprocess SIGTERM
- Result Backend → Broker 통합
- 글로벌 싱글턴 → 생성자 주입

이 설계가 확정되면 PRD 섹션 3(아키텍처)을 전면 수정해야 함.
