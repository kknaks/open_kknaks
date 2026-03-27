# Claude Code 터미널 제어 분석

> Claude Code CLI를 프로그래밍 방식으로 열고 명령을 보내는 방법에 대한 종합 분석

---

## 목차

1. [개요](#1-개요)
2. [CLI 기본 사용법 (`-p` 플래그)](#2-cli-기본-사용법--p-플래그)
3. [출력 포맷 제어](#3-출력-포맷-제어)
4. [주요 CLI 플래그 정리](#4-주요-cli-플래그-정리)
5. [실제 구현 사례: app_builder_local](#5-실제-구현-사례-app_builder_local)
6. [실제 구현 사례: persona_counselor](#6-실제-구현-사례-persona_counselor)
7. [Stream Parser 구현](#7-stream-parser-구현)
8. [프로세스 생명주기 관리](#8-프로세스-생명주기-관리)
9. [Agent SDK (Python / TypeScript)](#9-agent-sdk-python--typescript)
10. [PTY vs Subprocess Pipe 비교](#10-pty-vs-subprocess-pipe-비교)
11. [실전 패턴 및 레시피](#11-실전-패턴-및-레시피)
12. [환경 변수](#12-환경-변수)

---

## 1. 개요

Claude Code를 터미널에서 프로그래밍 방식으로 제어하는 핵심 접근법은 **3가지**:

| 접근법 | 설명 | 적합한 용도 |
|--------|------|-------------|
| **CLI `-p` 모드** | 비대화형 모드, 프롬프트를 인자로 전달 | 스크립트, CI/CD, 자동화 |
| **Agent SDK** | Python/TypeScript 라이브러리 | 커스텀 애플리케이션 |
| **PTY 기반** | 대화형 모드를 터미널 에뮬레이터로 제어 | 레거시 통합 (비권장) |

**권장 순서**: CLI `-p` > Agent SDK > PTY

---

## 2. CLI 기본 사용법 (`-p` 플래그)

`-p` (또는 `--print`) 플래그는 Claude Code를 **비대화형(headless)** 모드로 실행하는 핵심 메커니즘.

### 기본 실행

```bash
# 단순 프롬프트 실행
claude -p "auth.py의 버그를 찾아 수정해줘"

# stdin 파이프
cat error.log | claude -p "이 에러를 분석해줘"

# 출력을 파일로 저장
claude -p "코드 분석" --output-format json > result.json
```

### `--bare` 모드 (스크립팅 권장)

```bash
claude --bare -p "프로젝트 요약" --allowedTools "Read"
```

`--bare` 모드의 장점:
- hooks, skills, plugins, MCP 서버 자동 로딩 스킵
- CLAUDE.md, auto memory 로딩 스킵
- 빠른 시작 (설정 로딩 없음)
- 머신 간 예측 가능한 동작
- `ANTHROPIC_API_KEY` 환경변수 필요 (OAuth/키체인 미사용)

### 세션 관리

```bash
# 첫 번째 요청 → 세션 ID 획득
session_id=$(claude -p "인증 모듈 분석" --output-format json | jq -r '.session_id')

# 동일 세션 이어서 실행
claude -p "호출하는 곳을 모두 찾아줘" --resume "$session_id"

# 가장 최근 세션 이어서 실행
claude -p "요약해줘" --continue
```

---

## 3. 출력 포맷 제어

### text (기본값)

```bash
claude -p "요약" --output-format text
# → 일반 텍스트 출력
```

### json

```bash
claude -p "요약" --output-format json
```

```json
{
  "type": "result",
  "result": "분석 결과...",
  "session_id": "abc-123",
  "usage": { "input_tokens": 100, "output_tokens": 50 },
  "cost": 0.00123
}
```

### stream-json (실시간 스트리밍)

```bash
claude -p "설명해줘" \
  --output-format stream-json \
  --verbose \
  --include-partial-messages
```

스트림 이벤트:

```json
{"type":"system","subtype":"init","session_id":"..."}
{"type":"assistant","message":{"content":[{"type":"text","text":"..."}]}}
{"type":"result","result":"최종 결과","cost_usd":0.01,"usage":{"input_tokens":100,"output_tokens":50}}
```

텍스트만 추출:

```bash
claude -p "시 한 편 써줘" \
  --output-format stream-json \
  --verbose \
  --include-partial-messages | \
  jq -rj 'select(.type == "stream_event" and .event.delta.type? == "text_delta") | .event.delta.text'
```

### 구조화 출력 (JSON Schema)

```bash
claude -p "함수 이름 추출" \
  --output-format json \
  --json-schema '{"type":"object","properties":{"functions":{"type":"array","items":{"type":"string"}}},"required":["functions"]}'
```

응답:

```json
{
  "structured_output": {"functions": ["login", "logout", "validate"]},
  "session_id": "...",
  "usage": {...}
}
```

---

## 4. 주요 CLI 플래그 정리

### 출력 제어

| 플래그 | 설명 | 값 |
|--------|------|-----|
| `--output-format` | 응답 포맷 | `text`, `json`, `stream-json` |
| `--json-schema` | 출력 스키마 검증 | JSON Schema |
| `--verbose` | 전체 turn-by-turn 출력 | boolean |
| `--include-partial-messages` | 스트리밍 이벤트 포함 | boolean |

### 도구 및 권한 제어

| 플래그 | 설명 |
|--------|------|
| `--allowedTools` | 특정 도구 사전 승인 |
| `--disallowedTools` | 특정 도구 차단 |
| `--permission-mode` | 권한 모드 (`plan`, `auto`, `dontAsk`, `bypassPermissions`) |
| `--dangerously-skip-permissions` | 모든 권한 확인 스킵 (위험) |

### 도구 승인 세분화

```bash
# 특정 도구만 허용
claude -p "테스트 실행 후 수정" --allowedTools "Bash,Read,Edit"

# git 명령만 허용
claude -p "커밋 생성" \
  --allowedTools "Bash(git diff *),Bash(git log *),Bash(git status *),Bash(git commit *)"
```

### 시스템 프롬프트

| 플래그 | 설명 |
|--------|------|
| `--system-prompt` | 전체 시스템 프롬프트 교체 |
| `--system-prompt-file` | 파일에서 시스템 프롬프트 로드 |
| `--append-system-prompt` | 기본 프롬프트에 추가 |
| `--append-system-prompt-file` | 파일에서 추가 프롬프트 로드 |

### 세션 관리

| 플래그 | 설명 |
|--------|------|
| `--continue` / `-c` | 가장 최근 대화 이어서 |
| `--resume` / `-r` | 특정 세션 ID로 재개 |
| `--session-id` | 세션 UUID 지정 |
| `--no-session-persistence` | 세션 디스크 저장 안 함 |

### 실행 제한

| 플래그 | 설명 |
|--------|------|
| `--max-turns` | 최대 에이전트 턴 수 (print 모드 전용) |
| `--max-budget-usd` | 최대 비용 제한 |
| `--fallback-model` | 모델 과부하 시 폴백 |

---

## 5. 실제 구현 사례: app_builder_local

> 소스: `/Users/kknaks/git/toy_pr2/app_builder_local/backend/app/core/agent_runner.py`

AI 개발팀 플랫폼으로, PM/기획/백엔드/프론트엔드/디자인 5개 에이전트가 협업하여 앱을 생성.

### 아키텍처

```
┌─────────────────────────────────────────────────────┐
│  Frontend (Next.js 15)                              │
│  ┌──────────┬──────────────┬──────────────────┐    │
│  │ Project  │  Dashboard   │  Chat + Logs     │    │
│  │ List     │ (React Flow) │ (Agent Tabs)     │    │
│  └──────────┴──────────────┴──────────────────┘    │
│         WebSocket (chat / logs / flow)              │
├─────────────────────────────────────────────────────┤
│  Backend (FastAPI)                                  │
│  ┌─────────────────────────────────────────┐       │
│  │ AgentProcessManager (싱글턴)            │       │
│  │  ├─ spawn_agent() → asyncio.subprocess  │       │
│  │  ├─ _read_output() → stream-json 파싱   │       │
│  │  ├─ cancel_task() → SIGTERM/SIGKILL     │       │
│  │  └─ cleanup_all() → 앱 종료 시 정리     │       │
│  └─────────────────────────────────────────┘       │
│       ↓ subprocess (pipes, NOT PTY)                 │
│  ┌─────────────────────────────────────────┐       │
│  │ claude --dangerously-skip-permissions   │       │
│  │   -p "{prompt}"                         │       │
│  │   --output-format stream-json           │       │
│  │   --verbose                             │       │
│  │   --append-system-prompt {agent.md}     │       │
│  └─────────────────────────────────────────┘       │
├─────────────────────────────────────────────────────┤
│  PostgreSQL (7 tables)                              │
└─────────────────────────────────────────────────────┘
```

### 핵심 코드: 에이전트 스폰

```python
# agent_runner.py (app_builder_local)

async def spawn_agent(self, agent_md_path, prompt, project_dir,
                      project_id, agent, task_id, timeout=600):
    """Claude Code CLI 프로세스를 스폰하고 출력을 스트리밍."""

    # 1. CLI 명령 구성
    cmd = [
        "claude",
        "--dangerously-skip-permissions",
        "-p", prompt,
        "--output-format", "stream-json",
        "--verbose",
    ]

    # 에이전트별 시스템 프롬프트 추가
    if agent_md_path and os.path.exists(agent_md_path):
        with open(agent_md_path) as f:
            agent_prompt = f.read()
        cmd.extend(["--append-system-prompt", agent_prompt])

    # 2. subprocess 스폰 (Pipe 방식 — PTY가 아님)
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=project_dir,          # 프로젝트 디렉토리에서 실행
    )

    # 3. 프로세스 매니저에 등록
    process = AgentProcess(pid=proc.pid, project_id=project_id,
                           agent=agent, task_id=task_id, _proc=proc)
    self._processes[task_id] = process

    # 4. 출력 스트리밍 (line-by-line)
    try:
        async for line in self._read_output(proc, timeout):
            yield line  # WebSocket으로 브로드캐스트
    finally:
        # 5. 정리: 프로세스가 아직 살아있으면 종료
        if proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
        self._processes.pop(task_id, None)
```

### 출력 읽기 (타임아웃 포함)

```python
async def _read_output(self, proc, timeout):
    """stdout에서 stream-json을 라인별로 읽기."""
    deadline = asyncio.get_event_loop().time() + timeout

    while True:
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            proc.terminate()
            raise TimeoutError(f"Agent process {proc.pid} timed out")

        try:
            line_bytes = await asyncio.wait_for(
                proc.stdout.readline(),
                timeout=min(remaining, 10.0),  # 라인별 10초 타임아웃
            )
        except asyncio.TimeoutError:
            if proc.returncode is not None:
                break
            continue  # 라인 타임아웃은 무시, 전체 타임아웃만 체크

        if not line_bytes:
            break

        line = line_bytes.decode("utf-8", errors="replace")
        text = extract_text(line)
        if text:
            yield text
```

### 5개 에이전트 워크플로우

```
사용자 아이디어 입력
    ↓
[Planner Agent] → PRD.md 생성
    ↓
[BE/FE/Design Agent] → 병렬 리뷰 (Semaphore max=2)
    ↓
사용자 승인/피드백
    ↓
[PM Agent] → Phase.md (스프린트 계획) 생성
    ↓
[BE Agent + FE Agent] → 구현 (실패 시 최대 3회 재시도)
    ↓
Docker Compose 생성 → 앱 실행
```

---

## 6. 실제 구현 사례: persona_counselor

> 소스: `/Users/kknaks/git/toy_pr2/persona_counselor/backend/app/automation/`

Step 기반 자동화 시스템으로, 이슈 → 스텝 분해 → Claude 실행 → git commit 파이프라인.

### 아키텍처

```
Issue → TaskGroup → Steps
                      ↓
              step_executor.execute_step()
                      ↓
         ┌─────────────────────────┐
         │  1. 프롬프트 조립       │
         │  2. run_claude_oneshot() │  ← Claude CLI 실행
         │  3. get_changed_files() │  ← git diff 감지
         │  4. commit_step()       │  ← 자동 커밋
         │  (실패 시 rollback)     │
         └─────────────────────────┘
```

### 핵심 코드: Claude 실행기

```python
# agent_runner.py (persona_counselor)

async def run_claude(prompt, cwd, timeout=600):
    """Claude Code CLI를 실행하고 파싱된 stream-json을 yield."""
    cmd = [
        "claude",
        "--dangerously-skip-permissions",
        "-p", prompt,
        "--output-format", "stream-json",
        "--verbose",
    ]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
    )

    try:
        async for parsed in _read_output(proc, timeout):
            yield parsed  # {"type": "text", "content": ...} 또는 {"type": "cost", ...}
    finally:
        if proc.returncode is None:
            await _kill_process(proc)


async def run_claude_oneshot(prompt, cwd, timeout=600):
    """실행 후 전체 텍스트를 하나의 문자열로 반환."""
    texts = []
    async for parsed in run_claude(prompt, cwd, timeout):
        if parsed.get("type") == "text":
            texts.append(parsed["content"])
    return "\n".join(texts)
```

### Step 실행 파이프라인

```python
# step_executor.py

async def execute_step(step_order, file_path, diff_preview,
                       reason, context_from_prev, cwd):
    """
    1. 프롬프트 조립 (step 정보 + 이전 컨텍스트)
    2. Claude Code 실행
    3. 변경 파일 감지
    4. git commit (성공) 또는 rollback (실패)
    """
    prompt = _build_prompt(step_order, file_path, diff_preview,
                           reason, context_from_prev)

    output = await run_claude_oneshot(prompt=prompt, cwd=cwd)

    changed = await get_changed_files(cwd)
    if not changed:
        return StepResult(success=True, output=output, changed_files=[])

    commit_msg = f"[auto] step {step_order}: {reason or 'automated fix'}"
    commit_hash = await commit_step(cwd, changed, commit_msg)

    return StepResult(success=True, output=output,
                      commit_hash=commit_hash, changed_files=changed)
```

### Step 체이닝 (이전 결과를 다음 step의 컨텍스트로)

```python
def _build_prompt(step_order, file_path, diff_preview, reason, context_from_prev):
    parts = [f"Step {step_order} 실행."]
    if reason:
        parts.append(f"목적: {reason}")
    if file_path:
        parts.append(f"대상 파일: {file_path}")
    if diff_preview:
        parts.append(f"변경 내용:\n{diff_preview}")
    if context_from_prev:
        parts.append(f"이전 step 결과 참고:\n{context_from_prev}")
    parts.append("위 내용대로 코드를 수정하세요. 수정 후 변경 사항을 설명하세요.")
    return "\n\n".join(parts)
```

### 상수 설정

```python
# constants.py
CLAUDE_PROCESS_TIMEOUT = 600       # 전체 타임아웃: 10분
CLAUDE_LINE_TIMEOUT = 10           # 라인별 읽기 타임아웃: 10초
CLAUDE_KILL_GRACE_PERIOD = 5       # SIGTERM 후 대기: 5초
AUTOMATION_MAX_RETRIES = 3         # 최대 재시도 횟수
```

---

## 7. Stream Parser 구현

> 소스: `persona_counselor/backend/app/automation/stream_parser.py`

Claude Code의 `--output-format stream-json` 출력을 파싱하는 공통 모듈.

```python
def parse_stream_json_line(line: str) -> Optional[Dict]:
    """stream-json 한 줄을 파싱.

    Returns:
        {"type": "text", "content": str}           — 텍스트 블록
        {"type": "cost", "cost_usd": float, ...}   — 비용 정보
        None                                        — 무시할 줄
    """
    line = line.strip()
    if not line:
        return None

    obj = json.loads(line)
    msg_type = obj.get("type", "")

    if msg_type == "result":
        # 최종 결과 텍스트
        result_text = obj.get("result", "")
        if isinstance(result_text, str) and result_text.strip():
            return {"type": "text", "content": result_text.strip()}
        # 비용/사용량 정보
        cost_usd = obj.get("cost_usd")
        usage = obj.get("usage", {})
        if cost_usd is not None or usage:
            return {
                "type": "cost",
                "cost_usd": cost_usd,
                "input_tokens": usage.get("input_tokens", 0),
                "output_tokens": usage.get("output_tokens", 0),
                "duration_ms": obj.get("duration_ms"),
            }

    elif msg_type == "assistant":
        # 어시스턴트 메시지 (중간 출력)
        content = obj.get("message", {}).get("content", [])
        texts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                texts.append(block["text"])
            elif isinstance(block, str):
                texts.append(block)
        if texts:
            return {"type": "text", "content": "\n".join(texts)}

    return None
```

### stream-json 출력 형식 예시

```json
{"type":"system","subtype":"init","session_id":"abc-123"}
{"type":"assistant","message":{"content":[{"type":"text","text":"분석 시작..."}]}}
{"type":"assistant","message":{"content":[{"type":"text","text":"버그를 발견했습니다."}]}}
{"type":"result","result":"수정 완료","cost_usd":0.015,"usage":{"input_tokens":500,"output_tokens":200},"duration_ms":8500}
```

---

## 8. 프로세스 생명주기 관리

두 프로젝트 모두 동일한 **Graceful Shutdown** 패턴을 사용:

```
프로세스 실행 중
    ↓
타임아웃 또는 취소 요청
    ↓
SIGTERM 전송 (정상 종료 요청)
    ↓
5초 대기 (KILL_GRACE_PERIOD)
    ↓
아직 살아있으면 → SIGKILL (강제 종료)
    ↓
proc.wait() (좀비 프로세스 방지)
```

### app_builder_local의 프로세스 매니저

```python
class AgentProcessManager:
    """싱글턴: 모든 에이전트 프로세스를 추적하고 관리."""

    def __init__(self):
        self._processes: dict[int, AgentProcess] = {}  # task_id → AgentProcess
        self._lock = asyncio.Lock()

    async def cancel_task(self, task_id):
        """특정 태스크 취소."""
        process = self._processes.get(task_id)
        if process:
            await process.terminate()  # SIGTERM → SIGKILL
            self._processes.pop(task_id, None)

    async def cancel_project(self, project_id):
        """프로젝트의 모든 태스크 취소."""
        processes = [p for p in self._processes.values()
                     if p.project_id == project_id]
        for proc in processes:
            await proc.terminate()

    async def cleanup_all(self):
        """앱 종료 시 모든 프로세스 정리 (lifespan hook)."""
        for proc in self._processes.values():
            await proc.terminate()
        self._processes.clear()

# 전역 인스턴스
process_manager = AgentProcessManager()
```

### persona_counselor의 프로세스 매니저

```python
class ProcessManager:
    """asyncio.Task 기반 프로세스 추적."""

    def __init__(self):
        self._tasks: Dict[int, asyncio.Task] = {}  # step_id → Task
        self._lock = asyncio.Lock()

    async def register(self, step_id, task):
        async with self._lock:
            self._tasks[step_id] = task

    async def cancel(self, step_id):
        async with self._lock:
            task = self._tasks.pop(step_id, None)
            if task:
                task.cancel()

    async def cleanup_all(self):
        async with self._lock:
            for task in self._tasks.values():
                task.cancel()
            self._tasks.clear()
```

---

## 9. Agent SDK (Python / TypeScript)

CLI `-p` 대신 프로그래밍 언어에서 직접 Claude Code를 제어하는 공식 SDK.

### Python SDK

```bash
pip install claude-agent-sdk
```

```python
import asyncio
from claude_agent_sdk import query, ClaudeAgentOptions

async def main():
    async for message in query(
        prompt="auth.py의 버그를 찾아 수정해줘",
        options=ClaudeAgentOptions(
            allowed_tools=["Read", "Edit", "Bash"],
            cwd="/path/to/project"
        ),
    ):
        print(message)

asyncio.run(main())
```

### TypeScript SDK

```bash
npm install @anthropic-ai/claude-agent-sdk
```

```typescript
import { query } from "@anthropic-ai/claude-agent-sdk";

for await (const message of query({
  prompt: "auth.py의 버그를 찾아 수정해줘",
  options: {
    allowedTools: ["Read", "Edit", "Bash"],
    cwd: "/path/to/project"
  }
})) {
  console.log(message);
}
```

### SDK vs CLI 비교

| 기능 | CLI `-p` | Agent SDK |
|------|----------|-----------|
| 스트리밍 콜백 | 제한적 | 완전 지원 |
| 도구 승인 콜백 | 불가 | 가능 |
| 메시지 객체 접근 | 불가 | 가능 |
| 커스텀 훅 | 제한적 | 완전 지원 |
| 서브에이전트 제어 | 불가 | 가능 |
| MCP 통합 | 가능 | 가능 |
| 세션 관리 | 가능 | 가능 |
| 구조화 출력 | 가능 | 가능 |

---

## 10. PTY vs Subprocess Pipe 비교

### 왜 두 프로젝트 모두 Pipe를 선택했는가

| 항목 | PTY | Subprocess Pipe |
|------|-----|-----------------|
| 출력 형식 | ANSI escape 코드 포함 | 깔끔한 JSON |
| 파싱 난이도 | 높음 (터미널 제어 문자 제거 필요) | 낮음 (줄별 JSON 파싱) |
| 안정성 | 터미널 크기, 인코딩 등 변수 많음 | 예측 가능 |
| 성능 | 오버헤드 있음 | 직접 파이프 |
| 구현 복잡도 | `expect`/`pexpect` 등 필요 | `asyncio.create_subprocess_exec` |
| Claude Code 호환 | 대화형 모드용 | `-p` + `stream-json` 설계 목적 |

### PTY를 써야 하는 경우 (비권장)

레거시 시스템 통합 등 불가피한 경우:

```python
import pexpect

child = pexpect.spawn("claude")
child.expect("Claude Code")
child.sendline("analyze this code")
child.expect(pexpect.EOF)
output = child.before.decode()
```

**결론**: Claude Code의 `-p` + `--output-format stream-json`이 프로그래밍 제어를 위해 설계된 공식 방법. PTY는 안티패턴.

---

## 11. 실전 패턴 및 레시피

### CI/CD 파이프라인

```bash
#!/bin/bash
claude --bare -p "스테이징된 변경사항 보안 리뷰" \
  --allowedTools "Read,Bash(git diff *)" \
  --output-format json | jq -r '.result'
```

### 멀티턴 워크플로우

```bash
#!/bin/bash
session=$(claude -p "인증 모듈 분석" --output-format json | jq -r '.session_id')
claude -p "호출 위치 모두 찾기" --resume "$session" --output-format json | jq -r '.result'
claude -p "요약 생성" --resume "$session" --output-format json | jq -r '.result'
```

### Python asyncio 통합 (두 프로젝트의 공통 패턴)

```python
import asyncio

async def run_claude_agent(prompt: str, cwd: str) -> str:
    cmd = [
        "claude", "--dangerously-skip-permissions",
        "-p", prompt,
        "--output-format", "stream-json",
        "--verbose",
    ]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
    )

    texts = []
    deadline = asyncio.get_event_loop().time() + 600

    while True:
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            proc.terminate()
            break

        try:
            line = await asyncio.wait_for(proc.stdout.readline(), timeout=10)
        except asyncio.TimeoutError:
            if proc.returncode is not None:
                break
            continue

        if not line:
            break

        parsed = parse_stream_json_line(line.decode())
        if parsed and parsed["type"] == "text":
            texts.append(parsed["content"])

    return "\n".join(texts)
```

### WebSocket 실시간 스트리밍 (app_builder_local 패턴)

```python
from fastapi import WebSocket

async def stream_to_websocket(ws: WebSocket, prompt: str, cwd: str):
    async for line in process_manager.spawn_agent(
        agent_md_path="agent.md",
        prompt=prompt,
        project_dir=cwd,
        project_id=1,
        agent="backend",
        task_id=42,
    ):
        await ws.send_json({"type": "text", "content": line})
```

### Git 자동 커밋 통합 (persona_counselor 패턴)

```python
async def automated_code_change(prompt: str, cwd: str):
    # 1. Claude 실행
    output = await run_claude_oneshot(prompt, cwd)

    # 2. 변경 파일 감지
    changed = await get_changed_files(cwd)  # git diff --name-only

    # 3. 자동 커밋
    if changed:
        hash = await commit_step(cwd, changed, f"[auto] {prompt[:50]}")
        return {"output": output, "commit": hash, "files": changed}

    return {"output": output, "files": []}
```

---

## 12. 환경 변수

```bash
# 인증
export ANTHROPIC_API_KEY=your-api-key

# 클라우드 제공자
export CLAUDE_CODE_USE_BEDROCK=1    # AWS Bedrock
export CLAUDE_CODE_USE_VERTEX=1     # Google Vertex AI

# 동작 제어
export CLAUDE_CODE_SIMPLE=1         # bare 모드 동등
export MAX_THINKING_TOKENS=10000
export CLAUDE_CODE_EFFORT_LEVEL=high

# 설정 파일 경로
# ~/.claude/settings.json           — 사용자 수준
# .claude/settings.json             — 프로젝트 수준
# .claude/settings.local.json       — 로컬 (gitignore)
# .mcp.json                         — MCP 서버 설정
```

---

## 요약: 두 프로젝트의 공통 패턴

| 패턴 | app_builder_local | persona_counselor |
|------|-------------------|-------------------|
| **CLI 명령** | `claude --dangerously-skip-permissions -p {prompt} --output-format stream-json --verbose` | 동일 |
| **프로세스 생성** | `asyncio.create_subprocess_exec` (Pipe) | 동일 |
| **출력 파싱** | `extract_text()` → 텍스트만 | `parse_stream_json_line()` → text + cost |
| **타임아웃** | 전체 600s + 라인 10s | 동일 |
| **종료** | SIGTERM → 5s → SIGKILL | 동일 |
| **에이전트 역할** | 5개 (PM/Planner/BE/FE/Design) | Step 단위 단일 실행 |
| **실시간 전달** | WebSocket 브로드캐스트 | Task 브로커 기반 |
| **결과 관리** | DB 저장 (PostgreSQL) | git commit + DB |
| **에러 처리** | 재시도 (최대 3회) + 사용자 에스컬레이션 | rollback + 재시도 |
