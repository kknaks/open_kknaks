---
description: 아키텍처 규칙 — 설계 결정 위반 방지
globs: "open_kknaks/**/*.py"
---

# 아키텍처 규칙

## 절대 하면 안 되는 것

1. **InMemoryBroker 만들지 마라** — Redis 전용. 테스트는 fakeredis 사용
2. **API Key(`ANTHROPIC_API_KEY`) 지원하지 마라** — OAuth(`claude login`) 전용
3. **`--bare` 모드 사용하지 마라** — Claude Code의 도구 사용 기능이 핵심
4. **`max_budget_usd` CLI 플래그 쓰지 마라** — 구독 기반이라 불필요. 비용 제어는 CostMiddleware에서
5. **Windows 지원 코드 넣지 마라** — PTY는 POSIX 전용. `platform.system()` 분기 금지
6. **Pipe 방식(`subprocess.PIPE`)으로 Claude Code 실행하지 마라** — PTY만 사용

## 의존 관계 레이어 규칙

```
Layer 0: task.py, config.py, exceptions.py, worker/line_buffer.py
Layer 1: worker/pty_process.py, worker/stream_parser.py, broker/base.py
Layer 2: worker/executor.py, broker/redis.py
Layer 3: middleware/*.py, worker/worker.py
Layer 4: client.py, batch.py, __init__.py
Layer 5: cli/, mcp/
```

- **하위 레이어는 상위 레이어를 import하면 안 된다** (순환 의존 금지)
- 예: `task.py`(L0)가 `client.py`(L4)를 import하면 안 됨
- 같은 레이어 내 import은 허용하되, 순환 참조 주의

## 컴포넌트 책임

| 컴포넌트 | 하는 것 | 안 하는 것 |
|---|---|---|
| `ClaudeClient` | 큐에 Task 넣기, 상태/결과 조회 | Claude Code CLI 직접 실행 |
| `ClaudeWorker` | 큐에서 Task 꺼내 Executor에 위임 | 직접 fork/exec |
| `ClaudeCodeExecutor` | PTY 생성, fork, 출력 읽기, TaskResult 반환 | 큐 관련 로직 |
| `PTYProcess` | 단일 프로세스 라이프사이클 (시작/종료/정리) | 출력 파싱 |
| `StreamParser` | stream-json 한 줄 → text/cost/retry 분류 | PTY/프로세스 관리 |
| `RedisBroker` | Redis와 통신 (Lua 스크립트 경유) | 비즈니스 로직 |
| `Middleware` | 횡단 관심사 (로깅, 재시도, 비용, 타임아웃) | 코어 로직 변경 |

## CLI 플래그 매핑 (변경 금지)

Claude Code CLI 실행 시 항상 붙는 고정 플래그:
- `--output-format stream-json` — 파싱용
- `-p` — 비대화형 모드

이 두 플래그는 사용자가 오버라이드할 수 없다.

## 프로듀서/워커 분리 원칙

- `ClaudeClient`(프로듀서)와 `ClaudeWorker`(소비자)는 **별도 프로세스**에서 실행 가능해야 한다
- 둘 사이의 통신은 **오직 RedisBroker를 통해서만** 이뤄진다
- 프로듀서가 워커의 내부 상태에 직접 접근하는 코드 금지

## Worker 설정 병합

```
최종 실행 설정 = Worker 기본값(ClaudeConfig) ← Task 오버라이드(Task에 명시된 것만 덮어씀)
```

- `Task.model = None` → Worker의 ClaudeConfig.model 사용
- `Task.model = "opus"` → "opus"로 오버라이드
- 이 병합 로직은 `ClaudeWorker._merge_config()`에서만 수행
