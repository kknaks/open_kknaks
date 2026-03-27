# open_kknaks

Claude Code CLI를 PTY 기반으로 안정적으로 실행하는 프로듀서/워커 분리형 태스크 큐 라이브러리.

## 프로젝트 상태

- **Phase:** Sprint 1 (PTY Executor + 코어)
- **Python:** 3.10+
- **Platform:** Linux/macOS only (Windows 미지원 — PTY가 POSIX 전용)
- **빌드/배포:** uv (패키지 관리 + 빌드 + 배포)
- **설치:** `uv pip install open-kknaks[redis]` 또는 `pip install open-kknaks[redis]`

## 아키텍처 요약

```
ClaudeClient ──enqueue──> RedisBroker <──dequeue── ClaudeWorker
                               |                        |
                          Middleware                ClaudeConfig
                    (Log, Retry, Timeout,               |
                     Cost, Rate, Callback)         PTY Executor
                                                        |
                                                   os.fork()
                                                   os.setsid()
                                                   claude -p ...
```

### 핵심 설계 결정

| 결정 | 이유 |
|---|---|
| PTY (not Pipe) | 프로세스 그룹 관리, 고아 방지, 버퍼 데드락 없음, idle_timeout 행 감지 |
| 프로듀서/워커 분리 | 수평 확장, 멀티 머신 배포 |
| Redis 전용 (InMemory 없음) | 프로듀서/워커 분리 원칙에 InMemory 위배. 테스트는 fakeredis |
| OAuth 전용 | `claude login` 전용. API Key 사용 안 함. `--bare` 사용 안 함 |
| 3단계 종료 | SIGHUP → SIGTERM → SIGKILL (PTY 세션 전체 프로세스 트리 정리) |

## 패키지 구조

```
open_kknaks/
├── __init__.py              # ClaudeClient, Task, ClaudeConfig export
├── client.py                # ClaudeClient (프로듀서)
├── config.py                # ClaudeConfig
├── task.py                  # Task, TaskStatus, Priority, TaskResult, TokenUsage, StreamEvent
├── batch.py                 # BatchRunner, BatchStatus
├── exceptions.py            # BillingError, ClaudeAuthError 등
├── broker/
│   ├── base.py              # AbstractBroker
│   ├── redis.py             # RedisBroker
│   └── lua/                 # enqueue/dequeue/ack/nack/requeue/maintenance.lua
├── worker/
│   ├── worker.py            # ClaudeWorker
│   ├── executor.py          # ClaudeCodeExecutor (PTY 기반)
│   ├── pty_process.py       # PTYProcess (fork + setsid + 3단계 종료)
│   ├── line_buffer.py       # LineBuffer (바이트 -> 줄)
│   └── stream_parser.py     # stream-json 파싱 (text/cost/retry 분류)
├── middleware/
│   ├── base.py              # Middleware ABC (6개 시그널)
│   ├── logging.py
│   ├── retries.py
│   ├── timeout.py
│   ├── cost.py              # 3단계 비용 제어
│   ├── rate_limit.py
│   └── callback.py
├── mcp/
│   ├── server.py
│   └── __main__.py
└── cli/
    ├── main.py              # typer 진입점
    ├── worker_cmd.py
    ├── queue_cmd.py
    ├── dlq_cmd.py
    └── task_cmd.py
```

## 의존 관계 레이어

```
Layer 0 (의존 없음): task.py, config.py, exceptions.py, worker/line_buffer.py
Layer 1 (L0 의존): worker/pty_process.py, worker/stream_parser.py, broker/base.py
Layer 2 (L0-1 의존): worker/executor.py, broker/redis.py, broker/lua/
Layer 3 (L0-2 의존): middleware/*.py, worker/worker.py
Layer 4 (L0-3 의존): client.py, batch.py, __init__.py
Layer 5 (L0-4 의존): cli/, mcp/
```

구현 시 반드시 아래에서 위로(Layer 0 -> 5) 쌓아 올린다. 상위 레이어가 하위 의존성 없이 만들어지면 안 된다.

## 의존성

### 런타임 (필수)

| 패키지 | 버전 | 용도 |
|---|---|---|
| `pydantic` | `>=2.7` | Task, ClaudeConfig 등 데이터 모델 (v2 안정화 이후) |

### 런타임 (optional extras)

| extra | 패키지 | 버전 | 용도 |
|---|---|---|---|
| `redis` | `redis` | `>=5.0` | RedisBroker (asyncio 내장) |
| `mcp` | `mcp` | `>=1.6` | MCP 서버 (streamable-http 지원) |
| `cli` | `typer` | `>=0.12` | CLI 도구 (`open-kknaks worker` 등) |

### 개발 (dev extra)

| 패키지 | 버전 | 용도 |
|---|---|---|
| `pytest` | `>=8.0` | 테스트 러너 |
| `pytest-asyncio` | `>=0.24` | async 테스트 지원 |
| `fakeredis[lua]` | `>=2.21` | Redis mock (Lua 스크립트 테스트 포함) |
| `ruff` | `>=0.8` | 린트 + 포맷터 |
| `mypy` | `>=1.13` | 타입 체크 (strict) |
| `coverage` | `>=7.0` | 테스트 커버리지 |
| `structlog` | `>=24.1` | 구조화 로깅 (LoggingMiddleware) |

### pyproject.toml 기준

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

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

### 버전 정책

- 하한만 지정 (`>=`), 상한 금지 (`<` 사용 안 함) — 유저 환경 호환성
- `fakeredis[lua]`의 `lua` extra 필수 — Lua 스크립트 6개를 fakeredis에서 테스트
- `structlog`은 필수 의존성 (LoggingMiddleware 기본 활성화)

## 개발 환경

```bash
# 프로젝트 초기화
uv sync --all-extras          # 모든 optional deps 포함 설치
uv sync --extra dev           # dev deps만

# 린트 + 타입 체크
uv run ruff check open_kknaks/ tests/
uv run ruff format --check open_kknaks/ tests/
uv run mypy open_kknaks/

# 테스트
uv run pytest tests/ -v
uv run pytest tests/ -v --cov=open_kknaks   # 커버리지 포함

# 전체 체크
uv run ruff check && uv run ruff format --check && uv run mypy open_kknaks/ && uv run pytest tests/ -v

# 빌드 + 배포
uv build                      # dist/ 에 sdist + wheel 생성
uv publish                    # PyPI 업로드
```

## 문서 참조

| 문서 | 내용 |
|---|---|
| `docs/ARCHITECTURE_V2.md` | 상세 기술 설계 (컴포넌트별 구현 사양) |
| `docs/PRD.md` | 제품 요구사항 (API 인터페이스, Task 모델, Broker 인터페이스) |
| `docs/PLAN.md` | 구현 순서 + 스프린트 계획 |
| `docs/TEST_APP.md` | 예시 프로젝트 (Docker compose) |
| `docs/CLAUDE_CODE_ANALYSIS.md` | Claude Code CLI 조사/분석 근거 |
