# open-kknaks

Claude/Codex provider 기반 headless agent 태스크 큐 라이브러리.

프로듀서(`AgentClient`)가 Redis에 태스크를 넣으면, 워커(`ClaudeWorker`)가 provider adapter를 통해 Claude Code CLI 또는 Codex CLI를 실행하고 결과를 돌려줍니다.

```
AgentClient --enqueue--> Redis <--dequeue-- ClaudeWorker
                                                 |
                                         Provider Adapter
                                                 |
                                      claude -p / codex exec
```

## 설치

```bash
pip install open-kknaks
```

모든 의존성(redis, mcp, typer)이 포함됩니다.

## 라이브러리 사용법

### 태스크 제출 (AgentClient)

```python
import asyncio
from open_kknaks import AgentClient, RedisBroker

async def main():
    broker = RedisBroker(url="redis://localhost:6379", namespace="myapp")
    await broker.connect()
    client = AgentClient(broker=broker)

    # Claude가 기본 provider다.
    task_id = await client.submit("Explain Python decorators in 3 sentences.")
    print(f"Submitted: {task_id}")

    # Codex provider로 실행할 수도 있다.
    codex_task_id = await client.submit(
        "Summarize this repository structure.",
        provider="codex",
        model="gpt-5",
        options={"cwd": "/path/to/repo", "timeout_sec": 300},
        provider_options={"sandbox": "workspace-write", "color": "never"},
    )
    print(f"Codex submitted: {codex_task_id}")

    # 결과 대기
    task = await client.result(task_id, timeout=120)
    print(task.result)

    await broker.close()

asyncio.run(main())
```

#### submit 주요 파라미터

| 파라미터 | 설명 |
|---|---|
| `prompt` | Claude에게 보낼 프롬프트 |
| `context` | 프롬프트 앞에 붙는 추가 컨텍스트 |
| `queue` | 큐 이름 (기본: `"default"`) |
| `priority` | `Priority.HIGH(1)`, `NORMAL(5)`, `LOW(9)` |
| `provider` | 실행 provider. 기본 `"claude"`, 지원값 `"claude"`, `"codex"` |
| `model` | provider 모델 오버라이드 |
| `options` | 공통 실행 옵션. 예: `cwd`, `timeout_sec`, `resume` |
| `provider_options` | provider별 CLI 옵션. 예: Claude `max_turns`, Codex `sandbox` |
| `max_retries` | 실패 시 재시도 횟수 |
| `delay_seconds` | 지연 실행 (초) |
| `metadata` | 사용자 정의 메타데이터 |

#### 실시간 스트리밍

```python
task_id = await client.submit("Write a FastAPI TODO app.")

# 전체 이벤트 수신
async for event in client.stream(task_id):
    if event.type == "text":
        print(event.text, end="", flush=True)
    elif event.type == "tool_use":
        print(f"\n[tool] {event.tool_name}: {event.tool_input}")
    elif event.type == "tool_result":
        print(f"\n[result] {event.tool_result[:100]}")
    elif event.type == "thinking":
        print(f"\n[thinking] {event.text[:80]}...")
    elif event.type == "progress":
        print(f"\n[progress] tokens={event.total_tokens} tools={event.tool_uses} | {event.description}")
    elif event.type == "init":
        print(f"\n[init] model={event.model} session={event.session_id}")
    elif event.type == "cost":
        print(f"\n[cost] ${event.cost_usd}")
    elif event.type == "retry":
        print(f"\n[retry] {event.retry_info}")
```

#### 이벤트 필터링

필요한 이벤트 타입만 골라 받을 수 있습니다:

```python
# 텍스트와 진행 상황만
async for event in client.stream(task_id, event_types={"text", "progress"}):
    if event.type == "text":
        print(event.text, end="", flush=True)
    elif event.type == "progress":
        print(f"\n  [{event.total_tokens} tokens]", end="")

# 도구 사용 모니터링
async for event in client.stream(task_id, event_types={"tool_use", "tool_result"}):
    if event.type == "tool_use":
        print(f"  -> {event.tool_name}({event.tool_input})")
    elif event.type == "tool_result":
        err = " [ERROR]" if event.tool_is_error else ""
        print(f"  <- {event.tool_result[:100]}{err}")

# 세션 컴팩션 판단 (토큰 누적량 모니터링)
async for event in client.stream(task_id, event_types={"progress"}):
    if event.total_tokens and event.total_tokens > 100_000:
        print("Context too large — consider starting a new session")
        await client.cancel(task_id)
        break
```

#### StreamEvent 타입

| 타입 | 설명 | 주요 필드 |
|------|------|-----------|
| `text` | 어시스턴트 텍스트 출력 | `text` |
| `tool_use` | 도구 호출 | `tool_name`, `tool_input` |
| `tool_result` | 도구 실행 결과 | `tool_result`, `tool_is_error` |
| `thinking` | 사고 과정 (extended thinking) | `text` |
| `init` | 세션 초기화 | `model`, `session_id` |
| `progress` | 진행 상황 (매 도구 실행마다) | `total_tokens`, `tool_uses`, `duration_ms`, `description`, `last_tool_name` |
| `cost` | 최종 비용/토큰 사용량 | `cost_usd` |
| `retry` | API 재시도 정보 | `retry_info` |

#### 배치 실행

```python
from open_kknaks import BatchRunner

runner = BatchRunner(broker=broker)
batch_id, task_ids = await runner.submit_batch([
    {"prompt": "Explain Python GIL"},
    {"prompt": "Explain asyncio event loop"},
    {"prompt": "Compare threading vs multiprocessing"},
])

results = await runner.wait_batch(task_ids, timeout=300)
for r in results:
    print(f"[{r.status}] {r.result[:100]}")
```

### 워커 실행 (ClaudeWorker)

#### CLI로 실행

```bash
open-kknaks worker run \
    --broker redis://localhost:6379 \
    --namespace myapp \
    --queues default,analysis \
    --work-dir /path/to/project \
    --concurrency 4
```

#### Docker에서 실행

```dockerfile
FROM python:3.12-slim
RUN pip install --no-cache-dir open-kknaks

CMD ["open-kknaks", "worker", "run", \
     "--broker", "redis://redis:6379", \
     "--work-dir", "/workspace", \
     "--model", "claude-sonnet-4-5-20250514", \
     "--concurrency", "4", \
     "--queues", "default"]
```

#### Python으로 실행

```python
import asyncio
from open_kknaks import RedisBroker, ClaudeConfig, ClaudeWorker
from open_kknaks.middleware.logging import LoggingMiddleware
from open_kknaks.middleware.retries import RetriesMiddleware
from open_kknaks.middleware.cost import CostMiddleware

async def main():
    broker = RedisBroker(url="redis://localhost:6379", namespace="myapp")
    await broker.connect()

    worker = ClaudeWorker(
        broker=broker,
        config=ClaudeConfig(work_dir="/path/to/project"),
        queues=["default", "analysis"],
        concurrency=4,
        middleware=[
            LoggingMiddleware(),
            RetriesMiddleware(max_retries=2),
            CostMiddleware(worker_budget_usd=5.0),
        ],
    )

    await worker.run()

asyncio.run(main())
```

#### 워커 옵션

| 옵션 | 설명 |
|---|---|
| `queues` | 구독할 큐 목록 |
| `concurrency` | 동시 실행 태스크 수 |
| `work_dir` | Claude Code 작업 디렉토리 |
| `model` | 기본 모델 |
| `shutdown_timeout` | 종료 시 실행 중 태스크 대기 시간 (초) |

#### 미들웨어

| 미들웨어 | 설명 |
|---|---|
| `LoggingMiddleware` | 태스크 시작/완료/실패 구조화 로깅 |
| `RetriesMiddleware` | 실패 시 자동 재시도 |
| `TimeoutMiddleware` | 태스크 타임아웃 |
| `CostMiddleware` | 워커/글로벌 예산 제한 |
| `RateLimitMiddleware` | 요청 속도 제한 |
| `CallbackMiddleware` | 완료/실패 시 콜백 호출 |

### MCP 서버

Claude Code에서 도구 스키마를 조회할 수 있는 MCP 서버입니다.

`.mcp.json`:

```json
{
  "mcpServers": {
    "open-kknaks": {
      "command": "uvx",
      "args": ["--from", "open-kknaks", "open-kknaks-mcp"]
    }
  }
}
```

13개 도구 스키마를 제공합니다: `submit_task`, `get_task`, `get_status`, `get_result`, `cancel_task`, `submit_batch`, `get_batch_status`, `wait_batch`, `queue_size`, `list_dlq`, `retry_from_dlq`, `purge_dlq`, `get_cost`.

### CLI 명령어

```bash
# 워커
open-kknaks worker run --broker redis://localhost:6379 --queues default

# 태스크
open-kknaks task status <task-id>
open-kknaks task result <task-id> --wait
open-kknaks task cancel <task-id>

# 큐
open-kknaks queue size <queue-name>

# DLQ (Dead Letter Queue)
open-kknaks dlq list <queue-name>
open-kknaks dlq retry <queue-name> --task-id <id>
open-kknaks dlq purge <queue-name>
```

## Docker 데모 실행

`examples/` 디렉토리에 Docker Compose 기반 데모와 provider E2E 시나리오가 포함되어 있습니다.
PyPI 설치 후 동작을 확인하거나 처음 연동을 검증할 때는 GitHub repository를 clone한 뒤 이 흐름을 먼저 권장합니다.

### 구성

- **Redis** - 태스크 큐 브로커
- **Worker** - Claude/Codex provider adapter를 실행하는 워커
- **App** - FastAPI 웹 UI (provider 선택, 태스크 제출, 스트리밍, 시나리오)

### 사전 준비

- Docker, Docker Compose
- Node.js/npm (Claude Code CLI 설치용)
- Claude provider를 쓰려면 호스트에서 `claude setup-token`을 실행해 Claude Code setup token을 준비합니다.
- Codex provider를 쓰려면 호스트에서 Codex CLI 로그인을 완료해 `~/.codex`가 준비되어 있어야 합니다.

> `CLAUDE_CODE_OAUTH_TOKEN`에는 Anthropic Console API key가 아니라 `claude setup-token` 출력값을 넣어야 합니다.

### 실행

```bash
git clone https://github.com/kknaks/open_kknaks.git
cd open_kknaks
cd examples/
bash setup.sh
```

`setup.sh`가 다음을 자동으로 처리합니다:

1. Claude Code setup token 입력
2. Linux용 Node.js 다운로드 (Docker 컨테이너용)
3. Claude Code CLI 설치 (npm)
4. Codex CLI를 Docker/Linux용으로 `examples/.codex-tools/`에 설치
5. 호스트 `~/.codex` 인증/config를 `examples/.codex-home/`으로 복사
6. `.env` 생성 + Docker Compose 실행

완료되면:

- **Web UI**: http://localhost:8000
- **Swagger**: http://localhost:8000/docs
- **Redis**: localhost:6379

웹 UI에서 provider를 `claude` 또는 `codex`로 선택하고, 필요하면 `model`, `cwd`, `timeout`, `provider_options` JSON을 지정합니다.
Codex 선택 시 UI는 기본 provider option으로 `{"sandbox":"workspace-write","color":"never","skip_git_repo_check":true}`를 적용합니다.

### 수동 Docker 실행

이미 `setup.sh`를 한 번 실행해서 `.env`, `.claude-tools/`, `.codex-tools/`, `.codex-home/`이 준비되어 있다면 다음 명령으로 다시 띄울 수 있습니다.

```bash
cd examples/
docker compose up -d --build
```

로컬 checkout의 최신 코드를 Docker 데모에 바로 반영하려면 local override를 함께 사용합니다.

```bash
cd examples/
docker compose -f docker-compose.yml -f docker-compose.local.yml up -d --build
```

토큰이나 인증 파일을 바꾼 뒤에는 worker를 재생성해야 합니다.

```bash
docker compose -f docker-compose.yml -f docker-compose.local.yml up -d --force-recreate worker
```

상태와 로그는 다음으로 확인합니다.

```bash
docker compose ps
docker compose logs --tail=100 worker
```

worker 로그에서 `provider.check`가 `claude=ok`, `codex=ok`로 표시되면 CLI 바이너리 감지는 완료된 상태입니다.
실제 인증 실패는 task 실행 시 `401 Invalid bearer token` 같은 provider 에러로 반환되며, 웹 UI 출력 영역에도 표시됩니다.

### 수동 E2E 절차

Redis와 worker가 실행 중인 상태에서 provider별로 실제 queue -> worker -> provider -> result 흐름을 확인합니다.

```bash
# Claude 기본 provider
python examples/scenarios/01_basic.py

# Codex provider
OPEN_KKNAKS_PROVIDER=codex \
OPEN_KKNAKS_MODEL=gpt-5 \
OPEN_KKNAKS_CWD=/path/to/open_kknaks \
python examples/scenarios/01_basic.py

# Codex 세션 이어가기
OPEN_KKNAKS_PROVIDER=codex \
OPEN_KKNAKS_CWD=/path/to/open_kknaks \
python examples/scenarios/05_session.py
```

추가 옵션은 환경변수로 줄 수 있습니다.

```bash
OPEN_KKNAKS_TIMEOUT_SEC=300
OPEN_KKNAKS_PROVIDER_OPTIONS='{"sandbox":"workspace-write","color":"never"}'
OPEN_KKNAKS_CODEX_SANDBOX=workspace-write
```

### 시나리오 스크립트

Docker 없이 개별 시나리오를 직접 실행할 수도 있습니다. Redis + Worker가 실행 중이어야 하며, provider CLI 인증은 실행 환경에서 완료되어 있어야 합니다.

```bash
pip install -r examples/requirements.txt

python examples/scenarios/01_basic.py        # 기본 제출 -> 결과
python examples/scenarios/02_streaming.py    # 실시간 스트리밍
python examples/scenarios/03_batch.py        # 배치 실행 (3개 병렬)
python examples/scenarios/04_priority.py     # 우선순위 + 지연 실행
python examples/scenarios/05_session.py      # 세션 이어서 대화
python examples/scenarios/06_multi_queue.py  # 멀티 큐 라우팅
python examples/scenarios/07_code_review.py  # 코드 분석
```

## 요구 사항

- Python 3.10+
- Redis
- Claude Code CLI (`claude login` 완료)
- Linux / macOS (PTY는 POSIX 전용, Windows 미지원)

## 라이선스

MIT
