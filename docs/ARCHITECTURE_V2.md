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

### 3.1 result() / stream() 구현 방식

둘 다 Redis Stream `XREAD BLOCK` 기반. 폴링 안 씀.

```python
async def result(self, task_id: str, timeout: float = 600) -> Task:
    """완료 대기 후 최종 Task 반환.

    subscribe_chunks로 완료 이벤트 대기 (청크 데이터는 무시).
    완료 감지 후 get_task() 1회로 최종 결과를 가져온다.
    """
    async for _ in self.broker.subscribe_chunks(task_id, timeout=timeout):
        pass  # 청크 무시, 스트림 종료 = 작업 완료
    task = await self.broker.get_task(task_id)
    if task is None:
        raise TaskNotFoundError(task_id)
    return task

async def stream(self, task_id: str) -> AsyncIterator[StreamEvent]:
    """실시간 청크 스트리밍.

    subscribe_chunks로 청크를 yield.
    """
    async for chunk in self.broker.subscribe_chunks(task_id):
        yield chunk
```

> **인프라 공유:** `result()`와 `stream()` 모두 `broker.subscribe_chunks()`를 사용한다.
> `subscribe_chunks`는 내부적으로 Redis Stream의 `XREAD BLOCK`을 사용하므로 폴링이 아닌 블로킹 읽기다.

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
        model="sonnet",
        append_system_prompt="You are a backend error analyst. Be concise.",
        max_turns=10,
        effort="high",
        allowed_tools=["Read", "Bash(git log *)", "Bash(git diff *)"],
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
  │   │  before_process: 정순 실행 (예외 시 즉시 중단)
  │   │  executor.execute(task)  ← PTY 기반 Executor
  │   │  after_process: 역순 실행 (예외 시에도 모든 미들웨어 호출)
  │   │  ack 또는 nack
  │   └─ 실패 시: RetriesMiddleware.after_process에서 broker.enqueue(task, delay=backoff)
  │
  ├─ HeartbeatLoop (asyncio.Task × 1)
  │   └─ broker.heartbeat(worker_id) 주기적 호출
  │
  └─ SignalHandler
      ├─ SIGTERM → graceful shutdown (PTY 세션 전체 SIGHUP)
      └─ SIGINT  → graceful shutdown (2번 누르면 즉시 종료)
```

### 4.2 _process_task 흐름

```python
async def _process_task(self, task: Task):
    # before_process가 호출된 미들웨어 추적 (after_process 역순 호출용)
    called_middlewares: list[Middleware] = []
    result = None
    exception = None

    try:
        # 상태: RUNNING
        task.status = TaskStatus.RUNNING
        task.started_at = datetime.now(timezone.utc)
        await self.broker.update_task(task)

        # 미들웨어: before_process (정순, 예외 기반 sequential break)
        for mw in self.broker.middleware:
            mw.before_process(self.broker, task)  # 예외 발생 시 즉시 중단
            called_middlewares.append(mw)

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
        task.finished_at = datetime.now(timezone.utc)
        await self.broker.update_task(task)
        await self.broker.ack(task.queue, task.id)

    except TaskCancelledError:
        task.status = TaskStatus.CANCELLED
        task.finished_at = datetime.now(timezone.utc)
        await self.broker.update_task(task)
        await self.broker.ack(task.queue, task.id)

    except Exception as e:
        exception = e
        task.status = TaskStatus.FAILED
        task.error = str(e)
        task.exception_type = type(e).__name__
        task.finished_at = datetime.now(timezone.utc)
        await self.broker.update_task(task)

    finally:
        # 미들웨어: after_process (역순, 예외 시에도 모든 미들웨어 호출)
        for mw in reversed(called_middlewares):
            try:
                await mw.after_process(
                    self.broker, task, result=result, exception=exception,
                )
            except Exception:
                pass  # after_process 예외는 삼킴 (로깅만)

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

### 4.4 Executor — PTY 기반 프로세스 실행

> **설계 결정: Pipe가 아닌 PTY를 사용하는 이유**
>
> | 문제 | Pipe 방식 | PTY 방식 |
> |------|-----------|----------|
> | 버퍼 데드락 | stdout/stderr 동시 PIPE 시 OS 버퍼(64KB) 포화 → 프로세스 블록 | 단일 master_fd로 읽기 — 데드락 불가 |
> | 고아 프로세스 | `proc.terminate()`는 직접 자식만 종료. Claude Code 내부 자식(Node.js 등) 누수 | PTY 세션 리더 → `os.killpg(pgid, SIGHUP)`로 프로세스 트리 전체 정리 |
> | 출력 버퍼링 | 블록 버퍼링(~4KB) — 청크가 뭉쳐서 도착 → 실시간 스트리밍 지연 | 라인/캐릭터 단위 즉시 전달 |
> | 행(Hang) 감지 | readline() 블록 → 라인 타임아웃 무시 → 전체 타임아웃(600s)까지 대기 | 출력 패턴 모니터링 + idle 타임아웃으로 즉시 감지 |
> | concurrency 안정성 | 동시 4+ 프로세스 시 좀비/고아 누적 위험 | 세션별 격리 — 정리 실패해도 다른 세션에 영향 없음 |
>
> 라이브러리 수준의 안정성을 위해 PTY 기반 Executor를 사용한다.

#### 4.4.1 PTY Executor 구조

```
Executor.execute(task, config, on_chunk)
  │
  ├─ 1) _build_command(task, config) → cmd: list[str]
  │
  ├─ 2) PTY 생성 + 프로세스 스폰
  │     │
  │     │  master_fd, slave_fd = pty.openpty()
  │     │  설정: 터미널 크기 (80×24), raw mode, UTF-8
  │     │
  │     │  pid = os.fork()
  │     │  ├─ 자식: os.setsid()  ← 새 세션 리더 (핵심!)
  │     │  │       slave_fd → stdin/stdout/stderr
  │     │  │       os.execvpe(cmd, env)
  │     │  │
  │     │  └─ 부모: slave_fd close
  │     │          master_fd → asyncio 이벤트 루프에 등록
  │     │
  │     └─ PTYProcess(pid, master_fd, pgid=pid) 생성
  │
  ├─ 3) 출력 읽기 루프 (asyncio)
  │     │
  │     │  loop.add_reader(master_fd, _on_data)
  │     │  ├─ os.read(master_fd, 4096)
  │     │  ├─ LineBuffer에 축적
  │     │  ├─ 완성된 줄 → parse_stream_json_line()
  │     │  ├─ text chunk → on_chunk(chunk) 콜백 (Redis Stream)
  │     │  └─ idle 감지: last_data_time 갱신
  │     │
  │     │  동시에:
  │     │  ├─ 전체 타임아웃 감시 (deadline)
  │     │  └─ idle 타임아웃 감시 (마지막 출력 후 N초 무응답)
  │     │
  │     └─ EIO/EOF → 프로세스 종료 감지
  │
  ├─ 4) 프로세스 종료 대기
  │     │  pid, status = os.waitpid(pid, 0)
  │     │  exit_code 추출
  │     └─ master_fd close
  │
  └─ 5) TaskResult 반환
        (output, exit_code, session_id, usage)
```

#### 4.4.2 PTYProcess — 단일 프로세스 래퍼

```python
@dataclass
class PTYProcess:
    """PTY 세션으로 실행 중인 Claude Code 프로세스."""
    pid: int
    master_fd: int
    pgid: int                    # = pid (세션 리더)
    task_id: str
    started_at: float

    def is_alive(self) -> bool:
        try:
            os.kill(self.pid, 0)
            return True
        except OSError:
            return False

    async def terminate(self, grace_period: float = 5.0) -> int:
        """3단계 종료: SIGHUP → SIGTERM → SIGKILL.

        SIGHUP: PTY 세션 전체 (프로세스 그룹)에 전달.
                Claude Code 내부 자식 프로세스도 함께 종료.
        SIGTERM: SIGHUP 무시한 프로세스에 개별 전달.
        SIGKILL: 최후 수단.
        """
        if not self.is_alive():
            return self._reap()

        # Phase 1: SIGHUP → 프로세스 그룹 전체
        try:
            os.killpg(self.pgid, signal.SIGHUP)
        except OSError:
            pass
        if await self._wait(grace_period):
            return self._reap()

        # Phase 2: SIGTERM → 직접 프로세스
        try:
            os.kill(self.pid, signal.SIGTERM)
        except OSError:
            pass
        if await self._wait(grace_period):
            return self._reap()

        # Phase 3: SIGKILL → 강제 종료
        try:
            os.killpg(self.pgid, signal.SIGKILL)
        except OSError:
            pass
        return self._reap()

    async def _wait(self, timeout: float) -> bool:
        """timeout 내에 프로세스 종료 여부 확인."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                pid, status = os.waitpid(self.pid, os.WNOHANG)
                if pid != 0:
                    return True
            except ChildProcessError:
                return True
            await asyncio.sleep(0.1)
        return False

    def _reap(self) -> int:
        """좀비 프로세스 수거 + master_fd 정리."""
        try:
            _, status = os.waitpid(self.pid, os.WNOHANG)
            exit_code = os.WEXITSTATUS(status) if os.WIFEXITED(status) else -1
        except ChildProcessError:
            exit_code = -1
        try:
            os.close(self.master_fd)
        except OSError:
            pass
        return exit_code
```

#### 4.4.3 Executor 핵심 구현

```python
class ClaudeCodeExecutor:
    """PTY 기반 Claude Code CLI 실행기."""

    def __init__(self, claude_bin: str = "claude"):
        self.claude_bin = claude_bin
        self._active: dict[str, PTYProcess] = {}   # task_id → PTYProcess
        self._lock = asyncio.Lock()

    async def execute(
        self,
        task: Task,
        config: ClaudeConfig,
        on_chunk: Callable[[StreamEvent], Awaitable[None]] | None = None,
    ) -> TaskResult:
        cmd = self._build_command(task, config)
        env = self._build_env(config)
        cwd = config.work_dir or "."
        timeout = task.timeout or 600
        idle_timeout = 30               # 30초 무응답 → 행 감지

        # --- PTY 생성 + fork ---
        master_fd, slave_fd = pty.openpty()

        # 터미널 크기 설정 (ANSI 줄바꿈 방지)
        winsize = struct.pack("HHHH", 24, 200, 0, 0)  # rows=24, cols=200
        fcntl.ioctl(slave_fd, termios.TIOCSWINSZ, winsize)

        pid = os.fork()

        if pid == 0:
            # === 자식 프로세스 ===
            os.setsid()                     # 새 세션 리더 (핵심!)
            os.close(master_fd)

            # slave_fd → stdin/stdout/stderr
            os.dup2(slave_fd, 0)
            os.dup2(slave_fd, 1)
            os.dup2(slave_fd, 2)
            if slave_fd > 2:
                os.close(slave_fd)

            os.chdir(cwd)
            os.execvpe(cmd[0], cmd, env)
            os._exit(127)                   # exec 실패 시

        # === 부모 프로세스 ===
        os.close(slave_fd)

        # non-blocking 설정
        flags = fcntl.fcntl(master_fd, fcntl.F_GETFL)
        fcntl.fcntl(master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

        process = PTYProcess(
            pid=pid, master_fd=master_fd,
            pgid=pid, task_id=task.id,
            started_at=time.monotonic(),
        )

        async with self._lock:
            self._active[task.id] = process

        # --- 출력 읽기 ---
        try:
            output, usage = await self._read_pty_output(
                process, timeout, idle_timeout, on_chunk,
            )
            exit_code = await self._wait_for_exit(process)
        except (TimeoutError, IdleTimeoutError) as e:
            exit_code = await process.terminate()
            raise
        finally:
            async with self._lock:
                self._active.pop(task.id, None)

        # session_id 추출 (stream-json result 이벤트에서)
        return TaskResult(
            output=output,
            exit_code=exit_code,
            session_id=usage.get("session_id"),
            usage=TokenUsage(**usage) if usage else None,
        )

    async def _read_pty_output(
        self,
        process: PTYProcess,
        timeout: float,
        idle_timeout: float,
        on_chunk: Callable | None,
    ) -> tuple[str, dict]:
        """PTY master_fd에서 asyncio로 출력을 읽고 파싱."""
        loop = asyncio.get_event_loop()
        buffer = LineBuffer()
        texts: list[str] = []
        usage_info: dict = {}
        last_data_time = time.monotonic()
        deadline = time.monotonic() + timeout
        done = asyncio.Event()

        def _on_readable():
            nonlocal last_data_time
            try:
                data = os.read(process.master_fd, 4096)
                if not data:
                    done.set()
                    return
                last_data_time = time.monotonic()
                buffer.feed(data)

                for line in buffer.get_lines():
                    parsed = parse_stream_json_line(line)
                    if not parsed:
                        continue
                    if parsed["type"] == "text":
                        texts.append(parsed["content"])
                        if on_chunk:
                            asyncio.ensure_future(
                                on_chunk(StreamEvent(text=parsed["content"]))
                            )
                    elif parsed["type"] == "cost":
                        usage_info.update(parsed)
                    elif parsed["type"] == "result":
                        if "session_id" in parsed:
                            usage_info["session_id"] = parsed["session_id"]
                    elif parsed["type"] == "retry":
                        # API rate limit / billing / auth 등 재시도 이벤트
                        if on_chunk:
                            asyncio.ensure_future(
                                on_chunk(StreamEvent(
                                    type="retry",
                                    retry_info=parsed,
                                ))
                            )
                        # billing_error → 즉시 중단 (과금 방지)
                        if parsed["error"] == "billing_error":
                            raise BillingError(
                                f"Billing error (HTTP {parsed['error_status']}): "
                                f"API 결제/한도 문제 — 워커 중단 필요"
                            )

            except OSError:
                # EIO = PTY 자식 종료 → 정상
                done.set()

        loop.add_reader(process.master_fd, _on_readable)

        try:
            while not done.is_set():
                now = time.monotonic()

                # 전체 타임아웃
                if now >= deadline:
                    raise TimeoutError(
                        f"Process {process.pid} exceeded {timeout}s deadline"
                    )
                # idle 타임아웃 (행 감지)
                if now - last_data_time > idle_timeout:
                    raise IdleTimeoutError(
                        f"Process {process.pid} idle for {idle_timeout}s"
                    )

                wait_time = min(deadline - now, idle_timeout, 1.0)
                try:
                    await asyncio.wait_for(done.wait(), timeout=wait_time)
                except asyncio.TimeoutError:
                    continue
        finally:
            loop.remove_reader(process.master_fd)

        return "\n".join(texts), usage_info

    async def _wait_for_exit(self, process: PTYProcess) -> int:
        """프로세스 종료 대기 + 좀비 수거."""
        for _ in range(50):  # 최대 5초
            try:
                pid, status = os.waitpid(process.pid, os.WNOHANG)
                if pid != 0:
                    try:
                        os.close(process.master_fd)
                    except OSError:
                        pass
                    return os.WEXITSTATUS(status) if os.WIFEXITED(status) else -1
            except ChildProcessError:
                return -1
            await asyncio.sleep(0.1)
        return await process.terminate()

    async def cancel(self, task_id: str) -> bool:
        """실행 중인 태스크 취소."""
        process = self._active.get(task_id)
        if not process:
            return False
        await process.terminate()
        return True

    async def cleanup_all(self) -> int:
        """모든 활성 프로세스 종료 (워커 셧다운)."""
        count = 0
        async with self._lock:
            for process in list(self._active.values()):
                await process.terminate()
                count += 1
            self._active.clear()
        return count
```

#### 4.4.4 LineBuffer — 바이트 스트림 → 줄 단위 변환

PTY는 임의 길이의 바이트 청크를 반환하므로, 줄 단위로 조립하는 버퍼가 필요하다.

```python
class LineBuffer:
    """바이트 스트림을 줄 단위로 조립."""

    def __init__(self):
        self._buf = b""

    def feed(self, data: bytes) -> None:
        self._buf += data

    def get_lines(self) -> list[str]:
        """완성된 줄들을 반환. 마지막 미완성 줄은 버퍼에 유지."""
        lines = []
        while b"\n" in self._buf:
            line, self._buf = self._buf.split(b"\n", 1)
            decoded = line.decode("utf-8", errors="replace").strip()
            if decoded:
                lines.append(decoded)
        return lines
```

#### 4.4.5 StreamParser — stream-json 출력 파싱

Claude Code CLI의 `--output-format stream-json` 출력을 파싱한다.
텍스트, 비용, **API 재시도/에러 이벤트**를 분류한다.

```python
def parse_stream_json_line(line: str) -> Optional[Dict]:
    """stream-json 한 줄을 파싱.

    Returns:
        {"type": "text", "content": str}
        {"type": "cost", "cost_usd": float, "input_tokens": int, "output_tokens": int, ...}
        {"type": "retry", "error": str, "error_status": int, "attempt": int, ...}
        None (무시할 줄)
    """
    line = line.strip()
    if not line:
        return None

    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return None

    msg_type = obj.get("type", "")

    # --- 텍스트 결과 ---
    if msg_type == "result":
        result_text = obj.get("result", "")
        if isinstance(result_text, str) and result_text.strip():
            return {"type": "text", "content": result_text.strip()}
        # 비용/사용량
        cost_usd = obj.get("cost_usd")
        usage = obj.get("usage", {})
        if cost_usd is not None or usage:
            return {
                "type": "cost",
                "cost_usd": cost_usd,
                "input_tokens": usage.get("input_tokens", 0),
                "output_tokens": usage.get("output_tokens", 0),
                "cache_read_tokens": usage.get("cache_read_tokens", 0),
                "cache_write_tokens": usage.get("cache_write_tokens", 0),
                "duration_ms": obj.get("duration_ms"),
                "session_id": obj.get("session_id"),
            }

    elif msg_type == "assistant":
        content = obj.get("message", {}).get("content", [])
        texts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                texts.append(block["text"])
            elif isinstance(block, str):
                texts.append(block)
        if texts:
            return {"type": "text", "content": "\n".join(texts)}

    # --- API 재시도/에러 이벤트 ---
    elif msg_type == "system" and obj.get("subtype") == "api_retry":
        return {
            "type": "retry",
            "error": obj.get("error", "unknown"),
            "error_status": obj.get("error_status"),
            "attempt": obj.get("attempt", 0),
            "max_retries": obj.get("max_retries", 0),
            "retry_delay_ms": obj.get("retry_delay_ms", 0),
        }

    return None
```

**stream-json 이벤트 유형 전체:**

```json
// 1. 초기화
{"type": "system", "subtype": "init", "session_id": "abc-123"}

// 2. 어시스턴트 응답 (중간 출력)
{"type": "assistant", "message": {"content": [{"type": "text", "text": "분석 시작..."}]}}

// 3. API 재시도 (rate limit, billing, auth 등)
{"type": "system", "subtype": "api_retry", "attempt": 1, "max_retries": 3,
 "retry_delay_ms": 5000, "error_status": 429, "error": "rate_limit"}

// 4. 최종 결과
{"type": "result", "result": "분석 완료", "cost_usd": 0.015,
 "usage": {"input_tokens": 500, "output_tokens": 200,
           "cache_read_tokens": 100, "cache_write_tokens": 50},
 "duration_ms": 8500, "session_id": "abc-123"}
```

**API 에러 유형 분류:**

| `error` 필드 | `error_status` | 의미 | open_kknaks 대응 |
|---|---|---|---|
| `rate_limit` | 429 | RPM/TPM 초과 | CLI가 자동 재시도. RateLimitMiddleware가 요청 속도 감소 |
| `billing_error` | 402 | 결제 실패 / 월간 spend limit 도달 | **즉시 워커 중단** + 알림. BillingError 예외 발생 |
| `authentication_failed` | 401 | API 키 만료/무효 | **즉시 워커 중단** + 알림. ClaudeAuthError 예외 발생 |
| `server_error` | 500/529 | Anthropic 서버 장애/과부하 | CLI가 자동 재시도. 로그 기록 |
| `max_output_tokens` | null | 모델 최대 출력 도달 | 로그 기록 (제어 불가 — CLI에 `--max-tokens` 없음) |
| `unknown` | 기타 | 미분류 에러 | 로그 기록 |

**Claude Code CLI의 토큰/비용 제어 한계:**

```
                        Claude Code CLI    Anthropic API    Agent SDK
                        ──────────────     ─────────────    ─────────
max output tokens       ❌ 없음            ✅ max_tokens    ✅ max_tokens
max input tokens        ❌ 없음            ❌ 없음          ❌ 없음
max budget (비용)       ✅ --max-budget-usd ❌ 없음          ✅
max turns               ✅ --max-turns      ❌ 없음          ✅
thinking tokens         ✅ --effort         ✅ thinking.*    ✅
                        ✅ MAX_THINKING_TOKENS (env)
RPM/TPM 설정            ❌ API 백엔드 강제  ❌ API 백엔드 강제  ❌
```

> **참고:** Claude Code CLI는 `--max-tokens` 플래그를 제공하지 않는다.
> 출력 길이 제한이 필요하면 `--max-budget-usd`로 비용 기반 간접 제어하거나,
> Agent SDK를 직접 사용해야 한다.

#### 4.4.6 CLI 플래그 빌드 (_build_command)

Worker 기본값과 Task 오버라이드를 병합한 뒤, Claude Code CLI 플래그로 변환한다.

```python
def _build_command(self, task: Task, config: ClaudeConfig) -> list[str]:
    cmd = [self.claude_bin, "-p", task.prompt]
    cmd += ["--output-format", "stream-json"]
    cmd += ["--dangerously-skip-permissions"]    # 비대화형 실행 필수

    # LLM / 프롬프트
    if config.model:
        cmd += ["--model", config.model]
    if config.system_prompt:
        cmd += ["--system-prompt", config.system_prompt]
    if config.append_system_prompt:
        cmd += ["--append-system-prompt", config.append_system_prompt]
    if config.max_turns:
        cmd += ["--max-turns", str(config.max_turns)]
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
| `effort` | `--effort` | low/medium/high/max |
| `json_schema` | `--json-schema` | 구조화 출력 |
| `allowed_tools` | `--allowedTools` | |
| `disallowed_tools` | `--disallowedTools` | |
| `permission_mode` | `--permission-mode` / `--dangerously-skip-permissions` | |
| `session_id` | `--resume` | 세션 이어가기 |
| `mcp_config` | `--mcp-config` | MCP 서버 연결 |
| `add_dirs` | `--add-dir` | 추가 접근 디렉토리 |
| `context` | stdin 파이프 | `echo context \| claude -p` |
| (항상) | `--output-format stream-json` | 파싱용 고정 |
| (항상) | `-p` | 비대화형 모드 |
| (항상) | `--dangerously-skip-permissions` | 비대화형 실행 필수 |

#### 4.4.6 Pipe vs PTY 비교 — 왜 PTY인가

```
                    Pipe 방식 (기존 프로젝트들)          PTY 방식 (open_kknaks)
                    ─────────────────────────          ──────────────────────

프로세스 생성       asyncio.create_subprocess_exec     os.fork() + os.setsid()
                    stdout=PIPE, stderr=PIPE           slave_fd → stdin/stdout/stderr

출력 읽기           proc.stdout.readline()             os.read(master_fd, 4096)
                    await (blocking per line)           + loop.add_reader (non-blocking)

버퍼링             블록 버퍼(~4KB 뭉침)                라인 즉시 전달

프로세스 그룹      없음 (직접 자식만 관리)              os.setsid() → 세션 리더
                                                        pgid = pid (전체 트리 제어)

종료               proc.terminate() → SIGTERM          os.killpg(pgid, SIGHUP)
                    (직접 자식만)                       → 전체 프로세스 트리

고아 프로세스      Claude 내부 자식 누수 가능           SIGHUP 전파로 전체 정리

데드락 위험        stdout/stderr 동시 PIPE              단일 fd — 불가능
                    → stderr 버퍼 포화 시 블록

행(Hang) 감지      라인 타임아웃 → continue             idle_timeout → 즉시 예외
                    → 전체 600s 대기                     → 빠른 실패

concurrency        좀비 누적 위험                      세션별 격리 — 안전
(4+ 동시 실행)

구현 복잡도        낮음 (asyncio 내장)                  중간 (pty + fork + asyncio 연동)

플랫폼             Linux/macOS/Windows                 Linux/macOS (Windows 미지원)
```

**결론:** `open_kknaks`는 라이브러리로서 다수의 Claude Code 프로세스를 장시간 안정적으로
관리해야 한다. Pipe 방식의 고아 프로세스 누수, 버퍼 데드락, 행 감지 지연은 프로덕션
환경에서 치명적이므로, PTY 기반 Executor를 사용한다.

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

## 6. 인증

### 6.1 인증 방식 — OAuth 전용

open_kknaks는 Claude Code CLI의 **내장 OAuth 인증**만 사용한다.
API Key(`ANTHROPIC_API_KEY`) 방식은 지원하지 않는다.

```
사용자가 1회 실행: claude login
    │
    └─ Claude Code CLI가 OAuth 인증
    └─ ~/.claude/ 에 크리덴셜 저장

이후 open_kknaks 사용 시:
    │
    └─ Worker가 claude -p ... 실행
    └─ CLI가 ~/.claude/ 에서 자동으로 크리덴셜 읽음
    └─ 추가 로그인 불필요
```

**이미 로컬에서 Claude Code를 쓰고 있다면 추가 작업 없이 바로 사용 가능.**

| 항목 | 설명 |
|---|---|
| 인증 방식 | OAuth (`claude login`) |
| 크리덴셜 위치 | `~/.claude/` (CLI가 관리) |
| 과금 | Claude Pro/Max 구독 포함 |
| 일일 한도 | 있음 (구독 플랜별) |
| API Key | 사용 안 함 |
| `--bare` 모드 | 사용 안 함 (OAuth는 bare 불필요) |

### 6.2 인증 확인 — Worker 시작 시

Worker가 시작할 때 CLI 설치 + 로그인 상태를 **확인만** 한다. 로그인을 대신하지 않는다.

```python
async def verify_claude_auth(claude_bin: str) -> None:
    """Worker 시작 시 Claude Code 인증 상태 확인."""

    # 1. CLI 바이너리 존재 확인
    if not shutil.which(claude_bin or "claude"):
        raise ClaudeNotFoundError(
            "claude 바이너리를 찾을 수 없습니다. "
            "https://claude.ai/download 에서 설치하세요."
        )

    # 2. 로그인 상태 확인
    proc = await asyncio.create_subprocess_exec(
        claude_bin or "claude", "auth", "status",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()

    if proc.returncode != 0:
        raise ClaudeAuthError(
            "Claude Code 로그인이 필요합니다. "
            "터미널에서 'claude login'을 실행하세요."
        )
```

**호출 시점:**

```
ClaudeWorker.run()
    │
    ├─ verify_claude_auth()     ← 시작 시 1회 확인
    │   ├─ 바이너리 없음 → ClaudeNotFoundError
    │   └─ 로그인 안 됨 → ClaudeAuthError
    │
    ├─ before_worker_boot 미들웨어
    │
    └─ DequeueLoop + ProcessorLoop 시작
```

> **참고:** ClaudeClient(프로듀서)는 인증 확인하지 않음.
> Redis에 작업만 넣으므로 Claude CLI가 필요 없음.

### 6.3 Docker 환경

Worker가 Docker 안에서 실행될 때 claude를 못 찾으면 에러 메시지로 안내한다:

```python
if not shutil.which("claude"):
    if Path("/.dockerenv").exists():
        raise ClaudeNotFoundError(
            "Docker 환경에서 Claude CLI를 찾을 수 없습니다.\n"
            "\n"
            "docker-compose.yml에 추가하세요:\n"
            "\n"
            "  volumes:\n"
            "    - ~/.claude:/root/.claude:ro\n"
            "    - <NODE_PREFIX>:/host-node:ro\n"
            "  environment:\n"
            "    - PATH=/host-node/bin:$PATH\n"
            "\n"
            "호스트에서 경로 확인:\n"
            "  dirname $(dirname $(realpath $(which claude)))"
        )
```

---

## 7. ClaudeConfig

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

**재사용 예시:**
```python
config = ClaudeConfig(model="sonnet", work_dir="/my/project", effort="high")

worker_a = ClaudeWorker(broker=broker, queues=["queue-a"], claude=config, concurrency=4)
worker_b = ClaudeWorker(broker=broker, queues=["queue-b"], claude=config, concurrency=2)
```

### 7.1 Config 병합

MergedConfig 별도 클래스를 만들지 않는다. `ClaudeConfig.model_copy(update={})` 사용.

**병합 위치:** `Worker._merge_config(task)` → ClaudeConfig 복사본 반환.

```python
# 오버라이드 가능 필드 (화이트리스트)
_OVERRIDABLE_FIELDS = {
    "model", "system_prompt", "append_system_prompt", "max_turns",
    "effort", "json_schema", "allowed_tools", "disallowed_tools",
    "permission_mode", "mcp_config", "add_dirs",
}

# 오버라이드 불가 (보안): work_dir, claude_bin

def _merge_config(self, task: Task) -> ClaudeConfig:
    """Worker 기본 ClaudeConfig에 Task 오버라이드를 적용한 복사본 반환."""
    overrides = {}
    for field in _OVERRIDABLE_FIELDS:
        value = getattr(task, field, None)
        if value is not None:
            overrides[field] = value
    return self.claude.model_copy(update=overrides)
```

> **보안:** `work_dir`과 `claude_bin`은 Task에서 오버라이드할 수 없다.
> 프로듀서가 임의의 디렉토리에서 CLI를 실행하는 것을 방지한다.

---

## 7. Task 모델

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
    work_dir: str | None = None

    # LLM / 프롬프트
    model: str | None = None                        # --model
    system_prompt: str | None = None                # --system-prompt (전체 교체)
    append_system_prompt: str | None = None         # --append-system-prompt (추가)
    max_turns: int | None = None                    # --max-turns
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
    timeout: int | None = None

    # 재시도
    max_retries: int = 0
    retry_count: int = 0

    # 결과
    result: str | None = None
    error: str | None = None
    exception_type: str | None = None               # 예외 클래스명 (예: "BillingError")
    exit_code: int | None = None
    result_session_id: str | None = None            # 실행 후 반환된 세션 ID (이어가기용)
    usage: TokenUsage | None = None

    # 배치
    batch_id: str | None = None

    # 유저 메타
    metadata: dict[str, str | int | float | bool | None] = Field(default_factory=dict)

    # 타임스탬프 — datetime.now(timezone.utc) 사용, datetime.utcnow() 안 씀
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: datetime | None = None
    finished_at: datetime | None = None
```

> **datetime 정책:** `datetime.utcnow()` 대신 `datetime.now(timezone.utc)` 사용.
> `utcnow()`는 naive datetime을 반환하므로, aware datetime을 강제한다.

> **StreamEvent 타입:** text/cost/retry 3가지 유지. 작업 상태 변경(RUNNING, DONE, FAILED 등)은
> `Task.status`로 관장하며, StreamEvent 타입을 추가로 확장하지 않는다.

---

## 8. 미들웨어

시그널 6개. 기본 제공 6개.

### 8.1 시그널

미들웨어 생성자는 설정값만 받는다. broker는 시그널 메서드 호출 시 인자로 전달된다.

```python
class Middleware:
    """미들웨어 기본 클래스.

    생성자: 설정값만 받음 (broker 없음).
    시그널 메서드: broker를 첫 번째 인자로 받음.
    """

    async def before_enqueue(self, broker: AbstractBroker, task: Task) -> Task | None:
        """큐 등록 전. None 반환 시 취소."""
        return task

    async def after_enqueue(self, broker: AbstractBroker, task: Task) -> None:
        """큐 등록 후."""

    async def before_process(self, broker: AbstractBroker, task: Task) -> None:
        """실행 전. 중단하려면 예외를 던진다 (예외 기반 sequential break)."""

    async def after_process(self, broker: AbstractBroker, task: Task, *,
                            result=None, exception=None) -> None:
        """실행 후. 성공이면 result, 실패면 exception."""

    async def before_worker_boot(self, broker: AbstractBroker, worker) -> None: ...
    async def after_worker_shutdown(self, broker: AbstractBroker, worker) -> None: ...
```

### 8.1.1 미들웨어 체인 동작

**before_process: 예외 기반 sequential break (Dramatiq 방식)**
- 미들웨어를 순서대로(정순) 실행
- 미들웨어가 예외를 던지면 즉시 체인 중단, 이후 미들웨어의 before_process는 호출되지 않음
- 반환값으로 skip 판단하지 않음 (v1의 `None 반환 → skip` 패턴 폐기)

**after_process: 역순 실행 (스택, Dramatiq 방식)**
- before_process가 호출된 미들웨어의 역순으로 실행
- 예외 발생 시에도 **모든** 미들웨어의 after_process가 호출됨 (역순)
- 리소스 정리가 보장됨

**재시도:** RetriesMiddleware가 `after_process`에서 `broker.enqueue(task, delay=backoff)` 직접 호출.

```
before_process 실행 순서 (정순):
  Middleware_A.before_process(broker, task)  ← 예외 발생 시 여기서 중단
  Middleware_B.before_process(broker, task)
  Middleware_C.before_process(broker, task)

after_process 실행 순서 (역순):
  Middleware_C.after_process(broker, task, result=..., exception=...)
  Middleware_B.after_process(broker, task, result=..., exception=...)
  Middleware_A.after_process(broker, task, result=..., exception=...)
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
    """지수 백오프 재시도 + DLQ.

    생성자는 설정값만 받음. broker는 after_process에서 인자로 전달받음.
    재시도: after_process에서 broker.enqueue(task, delay=backoff) 직접 호출.
    """

    def __init__(
        self,
        max_retries: int = 3,
        min_backoff: float = 5.0,       # 초
        max_backoff: float = 300.0,     # 초
        backoff_factor: float = 2.0,
        no_retry_on: tuple = (TaskCancelledError, ClaudeAuthError, BillingError),
    ): ...

    async def after_process(self, broker: AbstractBroker, task: Task, *,
                            result=None, exception=None):
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
    """비용 추적 + 한도 관리 + API billing error 대응.

    3단계 비용 제어:
    1. Worker 단위: worker_budget_usd → 워커 누적 비용 한도
    2. 전체 단위: global_budget_usd → namespace 전체 비용 한도 (Redis에 저장)
    3. API 단위: billing_error(402) → 즉시 워커 중단 (구독 한도 도달)
    """

    def __init__(
        self,
        worker_budget_usd: float | None = None,    # 워커 누적 한도
        global_budget_usd: float | None = None,     # 전체 한도
        on_budget_alert: Callable | str | None = None,  # 한도 도달 시 콜백/webhook
        alert_threshold: float = 0.8,               # 80%에서 경고
    ): ...

    async def after_process(self, broker, task, *, result=None, exception=None):
        # --- API billing error 감지 ---
        if isinstance(exception, BillingError):
            # Anthropic 측 결제/한도 문제 → 즉시 알림 + 워커 중단 권고
            await self._alert(
                f"API billing error: {exception}. "
                f"Anthropic 월간 spend limit 또는 결제 문제. 워커 중단 필요."
            )
            # RetriesMiddleware가 재시도하지 않도록 no_retry 예외로 전파
            return

        if result and result.usage:
            # 1) task에 비용 기록
            task.usage = result.usage
            await broker.update_task(task)

            # 2) 워커 누적 비용 갱신
            self._worker_spent += result.usage.cost_usd or 0

            # 3) 전체 누적 비용 갱신 (Redis INCRBYFLOAT)
            await broker.incr_cost(result.usage.cost_usd or 0)

            # 4) 워커/전체 한도 체크
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

**비용 제어 전체 흐름:**

```
Task 실행 시작
    │
    ├─ [before_process] CostMiddleware: 우리 쪽 한도(Worker/전체) 체크
    │   └─ 초과 → skip (DLQ)
    │
    ├─ [execute] PTY Executor: claude -p ...
    │   │
    │   ├─ stream-json 중 rate_limit(429) → CLI가 자동 재시도
    │   │   └─ RateLimitMiddleware가 감지 → 요청 속도 감속
    │   │
    │   ├─ stream-json 중 billing_error(402) → BillingError 예외
    │   │   └─ CostMiddleware가 감지 → 알림 + 워커 중단 권고
    │   │   └─ (구독 일일 한도 초과 = "00시 이후에 시도하세요")
    │   │
    │   ├─ stream-json 중 max_output_tokens → 로그 기록
    │   │   └─ (제어 불가 — 구독 플랜 한계)
    │   │
    │   └─ 정상 완료 → cost/usage 정보 포함
    │
    └─ [after_process] CostMiddleware: 사용량 기록 + 한도 재체크
        ├─ Redis INCRBYFLOAT로 누적
        └─ threshold 도달 시 경고 알림
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
    """우리 쪽 요청 속도 제한 + API rate limit 피드백 반영.

    2중 방어:
    1. 선제적 제한: max_per_minute으로 요청 속도 제한 (before_process)
    2. 반응적 감속: API에서 429 받으면 자동으로 속도 줄임 (after_process)
    """

    def __init__(
        self,
        max_per_minute: int = 30,             # 선제적 제한
        adaptive: bool = True,                 # API 429 시 자동 감속
        backoff_factor: float = 0.5,           # 429 시 속도를 50%로 줄임
        recovery_factor: float = 1.1,          # 성공 시 속도를 10%씩 복구
    ): ...

    async def before_process(self, broker, task):
        """분당 요청 수 초과 시 지연."""
        await self._wait_if_needed()
        return task

    async def after_process(self, broker, task, *, result=None, exception=None):
        """API rate limit 피드백 반영."""
        if result and result.retry_events:
            rate_limit_hits = [e for e in result.retry_events if e["error"] == "rate_limit"]
            if rate_limit_hits and self.adaptive:
                # 429를 받았으면 요청 속도를 줄임
                self._current_rpm = int(self._current_rpm * self.backoff_factor)
                logger.warning("API rate limit hit, reducing to %d RPM", self._current_rpm)
        elif result and not result.retry_events:
            # 성공이고 재시도 없었으면 서서히 속도 복구
            if self.adaptive and self._current_rpm < self.max_per_minute:
                self._current_rpm = min(
                    int(self._current_rpm * self.recovery_factor),
                    self.max_per_minute,
                )
```

---

## 9. CLI

### 9.1 워커 실행

```bash
open-kknaks worker \
    --broker redis://localhost:6379 \
    --namespace myapp \
    --queues error-analysis,general \
    --work-dir /my/backend \
    --concurrency 4 \
    --model sonnet \
    --effort high \
    --max-turns 10 \
    --poll-interval 0.5 \
    --heartbeat-interval 30 \
    --shutdown-timeout 300

# 환경변수로도 가능
OPEN_KKNAKS_BROKER_URL=redis://localhost:6379 \
OPEN_KKNAKS_NAMESPACE=myapp \
OPEN_KKNAKS_QUEUES=error-analysis,general \
open-kknaks worker
```

**워커 옵션 전체:**

| 옵션 | 환경변수 | 기본값 | 설명 |
|---|---|---|---|
| `--broker` | `OPEN_KKNAKS_BROKER_URL` | `redis://localhost:6379` | Redis 브로커 URL |
| `--namespace` | `OPEN_KKNAKS_NAMESPACE` | `open_kknaks` | Redis 키 네임스페이스 |
| `--queues` | `OPEN_KKNAKS_QUEUES` | `default` | 소비할 큐 (쉼표 구분) |
| `--concurrency` | `OPEN_KKNAKS_CONCURRENCY` | `4` | 동시 Claude Code 프로세스 수 |
| `--work-dir` | `OPEN_KKNAKS_WORK_DIR` | `.` | Claude Code 작업 디렉토리 |
| `--poll-interval` | | `0.5` | 큐 폴링 간격 (초) |
| `--heartbeat-interval` | | `30` | 헬스체크 간격 (초) |
| `--shutdown-timeout` | | `300` | 그레이스풀 셧다운 대기 (초) |

**Claude Code 옵션 (ClaudeConfig):**

| 옵션 | 환경변수 | 기본값 | 설명 |
|---|---|---|---|
| `--model` | `OPEN_KKNAKS_MODEL` | (없음) | Claude 모델 |
| `--effort` | `OPEN_KKNAKS_EFFORT` | (없음) | low/medium/high/max |
| `--max-turns` | `OPEN_KKNAKS_MAX_TURNS` | (없음) | 에이전트 최대 턴 수 |
| `--system-prompt` | | (없음) | 시스템 프롬프트 전체 교체 |
| `--append-system-prompt` | | (없음) | 시스템 프롬프트 추가 |
| `--allowed-tools` | | (없음) | 허용 도구 (쉼표 구분) |
| `--disallowed-tools` | | (없음) | 차단 도구 (쉼표 구분) |
| `--permission-mode` | | `default` | 권한 모드 |
| `--mcp-config` | | (없음) | MCP 설정 파일 경로 |
| `--add-dir` | | (없음) | 추가 접근 디렉토리 |

**Dramatiq와 비교:**

```
Dramatiq:     dramatiq my_module --processes 4 --threads 8
open_kknaks:  open-kknaks worker --concurrency 4 --queues error-analysis
```

| Dramatiq | open_kknaks | 차이 |
|---|---|---|
| `--processes` | (없음) | 워커 1개가 1프로세스. 스케일은 워커를 여러 개 띄움 |
| `--threads` | `--concurrency` | PTY fork 기반이라 스레드가 아닌 동시 프로세스 수 |
| 모듈 지정 | (없음) | 실행 대상이 Claude Code CLI로 고정 |

### 9.2 큐 / DLQ / 작업 관리

```bash
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
