# open_kknaks — 예시 프로젝트 (TEST_APP)

> git clone 시 포함. PyPI 배포에는 미포함.
> `docker compose up` 한 방으로 Redis + Worker + 예시 앱이 뜨는 체험 환경.

---

## 1. 목적

| 대상 | 목적 |
|---|---|
| 라이브러리 평가자 | clone → docker compose up → 즉시 체험 |
| 기여자 | 개발 환경 원클릭 셋업 |
| 유저 | 실제 프로젝트에 통합할 때 참고할 레퍼런스 코드 |

---

## 2. 디렉토리 구조

```
open_kknaks/                     # 라이브러리 소스 (PyPI 배포 대상)
tests/                           # 유닛/통합 테스트
examples/                        # 예시 프로젝트 (PyPI 미포함, git 전용)
├── docker-compose.yml           # Redis + Worker + 예시 앱 한 번에 실행
├── setup.sh                     # Claude CLI 경로 자동 탐색 → .env 생성
├── .env.example                 # 환경변수 템플릿
├── Dockerfile.worker            # Worker 이미지
├── Dockerfile.app               # 예시 앱 이미지
│
├── worker/                      # 워커 설정
│   ├── run.py                   # 워커 진입점
│   └── claude_config.py         # ClaudeConfig 설정
│
├── app/                         # 예시 FastAPI 앱 (프로듀서)
│   ├── main.py                  # FastAPI 서버
│   ├── routes/
│   │   ├── submit.py            # POST /submit — 작업 등록
│   │   ├── status.py            # GET /status/{task_id} — 상태 조회
│   │   ├── result.py            # GET /result/{task_id} — 결과 조회
│   │   └── stream.py            # GET /stream/{task_id} — SSE 스트리밍
│   └── templates/
│       └── index.html           # 간단한 웹 UI (프롬프트 입력 + 실시간 출력)
│
└── scenarios/                   # 시나리오별 스크립트
    ├── 01_basic.py              # 단일 작업 submit → result
    ├── 02_streaming.py          # 실시간 스트리밍
    ├── 03_batch.py              # 배치 작업
    ├── 04_priority.py           # 우선순위 + 지연 실행
    ├── 05_session.py            # 세션 이어가기
    └── 06_multi_queue.py        # 멀티 큐 + 워커 라우팅
```

---

## 3. docker-compose.yml

```yaml
services:
  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
    volumes:
      - redis_data:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      retries: 3

  worker:
    build:
      context: .
      dockerfile: Dockerfile.worker
    depends_on:
      redis:
        condition: service_healthy
    environment:
      - REDIS_URL=redis://redis:6379
      - NAMESPACE=example
      - QUEUES=default,analysis,review
      - CONCURRENCY=2
      - PATH=/host-node/bin:/usr/local/bin:/usr/bin:/bin
    volumes:
      - ${HOME}/.claude:/root/.claude:ro       # OAuth 크리덴셜
      - ${CLAUDE_DIR}:/host-node:ro            # Node.js prefix (bin/ + lib/node_modules)
      - ./workspace:/workspace
    working_dir: /workspace

  app:
    build:
      context: .
      dockerfile: Dockerfile.app
    depends_on:
      redis:
        condition: service_healthy
    ports:
      - "8000:8000"
    environment:
      - REDIS_URL=redis://redis:6379
      - NAMESPACE=example

volumes:
  redis_data:
```

---

## 4. setup.sh — Claude CLI 경로 자동 탐색

```bash
#!/bin/bash
set -e

# Claude CLI 바이너리 찾기
CLAUDE_BIN=$(which claude 2>/dev/null)
if [ -z "$CLAUDE_BIN" ]; then
    echo "ERROR: claude CLI를 찾을 수 없습니다."
    echo "  설치: https://claude.ai/download"
    echo "  설치 후: claude login"
    exit 1
fi

# Node.js prefix 찾기 (bin/ + lib/node_modules 포함하는 상위 디렉토리)
#
# claude 바이너리 구조:
#   bin/claude → ../lib/node_modules/@anthropic-ai/claude-code/cli.js (심볼릭 링크)
#   bin/node   (Node.js 런타임)
#   lib/node_modules/@anthropic-ai/claude-code/ (72MB 패키지)
#
# bin/만 마운트하면 심볼릭 링크가 깨지므로,
# bin/ + lib/을 포함하는 prefix 디렉토리 전체를 마운트해야 한다.
CLAUDE_DIR=$(dirname "$(dirname "$(realpath "$CLAUDE_BIN")")")

# 로그인 상태 확인
if ! claude auth status &>/dev/null; then
    echo "WARNING: Claude Code 로그인이 필요합니다."
    echo "  실행: claude login"
fi

# .env 생성
cat > .env << EOF
REDIS_URL=redis://redis:6379
NAMESPACE=example
QUEUES=default,analysis,review
CONCURRENCY=2
CLAUDE_DIR=${CLAUDE_DIR}
EOF

echo "=== setup 완료 ==="
echo "Claude CLI: ${CLAUDE_BIN}"
echo "마운트 경로: ${CLAUDE_DIR} → /host-claude"
echo ""
echo "실행: docker compose up -d"
```

**동작 예시:**

```bash
$ ./setup.sh

# nvm 유저
# Claude CLI: /Users/kknaks/.nvm/versions/node/v20.20.0/bin/claude
# Node prefix: /Users/kknaks/.nvm/versions/node/v20.20.0
# → /host-node/bin/claude, /host-node/bin/node, /host-node/lib/node_modules/...

# homebrew 유저
# Claude CLI: /opt/homebrew/bin/claude
# Node prefix: /opt/homebrew
# → /host-node/bin/claude, /host-node/bin/node, /host-node/lib/node_modules/...

# 시스템 npm 유저
# Claude CLI: /usr/local/bin/claude
# Node prefix: /usr/local
# → /host-node/bin/claude, /host-node/bin/node, /host-node/lib/node_modules/...
```

어떤 방식으로 설치했든 `setup.sh`가 Node.js prefix를 찾아서 `.env`에 넣고,
docker-compose가 prefix 전체를 `/host-node`로 바인드 마운트합니다.
컨테이너 안에서 `claude` 실행 시 `/host-node/bin/node`가 런타임으로 사용됩니다.

---

## 5. Dockerfile

### Dockerfile.worker

```dockerfile
FROM python:3.12-slim

# Claude Code CLI는 호스트에서 바인드 마운트 (/host-claude)
# Node.js, claude 바이너리 설치 불필요 → 이미지 가벼움

# 라이브러리 설치 (로컬 소스)
WORKDIR /lib
COPY ../../open_kknaks ./open_kknaks
COPY ../../pyproject.toml .
RUN pip install -e ".[redis]"

# 워커 코드
WORKDIR /app
COPY worker/ .

CMD ["python", "run.py"]
```

### Dockerfile.app

```dockerfile
FROM python:3.12-slim

WORKDIR /lib
COPY ../../open_kknaks ./open_kknaks
COPY ../../pyproject.toml .
RUN pip install -e ".[redis]" && pip install fastapi uvicorn jinja2 sse-starlette

WORKDIR /app
COPY app/ .

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

---

## 5. 워커 코드

### worker/run.py

```python
"""예시 워커 — docker compose로 실행."""
import asyncio
import os

from open_kknaks.worker import ClaudeWorker
from open_kknaks.broker import RedisBroker
from open_kknaks.config import ClaudeConfig
from open_kknaks.middleware import (
    LoggingMiddleware,
    RetriesMiddleware,
    TimeoutMiddleware,
    CostMiddleware,
)


async def main():
    broker = RedisBroker(
        url=os.environ.get("REDIS_URL", "redis://localhost:6379"),
        namespace=os.environ.get("NAMESPACE", "example"),
    )

    worker = ClaudeWorker(
        broker=broker,
        queues=os.environ.get("QUEUES", "default").split(","),
        claude=ClaudeConfig(
            work_dir="/workspace",
            bare=True,
            permission_mode="bypassPermissions",
        ),
        concurrency=int(os.environ.get("CONCURRENCY", "2")),
        middlewares=[
            LoggingMiddleware(),
            RetriesMiddleware(max_retries=2),
            TimeoutMiddleware(),
            CostMiddleware(
                worker_budget_usd=5.0,       # 워커당 $5 한도
                global_budget_usd=20.0,       # 전체 $20 한도
            ),
        ],
    )

    print(f"Worker starting: queues={worker.queues}, concurrency={worker.concurrency}")
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
```

---

## 6. 예시 FastAPI 앱 (프로듀서)

### app/main.py

```python
"""예시 프로듀서 — 웹 UI + REST API."""
from contextlib import asynccontextmanager
import os

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from open_kknaks import ClaudeClient
from open_kknaks.broker import RedisBroker


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.client = ClaudeClient(
        broker=RedisBroker(
            url=os.environ.get("REDIS_URL", "redis://localhost:6379"),
            namespace=os.environ.get("NAMESPACE", "example"),
        ),
    )
    yield


app = FastAPI(title="open_kknaks Example", lifespan=lifespan)
templates = Jinja2Templates(directory="templates")

from routes import submit, status, result, stream  # noqa: E402

app.include_router(submit.router)
app.include_router(status.router)
app.include_router(result.router)
app.include_router(stream.router)


@app.get("/")
async def index(request):
    return templates.TemplateResponse("index.html", {"request": request})
```

### app/routes/submit.py

```python
from fastapi import APIRouter, Request
from pydantic import BaseModel

router = APIRouter()


class SubmitRequest(BaseModel):
    prompt: str
    context: str | None = None
    queue: str = "default"
    priority: str = "normal"


@router.post("/submit")
async def submit_task(req: SubmitRequest, request: Request):
    client = request.app.state.client
    task_id = await client.submit(
        prompt=req.prompt,
        context=req.context,
        queue=req.queue,
        priority=req.priority,
    )
    return {"task_id": task_id}
```

### app/routes/stream.py — SSE 실시간 스트리밍

```python
from fastapi import APIRouter, Request
from sse_starlette.sse import EventSourceResponse

router = APIRouter()


@router.get("/stream/{task_id}")
async def stream_task(task_id: str, request: Request):
    client = request.app.state.client

    async def generate():
        async for event in client.stream(task_id):
            if event.text:
                yield {"event": "text", "data": event.text}
            elif event.type == "retry":
                yield {"event": "retry", "data": str(event.retry_info)}
        yield {"event": "done", "data": ""}

    return EventSourceResponse(generate())
```

### app/routes/result.py — 완성 결과 조회

```python
from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/result/{task_id}")
async def get_result(task_id: str, request: Request):
    client = request.app.state.client
    result = await client.result(task_id, timeout=600)
    return {
        "task_id": result.task_id,
        "status": result.status,
        "result": result.result,
        "usage": result.usage.model_dump() if result.usage else None,
    }
```

### app/routes/status.py

```python
from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/status/{task_id}")
async def get_status(task_id: str, request: Request):
    client = request.app.state.client
    status = await client.status(task_id)
    return {"task_id": task_id, "status": status}
```

---

## 7. 웹 UI

### app/templates/index.html

```html
<!DOCTYPE html>
<html>
<head>
    <title>open_kknaks Example</title>
    <style>
        body { font-family: monospace; max-width: 800px; margin: 40px auto; }
        textarea { width: 100%; height: 80px; }
        #output { background: #1a1a2e; color: #0f0; padding: 16px;
                  min-height: 200px; white-space: pre-wrap; overflow-y: auto; }
        button { padding: 8px 16px; margin: 8px 4px 8px 0; cursor: pointer; }
        .status { color: #888; font-size: 12px; }
    </style>
</head>
<body>
    <h1>open_kknaks Example</h1>

    <textarea id="prompt" placeholder="프롬프트를 입력하세요..."></textarea>
    <br>
    <button onclick="submitStream()">Submit (Streaming)</button>
    <button onclick="submitWait()">Submit (Wait for Result)</button>
    <span id="statusText" class="status"></span>

    <div id="output"></div>

    <script>
    const output = document.getElementById('output');
    const statusText = document.getElementById('statusText');

    async function submitStream() {
        const prompt = document.getElementById('prompt').value;
        output.textContent = '';
        statusText.textContent = 'submitting...';

        const res = await fetch('/submit', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({prompt}),
        });
        const {task_id} = await res.json();
        statusText.textContent = `task: ${task_id} — streaming...`;

        const evtSource = new EventSource(`/stream/${task_id}`);
        evtSource.addEventListener('text', e => {
            output.textContent += e.data;
            output.scrollTop = output.scrollHeight;
        });
        evtSource.addEventListener('retry', e => {
            output.textContent += `\n[RETRY] ${e.data}\n`;
        });
        evtSource.addEventListener('done', () => {
            statusText.textContent = `task: ${task_id} — done`;
            evtSource.close();
        });
        evtSource.onerror = () => {
            statusText.textContent = `task: ${task_id} — connection closed`;
            evtSource.close();
        };
    }

    async function submitWait() {
        const prompt = document.getElementById('prompt').value;
        output.textContent = '';
        statusText.textContent = 'submitting...';

        const res = await fetch('/submit', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({prompt}),
        });
        const {task_id} = await res.json();
        statusText.textContent = `task: ${task_id} — waiting for result...`;

        const resultRes = await fetch(`/result/${task_id}`);
        const result = await resultRes.json();
        output.textContent = result.result || result.error || 'No result';
        statusText.textContent = `task: ${task_id} — ${result.status}`;
    }
    </script>
</body>
</html>
```

---

## 8. 시나리오 스크립트

### scenarios/01_basic.py

```python
"""기본 사용법 — submit → result."""
import asyncio
from open_kknaks import ClaudeClient
from open_kknaks.broker import RedisBroker


async def main():
    client = ClaudeClient(
        broker=RedisBroker(url="redis://localhost:6379", namespace="example"),
    )

    task_id = await client.submit("Python에서 데코레이터가 뭔지 설명해줘")
    print(f"Submitted: {task_id}")

    result = await client.result(task_id, timeout=120)
    print(f"Status: {result.status}")
    print(f"Result:\n{result.result}")
    if result.usage:
        print(f"Tokens: {result.usage.input_tokens} in / {result.usage.output_tokens} out")
        print(f"Cost: ${result.usage.cost_usd:.4f}")


asyncio.run(main())
```

### scenarios/02_streaming.py

```python
"""실시간 스트리밍."""
import asyncio
from open_kknaks import ClaudeClient
from open_kknaks.broker import RedisBroker


async def main():
    client = ClaudeClient(
        broker=RedisBroker(url="redis://localhost:6379", namespace="example"),
    )

    task_id = await client.submit("FastAPI로 간단한 TODO API를 만들어줘")
    print(f"Submitted: {task_id}\n")

    async for event in client.stream(task_id):
        if event.text:
            print(event.text, end="", flush=True)
    print("\n\nDone!")


asyncio.run(main())
```

### scenarios/03_batch.py

```python
"""배치 작업 — 3개 작업 병렬 실행."""
import asyncio
from open_kknaks import ClaudeClient
from open_kknaks.broker import RedisBroker


async def main():
    client = ClaudeClient(
        broker=RedisBroker(url="redis://localhost:6379", namespace="example"),
    )

    batch_id = await client.batch_submit(
        tasks=[
            {"prompt": "Python의 GIL이 뭔지 설명해줘"},
            {"prompt": "asyncio의 이벤트 루프 구조를 설명해줘"},
            {"prompt": "Python에서 멀티프로세싱 vs 멀티스레딩 비교해줘"},
        ],
        mode="parallel",
    )
    print(f"Batch submitted: {batch_id}")

    results = await client.batch_wait(batch_id, timeout=300)
    for r in results:
        print(f"\n{'='*60}")
        print(f"[{r.status}] {r.result[:200]}...")


asyncio.run(main())
```

### scenarios/06_multi_queue.py

```python
"""멀티 큐 — 큐별로 다른 작업 라우팅."""
import asyncio
from open_kknaks import ClaudeClient
from open_kknaks.broker import RedisBroker


async def main():
    client = ClaudeClient(
        broker=RedisBroker(url="redis://localhost:6379", namespace="example"),
    )

    # analysis 큐 → 분석 전용 워커가 처리
    t1 = await client.submit(
        "이 에러 로그를 분석해줘",
        context="TypeError: cannot unpack non-iterable NoneType object",
        queue="analysis",
        priority="high",
    )

    # review 큐 → 리뷰 전용 워커가 처리
    t2 = await client.submit(
        "이 코드를 리뷰해줘",
        context="def foo(x): return x+1",
        queue="review",
    )

    r1 = await client.result(t1, timeout=120)
    r2 = await client.result(t2, timeout=120)

    print(f"Analysis: {r1.result[:200]}")
    print(f"Review: {r2.result[:200]}")


asyncio.run(main())
```

---

## 9. 실행 방법

### 사전 조건

```bash
# 호스트에 Claude Code CLI 설치 + 로그인 (1회만)
claude login
claude auth status   # 로그인 확인
```

> 이미 로컬에서 Claude Code를 쓰고 있다면 추가 작업 없음.

### 한 방 실행

```bash
git clone https://github.com/kknaks/open_kknaks.git
cd open_kknaks/examples

./setup.sh           # Claude CLI 경로 자동 탐색 → .env 생성
docker compose up -d
```

### 접속

```
웹 UI:  http://localhost:8000
API:    http://localhost:8000/docs  (Swagger)
Redis:  localhost:6379
```

### 시나리오 스크립트 실행

```bash
# docker 없이 로컬에서 직접 실행 (Redis만 있으면 됨)
cd examples
pip install -e "../[redis]"

python scenarios/01_basic.py
python scenarios/02_streaming.py
python scenarios/03_batch.py
```

### 종료

```bash
docker compose down -v
```

---

## 10. .env.example

```env
REDIS_URL=redis://redis:6379
NAMESPACE=example
QUEUES=default,analysis,review
CONCURRENCY=2
CLAUDE_DIR=             # setup.sh가 자동 설정
```

---

## 11. PyPI 배포에서 제외

```toml
# pyproject.toml
[tool.hatch.build.targets.sdist]
exclude = ["examples/", "tests/", "docs/"]

[tool.hatch.build.targets.wheel]
exclude = ["examples/", "tests/", "docs/"]
```

```gitignore
# .gitignore — examples 디렉토리는 git에 포함
# (별도 제외 없음)
```

배포 시:
- `pip install open-kknaks` → examples 미포함
- `git clone` → examples 포함, docker compose up으로 즉시 체험
