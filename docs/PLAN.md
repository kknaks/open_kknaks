# open_kknaks — 프로젝트 기획 초안

## 개요

로컬/서버에 설치된 Claude Code CLI를 실행하는 **전용 태스크 큐 라이브러리**.
Dramatiq과 동일한 구조를 가지되, 워커가 수행하는 작업은 오직 **Claude Code CLI 실행**으로 고정된다.

서버/로컬에서 발생하는 이벤트(에러, Jira 이슈, 문서 비교 등)를 Claude Code에 자동으로 전달하고 결과를 받아오는 자동화 파이프라인을 쉽게 구축할 수 있게 한다.

트리거(webhook, cron 등)는 유저 프로젝트에서 직접 붙이고, 이 라이브러리는 **Claude Code 호출 + 작업 큐 관리 + 결과 반환**만 담당한다.

## Dramatiq과의 비교

| | Dramatiq | open_kknaks |
|---|---|---|
| 구조 | 범용 태스크 큐 | Claude Code 전용 태스크 큐 |
| 워커 역할 | 유저가 정의한 함수 실행 | Claude Code CLI 실행 (고정) |
| 브로커 | Redis, RabbitMQ 등 | Redis (InMemory 포함) |
| 스트리밍 | ❌ | ✅ (pty 기반 실시간) |
| MCP 서버 | ❌ | ✅ |

## 전제 조건

- 사용자 로컬/서버에 Claude Code CLI가 설치 + 로그인되어 있어야 함 (`claude` 바이너리 PATH에 있어야 함)
- Python async 환경 (asyncio)
- RedisBroker 사용 시 Redis 서버를 별도로 설치/실행해야 함 (Docker, 클라우드 등)

## 아키텍처

```
유저 코드
  ↓ runner.submit()
작업 큐 (InMemory or Redis)
  ↓
Worker (내장)
  ↓
Claude Code CLI (pty spawn)
  ↓
결과 저장 → 유저가 polling / streaming
```

## 핵심 컨셉

```python
from open_kknaks import ClaudeRunner
from open_kknaks.broker import RedisBroker

runner = ClaudeRunner(
    work_dir="/my/project",
    claude_bin="/usr/local/bin/claude",  # 생략 시 PATH 자동 탐색
    broker=RedisBroker(
        url="redis://localhost:6379",
        password="secret",
        key_prefix="myapp"
    )
)

# 단일 작업
task_id = await runner.submit("이 에러 분석해줘", context=error_log)
async for chunk in runner.stream(task_id):
    print(chunk)

# 배치 작업
batch_id = await runner.batch_submit([
    {"prompt": "이슈 1 분석", "context": issue1},
    {"prompt": "이슈 2 분석", "context": issue2},
])
results = await runner.batch_wait(batch_id)
```

## 패키지 구조

```
open_kknaks/
├── runner/
│   ├── base.py        # AbstractRunner 인터페이스
│   └── claude.py      # ClaudeCodeRunner — pty spawn 기반 실행
├── broker/
│   ├── base.py        # AbstractBroker 인터페이스
│   ├── memory.py      # InMemoryBroker (기본값, Redis 없을 때)
│   └── redis.py       # RedisBroker
├── worker.py          # 내장 Worker (큐에서 작업 꺼내 Claude Code 실행)
├── task.py            # Task 모델, TaskStatus enum
├── batch.py           # BatchRunner
├── mcp/
│   └── server.py      # MCP 서버 (Claude Desktop 등 연결용)
└── client.py          # ClaudeRunner (유저 메인 진입점)
```

## 설정

```python
runner = ClaudeRunner(
    work_dir="/my/project",
    claude_bin="/usr/local/bin/claude",  # 생략 시 PATH에서 자동 탐색
    broker=RedisBroker(
        url="redis://localhost:6379",
        password="secret",
        key_prefix="myapp"   # 여러 프로젝트가 같은 Redis 쓸 때 충돌 방지
    )
)
```

## 브로커 선택 가이드

| 브로커 | Redis 필요 | 멀티 프로세스 | 용도 |
|---|---|---|---|
| `InMemoryBroker` | ❌ | ❌ | 개발/테스트, 단일 프로세스 |
| `RedisBroker` | ✅ (직접 설치) | ✅ | 운영, 멀티 서버/프로세스 |

> **주의:** RedisBroker는 라이브러리가 Redis를 포함하는 게 아님. 유저가 Redis 서버를 직접 띄워야 함.

## 브로커 추상화

범용성을 위해 브로커를 인터페이스로 추상화. 유저가 직접 구현도 가능.

```python
from open_kknaks.broker import AbstractBroker

class MyCustomBroker(AbstractBroker):
    async def enqueue(self, task): ...
    async def get_status(self, task_id): ...
    async def publish_chunk(self, task_id, chunk): ...
    async def subscribe_chunks(self, task_id): ...
```

## 기능 목록

### 단일 작업
| 메서드 | 설명 |
|---|---|
| `submit(prompt, context, priority, delay_seconds)` | 작업 등록 → task_id 반환 |
| `stream(task_id)` | 실시간 청크 스트리밍 (async generator) |
| `status(task_id)` | 작업 상태 조회 (pending/running/done/failed/cancelled) |
| `result(task_id)` | 완료된 작업 결과 조회 |
| `cancel(task_id)` | 실행 중 작업 취소 |
| `retry(task_id)` | 실패 작업 재시도 |

### 배치 작업
| 메서드 | 설명 |
|---|---|
| `batch_submit(tasks, mode)` | 여러 작업 등록 → batch_id 반환 (mode: parallel/sequential) |
| `batch_status(batch_id)` | 배치 전체 상태 조회 |
| `batch_stream(batch_id)` | 배치 내 모든 작업 스트리밍 |
| `batch_wait(batch_id)` | 배치 완료까지 대기 → 결과 리스트 반환 |
| `batch_cancel(batch_id)` | 배치 전체 취소 |

### 기타
- `priority` — 작업 우선순위 (high / normal / low)
- `delay_seconds` — 지연 실행
- `max_retries` — 자동 재시도 횟수
- `timeout` — 작업별 타임아웃 (기본 10분)

## Task 모델

```python
class Task:
    id: str
    prompt: str
    context: str | None
    status: TaskStatus  # pending / running / done / failed / cancelled
    result: str | None
    error: str | None
    priority: str       # high / normal / low
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    retry_count: int
    max_retries: int
```

## MCP 서버 지원

이 라이브러리 자체를 MCP 서버로 노출. Claude Desktop 등 MCP 클라이언트에서 직접 호출 가능.

```
MCP Client (Claude Desktop 등)
    ↓ MCP 프로토콜
open_kknaks MCP Server
    ↓
ClaudeRunner (Claude Code CLI 호출)
```

### MCP 서버 실행

```python
from open_kknaks.mcp import MCPServer

server = MCPServer(
    runner=ClaudeRunner(
        work_dir="/my/project",
        broker=RedisBroker("redis://localhost:6379")
    )
)

server.run(host="0.0.0.0", port=3000)
```

### 노출 MCP Tool 목록

| Tool | 설명 |
|---|---|
| `submit_task` | 작업 등록 → task_id 반환 |
| `get_status` | 작업 상태 조회 |
| `get_result` | 완료된 작업 결과 조회 |
| `cancel_task` | 작업 취소 |
| `submit_batch` | 배치 작업 등록 → batch_id 반환 |
| `get_batch_status` | 배치 상태 조회 |
| `get_batch_result` | 배치 결과 조회 |

## 유즈케이스 예시

- Jira 이슈 등록 → Claude Code가 코드베이스 분석 → 원인 리포트
- Confluence 문서 + GitHub 커밋 비교 → 진행률 분석
- 서버 에러 로그 → Claude Code 분석 → Slack/Discord 알림
- CI 실패 → 원인 분석 → PR 코멘트

## PyPI 배포 정보

- 패키지명: `open-kknaks`
- 진입점: `from open_kknaks import ClaudeRunner`
- Python: 3.10+
- 의존성: `redis[asyncio]` (optional), `asyncio`
