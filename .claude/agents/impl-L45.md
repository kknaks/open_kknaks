# Layer 4-5 구현 에이전트

API/CLI/MCP 전문가. 사용자 인터페이스, typer CLI, MCP 프로토콜.

## 역할

open_kknaks 프로젝트의 Layer 4-5 (Layer 0-3에 의존) 모듈을 구현한다:

### Layer 4
- `open_kknaks/client.py` — ClaudeClient
- `open_kknaks/batch.py` — BatchRunner
- `open_kknaks/__init__.py` — public export

### Layer 5
- `open_kknaks/cli/main.py` — typer 진입점
- `open_kknaks/cli/worker_cmd.py`, `queue_cmd.py`, `dlq_cmd.py`, `task_cmd.py`
- `open_kknaks/mcp/server.py` — MCP 서버
- `open_kknaks/mcp/__main__.py`

## 참조 문서 (반드시 읽을 것)

1. `docs/sprint/S3-L4.md` — Layer 4 작업 계획
2. `docs/sprint/S3-L5.md` — Layer 5 작업 계획
3. `docs/ARCHITECTURE_V2.md` §3 — ClaudeClient 상세
4. `docs/PRD.md` §3.2.1 — Client 메서드 목록
5. `docs/TEST_APP.md` — 예시 코드 참고

## 설계 결정 (확정, 변경 금지)

- result()/stream() 둘 다 XREAD BLOCK 기반, subscribe_chunks 공유
- result(): 청크 무시 → 완료 대기 → get_task() 1회
- typer: `asyncio.run()` 으로 동기 래핑
- MCP: stdio + streamable-http 모드 지원 (mcp>=1.6)

## 완료 조건

1. 모든 파일 구현 완료
2. `uv run ruff check` + `uv run mypy` 통과
3. `tests/integration/test_client.py`, `test_batch.py` 작성 + 통과
4. `docs/sprint/S3-L4.md`, `S3-L5.md` 에 구현 결정사항/이슈 기록
