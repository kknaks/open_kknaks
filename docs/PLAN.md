# open_kknaks — 구현 계획

> **Version:** 3.0
> **Updated:** 2026-03-26
> **근거 문서:** ARCHITECTURE_V2.md, PRD.md, CLAUDE_CODE_ANALYSIS.md

---

## 1. 한 줄 정의

Claude Code CLI를 **PTY 기반으로 안정적으로 실행**하는 프로듀서/워커 분리형 태스크 큐 라이브러리.

---

## 2. 핵심 설계 결정 (왜 이렇게 만드는가)

| 결정 | 이유 | 근거 |
|---|---|---|
| **PTY** (not Pipe) | 프로세스 그룹 관리, 고아 방지, 버퍼 데드락 없음, 행 감지 | CLAUDE_CODE_ANALYSIS.md — app_builder_local/persona_counselor 모두 Pipe 사용했으나 프로덕션 안정성 부족 |
| **프로듀서/워커 분리** | 수평 확장, 멀티 머신 배포 | ARCHITECTURE_V2.md §1 — Dramatiq 패턴 차용 |
| **Redis 전용** (InMemory 제거) | 프로듀서/워커 분리 원칙에 InMemory 위배 | ARCHITECTURE_V2.md §11 — v1→v2 변경 |
| **멀티 큐** | 작업 유형/환경별 워커 라우팅 | ARCHITECTURE_V2.md §2 — 큐 = 작업 유형 단위 |
| **3단계 종료** (SIGHUP→SIGTERM→SIGKILL) | PTY 세션 전체 프로세스 트리 정리 | ARCHITECTURE_V2.md §4.4.2 |
| **ClaudeConfig 분리** | 여러 Worker에서 재사용 | ARCHITECTURE_V2.md §6 |
| **Linux/macOS 전용** | PTY는 POSIX 전용 | PRD.md §14 — Windows는 Non-Goal |
| **Task.exception_type** | 실패 원인 분류 (BillingError 등). StreamEvent 타입 확장 안 함 | 설계 결정 §11 |
| **Config 병합: model_copy** | MergedConfig 별도 클래스 안 만듦. `ClaudeConfig.model_copy(update={})` 사용 | 설계 결정 §11 |
| **Middleware → Broker 접근** | 시그널 메서드에 broker 인자 전달 (MW가 직접 enqueue 가능) | 설계 결정 §11 — Dramatiq 방식 |
| **MW 체인: 예외 기반 break** | before 순차/예외 break, after 역순/예외에도 전부 호출 | 설계 결정 §11 |
| **client.result()/stream()** | XREAD BLOCK 기반. 폴링 안 씀 | 설계 결정 §11 |

---

## 3. 전체 구조

```
ClaudeClient ──enqueue──▶ RedisBroker ◀──dequeue── ClaudeWorker
                              │                        │
                         Middleware                ClaudeConfig
                   (Log, Retry, Timeout,               │
                    Cost, Rate, Callback)         PTY Executor
                                                       │
                                                  os.fork()
                                                  os.setsid()
                                                  claude -p ...
```

```
┌─────────────────────────────────────────────────────────┐
│  유저 코드 (프로듀서)                                     │
│  client = ClaudeClient(broker=RedisBroker(...))         │
│  await client.submit("분석해줘", queue="error-analysis") │
└──────────────────────┬──────────────────────────────────┘
                       │ enqueue
                       ▼
┌─────────────────────────────────────────────────────────┐
│  RedisBroker                                            │
│  큐: error-analysis, pr-review, default, ...            │
│  DLQ: {queue}.dlq    스트림: stream:{task_id}           │
└──────┬──────────────────────────────┬───────────────────┘
       ▼                              ▼
┌──────────────────┐    ┌──────────────────┐
│  Worker A        │    │  Worker B        │
│  queues:         │    │  queues:         │
│   error-analysis │    │   pr-review      │
│  concurrency: 4  │    │  concurrency: 2  │
│  ┌────────────┐  │    │  ┌────────────┐  │
│  │PTY Executor│  │    │  │PTY Executor│  │
│  │ fork+setsid│  │    │  │ fork+setsid│  │
│  └────────────┘  │    │  └────────────┘  │
└──────────────────┘    └──────────────────┘
```

---

## 4. 패키지 구조

```
open_kknaks/
├── __init__.py              # ClaudeClient, Task, ClaudeConfig export
├── client.py                # ClaudeClient (프로듀서)
├── config.py                # ClaudeConfig
├── task.py                  # Task, TaskStatus, Priority, TaskResult, TokenUsage, StreamEvent
├── batch.py                 # BatchRunner, BatchStatus
├── broker/
│   ├── __init__.py
│   ├── base.py              # AbstractBroker
│   ├── redis.py             # RedisBroker
│   └── lua/                 # enqueue/dequeue/ack/nack/requeue/maintenance.lua
├── worker/
│   ├── __init__.py
│   ├── worker.py            # ClaudeWorker
│   ├── executor.py          # ClaudeCodeExecutor (PTY 기반)
│   ├── pty_process.py       # PTYProcess (fork + setsid + 3단계 종료)
│   ├── line_buffer.py       # LineBuffer (바이트 → 줄)
│   └── stream_parser.py     # stream-json 파싱 (text/cost/retry 분류)
├── middleware/
│   ├── __init__.py
│   ├── base.py              # Middleware ABC (6개 시그널)
│   ├── logging.py
│   ├── retries.py
│   ├── timeout.py
│   ├── cost.py              # 3단계 비용 제어 (Worker/전체/API billing)
│   ├── rate_limit.py
│   └── callback.py
├── mcp/
│   ├── __init__.py
│   ├── server.py
│   └── __main__.py
├── cli/
│   ├── __init__.py
│   ├── main.py              # typer 진입점
│   ├── worker_cmd.py
│   ├── queue_cmd.py
│   ├── dlq_cmd.py
│   └── task_cmd.py
├── exceptions.py
└── py.typed
```

---

## 5. 구현 순서 — 의존 관계 기반

파일 간 의존 관계를 기준으로, 아래에서 위로 쌓아 올린다.

```
Layer 0 (의존 없음)
  ├─ task.py            — Task, TaskStatus, Priority, TokenUsage, StreamEvent, TaskResult
  ├─ config.py          — ClaudeConfig
  ├─ exceptions.py      — 예외 계층 (BillingError, ClaudeAuthError 등)
  └─ worker/line_buffer.py — LineBuffer

Layer 1 (Layer 0에 의존)
  ├─ worker/pty_process.py    — PTYProcess (task.py, exceptions.py)
  ├─ worker/stream_parser.py  — parse_stream_json_line (text/cost/retry 분류)
  └─ broker/base.py           — AbstractBroker (task.py)

Layer 2 (Layer 0-1에 의존)
  ├─ worker/executor.py    — ClaudeCodeExecutor (pty_process, line_buffer, stream_parser, config)
  ├─ broker/redis.py       — RedisBroker (base, task)
  └─ broker/lua/*.lua      — Lua 스크립트

Layer 3 (Layer 0-2에 의존)
  ├─ middleware/base.py     — Middleware ABC (task)
  ├─ middleware/*.py        — 6개 미들웨어 (base, task, broker)
  └─ worker/worker.py      — ClaudeWorker (executor, broker, middleware, config)

Layer 4 (Layer 0-3에 의존)
  ├─ client.py             — ClaudeClient (broker, task)
  ├─ batch.py              — BatchRunner (client, task)
  └─ __init__.py           — public export

Layer 5 (Layer 0-4에 의존)
  ├─ cli/                  — CLI 도구 (client, worker, broker)
  └─ mcp/                  — MCP 서버 (client, broker)
```

---

## 6. Sprint 계획

### Sprint 1 — PTY Executor + 코어 (1.5주)

**목표:** PTY로 Claude Code 스폰 → stream-json 파싱 → Redis 경유 결과 반환

| # | 작업 | 파일 | 의존 | 완료 기준 |
|---|---|---|---|---|
| 1-1 | 프로젝트 세팅 | pyproject.toml, ruff, mypy, CI | — | `uv sync` + `uv run ruff check` + `uv run mypy` 통과 |
| 1-2 | 데이터 모델 | task.py, config.py, exceptions.py | — | pydantic 직렬화/역직렬화 테스트 |
| 1-3 | LineBuffer | worker/line_buffer.py | — | 바이트 청크 → 줄 조립 테스트 |
| 1-4 | **PTYProcess** | worker/pty_process.py | 1-2 | fork+setsid, 3단계 종료, 고아 정리 검증 |
| 1-4b | **StreamParser** | worker/stream_parser.py | 1-2 | text/cost/retry 분류 + billing_error→BillingError |
| 1-5 | **PTY Executor** | worker/executor.py | 1-3, 1-4, 1-4b | PTY로 `claude -p` 실행 → stream-json 파싱 → TaskResult |
| 1-6 | AbstractBroker | broker/base.py | 1-2 | 인터페이스 정의 |
| 1-7 | RedisBroker | broker/redis.py, broker/lua/ | 1-6 | enqueue/dequeue/ack/nack + Lua 스크립트 |
| 1-8 | ClaudeWorker | worker/worker.py | 1-5, 1-7 | 큐 소비 + PTY 실행 + concurrency |
| 1-9 | ClaudeClient | client.py | 1-7 | submit/status/result/stream/cancel |
| 1-10 | 테스트 | tests/ | 전부 | unit + integration (fakeredis[lua]) |

**핵심 검증:**
- [ ] PTYProcess: 자식 + 손자 프로세스가 SIGHUP으로 모두 정리되는가
- [ ] PTY Executor: idle_timeout(30s)으로 행 감지되는가
- [ ] PTY Executor: `--output-format stream-json` 출력이 PTY에서도 깨끗한 JSON인가
- [ ] StreamParser: text/cost/retry 3가지 타입 정확히 분류되는가
- [ ] StreamParser: billing_error(402) → BillingError 예외 발생하는가
- [ ] StreamParser: rate_limit(429) → retry 이벤트로 on_chunk 콜백 전달되는가
- [ ] Worker concurrency=4에서 좀비/고아 누적 없는가
- [ ] RedisBroker: enqueue → dequeue → ack 사이클 정상인가

### Sprint 2 — 미들웨어 + 배치 + DLQ (1주)

**목표:** 운영 수준의 재시도/비용/DLQ 관리

| # | 작업 | 파일 | 의존 | 완료 기준 |
|---|---|---|---|---|
| 2-1 | Middleware ABC | middleware/base.py | S1 | 6개 시그널 파이프라인 |
| 2-2 | LoggingMiddleware | middleware/logging.py | 2-1 | structlog>=24.1 연동 |
| 2-3 | RetriesMiddleware | middleware/retries.py | 2-1 | 지수 백오프 + DLQ 이동 |
| 2-4 | TimeoutMiddleware | middleware/timeout.py | 2-1 | PTY 프로세스 강제 종료 연동 |
| 2-5 | **CostMiddleware** | middleware/cost.py | 2-1 | 3단계 비용 제어 (Worker/전체/API billing) |
| 2-6 | RateLimitMiddleware | middleware/rate_limit.py | 2-1 | 분당 요청 제한 |
| 2-7 | CallbackMiddleware | middleware/callback.py | 2-1 | webhook/함수 콜백 |
| 2-8 | DLQ 관리 | broker/redis.py 확장 | S1 | nack→DLQ, retry, purge |
| 2-9 | BatchRunner | batch.py | S1 | parallel/sequential 모드 |
| 2-10 | 테스트 | tests/ | 전부 | 미들웨어 체인 + DLQ + 배치 |

**핵심 검증:**
- [ ] RetriesMiddleware: 실패 → 지수 백오프 재큐잉 → max_retries 초과 시 DLQ
- [ ] RetriesMiddleware: BillingError/ClaudeAuthError는 재시도 안 하는가
- [ ] CostMiddleware: Worker 누적 비용 한도 도달 → 작업 거부
- [ ] CostMiddleware: 전체 비용 한도 (Redis INCRBYFLOAT) 정확한가
- [ ] CostMiddleware: BillingError(402) → 알림 + 워커 중단 권고
- [ ] RateLimitMiddleware: API 429 감지 시 자동 감속 (adaptive)
- [ ] RateLimitMiddleware: 성공 시 서서히 속도 복구
- [ ] TimeoutMiddleware: PTY Executor의 3단계 종료와 정상 연동되는가

### Sprint 3 — CLI + MCP + 배포 (1주)

**목표:** `uv pip install open-kknaks[redis]` → 즉시 사용 가능

| # | 작업 | 파일 | 의존 | 완료 기준 |
|---|---|---|---|---|
| 3-1 | CLI worker 커맨드 | cli/worker_cmd.py | S1-S2 | `open-kknaks worker --queues X` 동작 |
| 3-2 | CLI queue/dlq/task 커맨드 | cli/*.py | S1-S2 | 큐 조회, DLQ 관리, 작업 취소 |
| 3-3 | MCP Server (stdio) | mcp/server.py | S1-S2 | Claude Desktop 연동 |
| 3-4 | MCP Server (streamable-http) | mcp/server.py | 3-3 | HTTP 서버 모드 (mcp>=1.6) |
| 3-5 | README.md | README.md | 전부 | Quick Start + API Reference |
| 3-6 | PyPI 배포 | .github/workflows/ | 전부 | `uv build` + `uv publish` 성공 |
| 3-7 | E2E 테스트 | tests/ | 전부 | 실제 `claude -p` 호출 (수동 트리거) |

**핵심 검증:**
- [ ] CLI: `open-kknaks worker` → PTY Executor로 Claude Code 실행
- [ ] MCP: Claude Desktop에서 submit_task → 결과 수신
- [ ] MCP: streamable-http 모드로 원격 접속 동작
- [ ] PyPI: `uv pip install open-kknaks[redis]` → import + 기본 동작

---

## 7. 구현 상세 — 핵심 컴포넌트별

### 7.1 PTYProcess (S1-4)

PTY 기반 프로세스 래퍼. 라이브러리의 안정성을 결정하는 가장 중요한 컴포넌트.

```python
@dataclass
class PTYProcess:
    pid: int
    master_fd: int
    pgid: int           # = pid (세션 리더)
    task_id: str
    started_at: float
```

**구현 포인트:**

1. `os.fork()` 후 자식에서 `os.setsid()` → 새 세션 리더
2. 자식: `slave_fd` → stdin/stdout/stderr, `os.execvpe(cmd, env)`
3. 부모: `slave_fd` close, `master_fd` non-blocking 설정
4. 3단계 종료:
   - `os.killpg(pgid, SIGHUP)` → 프로세스 그룹 전체
   - 5초 대기 → `os.kill(pid, SIGTERM)` → 개별
   - 5초 대기 → `os.killpg(pgid, SIGKILL)` → 강제
5. `os.waitpid(pid, WNOHANG)` + `os.close(master_fd)` → 좀비/리소스 정리

**테스트:**
- 자식 + 손자(fork 2단계) 모두 SIGHUP으로 정리되는가
- master_fd close 후 리소스 누수 없는가
- is_alive() 정확한가

### 7.2 PTY Executor (S1-5)

PTYProcess를 생성하고, asyncio 이벤트 루프와 통합하여 출력을 읽는다.

```python
class ClaudeCodeExecutor:
    async def execute(self, task, config, on_chunk) -> TaskResult: ...
    async def cancel(self, task_id) -> bool: ...
    async def cleanup_all(self) -> int: ...
```

**구현 포인트:**

1. `pty.openpty()` + 터미널 크기 설정 (rows=24, cols=200)
2. `os.fork()` → 자식: `setsid + dup2 + execvpe`
3. 부모: `loop.add_reader(master_fd, _on_readable)`
4. `_on_readable`:
   - `os.read(master_fd, 4096)` → `LineBuffer.feed()`
   - 완성된 줄 → `parse_stream_json_line()` → text/cost 분류
   - text → `on_chunk()` 콜백
5. 이중 타임아웃: deadline(전체) + idle_timeout(무응답 30s)
6. `OSError(EIO)` → 정상 종료 신호

**주의사항:**
- `--output-format stream-json`이 PTY에서도 깨끗한 JSON을 출력하는지 검증 필요
- ANSI escape 코드가 섞이면 strip 로직 추가
- asyncio 이벤트 루프 내 `os.fork()` 안정성 — 필요 시 전용 스레드에서 fork

### 7.3 RedisBroker (S1-7)

```
{ns}:queue:{name}            Sorted Set (score = priority * 1e12 + ts)
{ns}:queue:{name}.delayed    Sorted Set (score = delay_until)
{ns}:queue:{name}.active     Set (처리 중 task_id)
{ns}:queue:{name}.dlq        List (Dead Letter Queue)
{ns}:task:{id}               Hash (JSON)
{ns}:stream:{id}             Redis Stream (청크)
{ns}:batch:{id}              Set (task_id 목록)
{ns}:workers                 Hash (worker_id → heartbeat)
{ns}:cost:total              Float (INCRBYFLOAT)
{ns}:cost:worker:{id}        Float
{ns}:cost:daily:{date}       Float
```

**Lua 스크립트 6개:**
- `enqueue.lua` — ZADD + HSET (atomic)
- `dequeue.lua` — ZPOPMIN + SADD active (atomic)
- `ack.lua` — SREM active + EXPIRE task
- `nack.lua` — SREM active + RPUSH dlq
- `requeue.lua` — SREM active + ZADD (셧다운 시)
- `maintenance.lua` — 좀비 워커 감지 + active task requeue

### 7.4 ClaudeWorker (S1-8)

```
ClaudeWorker
  ├─ DequeueLoop      × 1  — 라운드로빈 폴링 + delayed 체크
  ├─ ProcessorLoop     × N  — PTY Executor 호출 + ack/nack
  ├─ HeartbeatLoop     × 1  — broker.heartbeat()
  └─ SignalHandler          — SIGTERM/SIGINT → graceful shutdown
```

**구현 포인트:**
- `asyncio.Semaphore(concurrency)`로 동시 실행 제한
- `_merge_config(task)`: `ClaudeConfig.model_copy(update={})` 사용. 화이트리스트 방식 — `work_dir`, `claude_bin` 오버라이드 불가
- `_process_task` 흐름:
  1. `_merge_config(task)` → 실행 설정 생성
  2. **before 체인** (순차): 각 MW의 `before_process(broker, task)` 호출. 예외 발생 시 체인 즉시 중단 (Dramatiq 방식)
  3. `executor.execute(task, config, on_chunk)` → TaskResult
  4. **after 체인** (역순 스택): 모든 MW의 `after_process(broker, task, result)` 호출. **예외가 발생해도 나머지 MW의 after는 반드시 호출**
  5. ack (성공) / nack (실패)
  6. 실패 시 `task.exception_type`에 예외 클래스명 기록, `task.status` 업데이트
- 셧다운: `_running=False` → 실행 중 대기 → 미처리 requeue → broker.close()

### 7.5 CostMiddleware (S2-5)

3단계 비용 제어:

```
1. Worker 단위 → worker_budget_usd   → 워커 누적 한도 (메모리)
2. 전체 단위   → global_budget_usd   → namespace 한도 (Redis INCRBYFLOAT)
3. API billing → BillingError(402)   → 알림 + 워커 중단 권고
```

- `before_process(broker, task)`: 한도 초과 시 예외 발생 → 체인 중단
- `after_process(broker, task, result)`: 비용 기록 + 한도 체크 + 알림 (threshold 80%)
- broker 인자를 통해 Redis 전체 비용(`INCRBYFLOAT`) 직접 조회/갱신

---

## 8. 의존성

### 빌드 시스템

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```

### 런타임 + optional

```toml
[project]
name = "open-kknaks"
requires-python = ">=3.10"
dependencies = ["pydantic>=2.7", "structlog>=24.1"]

[project.optional-dependencies]
redis = ["redis>=5.0"]
mcp = ["mcp>=1.6"]
cli = ["typer>=0.12"]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.24",
    "fakeredis[lua]>=2.21",
    "ruff>=0.8",
    "mypy>=1.13",
    "coverage>=7.0",
]

[project.scripts]
open-kknaks = "open_kknaks.cli.main:app"
```

### 버전 선정 근거

| 패키지 | 버전 | 이유 |
|---|---|---|
| `pydantic>=2.7` | v2 안정화 이후, `ConfigDict` 완성 | Task/ClaudeConfig 등 모든 모델 |
| `structlog>=24.1` | 필수 의존성 — LoggingMiddleware 기본 활성화 | 구조화 로깅 |
| `redis[asyncio]>=5.0` | asyncio 클라이언트 내장 (별도 aioredis 불필요) | RedisBroker |
| `mcp>=1.6` | streamable-http 지원 (SSE 대체) | MCP 서버 |
| `typer>=0.12` | rich 통합, Python 3.10+ 지원 안정 | CLI |
| `fakeredis[lua]>=2.21` | Lua 스크립트 실행 지원 안정화 | 6개 Lua 스크립트 테스트 |
| `pytest-asyncio>=0.24` | auto mode 안정, fixture scope 지원 | async 테스트 |
| `ruff>=0.8` | isort+black+flake8 대체 안정 | 린트 + 포맷터 |
| `mypy>=1.13` | strict mode + pydantic plugin | 타입 체크 |

### 버전 정책

- **하한만 지정** (`>=`), 상한 금지 (`<` 사용 안 함) — 유저 환경 호환성
- `fakeredis[lua]`의 `lua` extra 필수 — Lua 스크립트 6개를 fakeredis에서 테스트
- `structlog`은 필수 의존성 — LoggingMiddleware가 기본 활성화이므로 optional이 아님

### 개발 환경 (uv)

```bash
# 초기화
uv sync --all-extras

# 린트 + 타입 체크
uv run ruff check open_kknaks/ tests/
uv run ruff format --check open_kknaks/ tests/
uv run mypy open_kknaks/

# 테스트
uv run pytest tests/ -v
uv run pytest tests/ -v --cov=open_kknaks

# 빌드 + 배포
uv build                      # dist/ 에 sdist + wheel
uv publish                    # PyPI 업로드
```

---

## 9. 리스크 + 대응

| 리스크 | 대응 |
|---|---|
| PTY에서 ANSI escape 코드 섞임 | `--output-format stream-json` 검증 → 필요 시 strip 로직 |
| asyncio 루프 내 `os.fork()` 불안정 | fork를 전용 스레드에서 실행하는 옵션 준비 |
| Claude Code CLI 인터페이스 변경 | stream_parser 모듈 분리 → 교체 용이 |
| macOS vs Linux PTY 차이 | CI에서 양쪽 OS 테스트 |
| concurrency 높을 때 fd 고갈 | `ulimit -n` 체크 + 경고 로그 |

---

## 10. 전체 일정

```
S1 (1.5주) ──── S2 (1주) ──── S3 (1주)
 PTY+코어       MW+배치+DLQ    CLI+MCP+배포

Week 1  ┃ S1-1~S1-5  프로젝트 세팅 + 데이터 모델 + PTY Executor
Week 2  ┃ S1-6~S1-10 Broker + Worker + Client + 테스트
Week 3  ┃ S2-1~S2-10 미들웨어 6종 + DLQ + 배치
Week 4  ┃ S3-1~S3-7  CLI + MCP + README + PyPI 배포
```

**완료 기준:** `uv pip install open-kknaks[redis]` → `ClaudeClient.submit()` → PTY로 Claude Code 실행 → 결과 반환

---

## 11. 구현 전 설계 결정 (확정)

아래 5가지 결정은 구현 전 확정된 사항으로, 변경 시 반드시 이 문서와 CLAUDE.md를 함께 갱신한다.

### 11.1 Task 모델

| 항목 | 결정 | 비고 |
|---|---|---|
| 예외 타입 필드 | `exception_type: str \| None = None` | 실패 시 예외 클래스명 기록 (e.g. `"BillingError"`) |
| StreamEvent 타입 | 확장 안 함 — `text / cost / retry` 유지 | 작업 상태 변경은 `Task.status`로 관장 |
| datetime | `datetime.now(timezone.utc)` 사용 | naive datetime 금지 |
| model_config | `model_config = ConfigDict(use_enum_values=True)` | Enum 직렬화 시 값으로 저장 |

### 11.2 Config 병합

| 항목 | 결정 | 비고 |
|---|---|---|
| MergedConfig 클래스 | 만들지 않음 | `ClaudeConfig.model_copy(update={})` 사용 |
| 병합 위치 | `Worker._merge_config(task)` | Worker 내부에서만 수행 |
| 오버라이드 제한 | 화이트리스트 방식 | `work_dir`, `claude_bin`은 Task에서 오버라이드 불가 |

### 11.3 Middleware → Broker 접근

| 항목 | 결정 | 비고 |
|---|---|---|
| 접근 방식 | 시그널 메서드에 `broker` 인자 전달 | `before_process(broker, task)`, `after_process(broker, task, result)` |
| MW가 broker를 속성으로 보유 | 안 함 | 매 호출 시 인자로 받음 |

### 11.4 Middleware 체인 동작

| 항목 | 결정 | 비고 |
|---|---|---|
| before 체인 | 순차 실행. 예외 발생 시 즉시 중단 (sequential break) | Dramatiq 방식 |
| after 체인 | 역순 실행 (스택) | before에서 A→B→C 순이면 after는 C→B→A |
| 예외 시 after | 모든 MW의 after 호출 보장 | executor 예외/before 예외 무관 |
| RetriesMiddleware | `after_process`에서 `broker.enqueue(delay=)` 직접 호출 | 재큐잉을 MW가 직접 수행 |

### 11.5 client.result() / stream()

| 항목 | 결정 | 비고 |
|---|---|---|
| 기반 메커니즘 | `XREAD BLOCK` (Redis Stream) | 폴링 안 씀 |
| `result()` | `subscribe_chunks` → 완료 이벤트 대기 → `get_task()` 1회 호출 → TaskResult 반환 | 최종 결과만 필요한 경우 |
| `stream()` | `subscribe_chunks` → `yield` 청크 (AsyncGenerator) | 실시간 스트리밍 |
| 폴링 | 사용 안 함 | XREAD BLOCK이 이벤트 기반으로 대체 |
