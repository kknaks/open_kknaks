---
description: Python 코딩 컨벤션 — open_kknaks 프로젝트 전체에 적용
globs: "**/*.py"
---

# Python 코딩 컨벤션

## 기본 도구

- **포맷터/린터:** ruff (flake8/isort/black 대체)
- **타입 체크:** mypy (strict mode)
- **테스트:** pytest + pytest-asyncio
- **모델:** pydantic v2

## 스타일

- Python 3.10+ 문법 사용 (`X | Y` union, `match/case` 등)
- `from __future__ import annotations` 사용하지 않음 — 런타임 타입 필요 (pydantic)
- 문자열: 큰따옴표 `"` 사용 (ruff default)
- 줄 길이: 120자
- import 순서: stdlib → third-party → local (ruff isort가 자동 정리)

## 타입 힌트

- 모든 public 함수/메서드에 타입 힌트 필수
- `Any` 사용 최소화 — 구체적 타입 우선
- `Optional[X]` 대신 `X | None` 사용
- 콜백: `Callable[[arg_types], return_type]` 또는 `Protocol`
- async 콜백: `Callable[[StreamEvent], Awaitable[None]]`

## async 패턴

- I/O 바운드 작업은 모두 async
- `asyncio.Semaphore`로 동시성 제어 (스레드 락 아님)
- `asyncio.TaskGroup` 또는 `asyncio.gather`로 병렬 실행
- `loop.add_reader(fd, callback)`로 PTY fd 읽기 — 스레드 풀 사용 안 함
- `asyncio.Event`로 상태 변경 알림
- 취소: `asyncio.CancelledError` 전파 허용, 리소스 정리 후 re-raise

## PTY/프로세스 관련

- `os.fork()` 후 자식에서 반드시 `os.setsid()` 호출
- fd 누수 방지: `try/finally`로 `os.close(master_fd)` 보장
- 시그널 처리: `os.killpg(pgid, signal)` — 개별 pid가 아닌 프로세스 그룹 단위
- `OSError(errno.EIO)` = PTY 정상 종료 신호 — 예외 아님
- `os.waitpid(pid, WNOHANG)` 으로 좀비 수거

## pydantic 모델

- `BaseModel` 상속, `model_config = ConfigDict(...)` 사용
- Enum은 `str, Enum` 또는 `int, Enum` 다중 상속 (JSON 직렬화 호환)
- `Field(default_factory=...)` 사용 — mutable default 금지
- 직렬화: `model_dump()` / `model_validate()` (v1 메서드 사용 안 함)

## Redis / Lua

- `redis[asyncio]>=5.0` 사용 (동기 redis 사용 안 함)
- Redis 키 네이밍: `{namespace}:{resource}:{id}` 패턴
- 원자적 연산은 Lua 스크립트로 — 파이프라인 경쟁 조건 방지
- 연결 풀: `redis.asyncio.ConnectionPool` — 매번 연결 생성 안 함
- 테스트: `fakeredis[lua]>=2.21` — Lua 스크립트가 fakeredis에서도 동작해야 함

## 에러 처리

- 커스텀 예외는 `exceptions.py`에 정의, 계층 구조 유지
- `BillingError(402)`, `ClaudeAuthError(401)` — 재시도 불가 예외
- `RateLimitError(429)` — CLI가 자동 재시도하므로 라이브러리에서 별도 처리 안 함
- bare `except:` 금지 — 최소 `except Exception:`
- 로깅은 `structlog>=24.1` 사용 (LoggingMiddleware 기본 활성화)

## 네이밍

- 모듈/파일: snake_case (`pty_process.py`)
- 클래스: PascalCase (`ClaudeWorker`)
- 상수: UPPER_SNAKE_CASE (`DEFAULT_TIMEOUT`)
- private: `_` prefix (`_active_processes`)
- 테스트: `test_` prefix, 파일명은 `test_{module}.py`
