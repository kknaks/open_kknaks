---
description: 테스트 규칙 — pytest + fakeredis + asyncio
globs: "tests/**/*.py"
---

# 테스트 규칙

## 구조

```
tests/
├── conftest.py              # 공통 fixture (fakeredis broker, sample tasks 등)
├── unit/                    # 단위 테스트 (외부 의존 없음)
│   ├── test_task.py
│   ├── test_config.py
│   ├── test_exceptions.py
│   ├── test_line_buffer.py
│   ├── test_stream_parser.py
│   └── test_pty_process.py
├── integration/             # 통합 테스트 (fakeredis 사용)
│   ├── test_broker.py
│   ├── test_worker.py
│   ├── test_client.py
│   ├── test_middleware.py
│   └── test_batch.py
└── e2e/                     # E2E 테스트 (실제 claude CLI — 수동 트리거)
    └── test_real_execution.py
```

## 기본 원칙

- **Redis 테스트는 fakeredis 사용** — 실제 Redis 인스턴스 불필요
- **E2E(실제 claude 실행)는 별도 마커**: `@pytest.mark.e2e` — CI에서 기본 제외
- **모든 async 테스트는 `pytest-asyncio`**: `@pytest.mark.asyncio` 데코레이터 사용
- **테스트 격리**: 각 테스트는 독립적으로 실행 가능해야 함. 순서 의존 금지
- **mock 최소화**: fakeredis로 브로커 전체를 대체. 개별 메서드 mock은 PTY syscall 등 불가피한 경우만

## fixture 패턴

```python
# conftest.py
import fakeredis.aioredis
import pytest
import pytest_asyncio

@pytest_asyncio.fixture
async def broker():
    """fakeredis 기반 RedisBroker."""
    server = fakeredis.FakeServer()
    redis = fakeredis.aioredis.FakeRedis(server=server)
    broker = RedisBroker(redis=redis, namespace="test")
    await broker.connect()
    yield broker
    await broker.close()

@pytest.fixture
def sample_task():
    """기본 Task 객체."""
    return Task(prompt="test prompt", queue="default")
```

## PTY 테스트

- PTY 테스트는 실제 `os.fork()` 사용 — mock 불가
- 자식 프로세스로 `echo`, `sleep`, `cat` 등 간단한 유닉스 명령 사용
- claude CLI 직접 호출은 E2E에서만
- 좀비/고아 프로세스 검증: 테스트 후 `os.waitpid` 확인
- fd 누수 검증: 테스트 전후 `/proc/self/fd` 또는 `os.listdir('/dev/fd')` 비교

## 검증 포인트 (Sprint 1)

PTY 관련:
- [ ] PTYProcess: 자식 + 손자 프로세스가 SIGHUP으로 모두 정리되는가
- [ ] PTYProcess: `is_alive()` 정확한가
- [ ] PTYProcess: `master_fd` close 후 리소스 누수 없는가
- [ ] Executor: `idle_timeout`으로 행 감지되는가
- [ ] Executor: `--output-format stream-json` 출력이 PTY에서도 깨끗한 JSON인가

파싱 관련:
- [ ] StreamParser: text/cost/retry 3가지 타입 정확히 분류
- [ ] StreamParser: `billing_error(402)` → `BillingError` 예외
- [ ] StreamParser: `rate_limit(429)` → retry 이벤트

브로커 관련:
- [ ] RedisBroker: enqueue → dequeue → ack 사이클
- [ ] RedisBroker: priority 순서 (HIGH=1 < NORMAL=5 < LOW=9)
- [ ] RedisBroker: delayed task가 시간 경과 후 메인 큐로 이동
- [ ] RedisBroker: DLQ 이동 + retry_from_dlq

워커 관련:
- [ ] ClaudeWorker: concurrency=4에서 좀비/고아 누적 없는가
- [ ] ClaudeWorker: graceful shutdown 시 실행 중 작업 완료 대기
- [ ] ClaudeWorker: 미처리 작업 requeue

## 네이밍

- 테스트 함수: `test_{동작}_{조건}_{기대결과}` 또는 `test_{동작}` (간단한 경우)
  - 예: `test_dequeue_returns_highest_priority_first`
  - 예: `test_pty_process_cleanup_on_sighup`
- fixture: 명사형 (`broker`, `sample_task`, `running_worker`)
